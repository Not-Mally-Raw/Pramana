// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/security/ReentrancyGuard.sol";

/// @title Pramãna Reputation SBT (Soulbound Token)
/// @notice Non-transferable ERC-721-like token tracking agent reputation scores.
///         Scores (success_rate, roi, uptime, halluc_rate) are stored as packed uint16s
///         to minimize gas. One SBT per registered agent address.
///         Batch updates via multicall pattern to minimize tx count.
contract ReputationSBT is ReentrancyGuard {
    // ─── Structs ──────────────────────────────────────────────────────────────

    struct ReputationScore {
        uint16 successRate;  // 0-1000 (divide by 10 for %)
        uint16 roiScore;     // 0-1000
        uint16 uptimeScore;  // 0-1000
        uint16 hallucRate;   // 0-1000 (lower is better)
        uint16 composite;    // weighted aggregate 0-1000
        uint256 updatedAt;
    }

    // ─── State ────────────────────────────────────────────────────────────────

    address public admin;
    address public orchestrator;
    uint256 public tokenCount;

    // tokenId → owner (soulbound: never changes after mint)
    mapping(uint256 => address) private _owners;
    // owner → tokenId (0 = no SBT)
    mapping(address => uint256) public tokenOf;
    // tokenId → reputation
    mapping(uint256 => ReputationScore) public scores;
    // tokenId → metadata URI (IPFS/Arweave, set once)
    mapping(uint256 => string) private _tokenURI;

    // ─── Events ───────────────────────────────────────────────────────────────

    event Minted(uint256 indexed tokenId, address indexed owner, string uri);
    event ScoreUpdated(
        uint256 indexed tokenId,
        uint16  successRate,
        uint16  roiScore,
        uint16  uptimeScore,
        uint16  hallucRate,
        uint16  composite
    );

    // ERC-721 transfer attempt blocker
    event TransferBlocked(address indexed from, address indexed to, uint256 indexed tokenId);

    // ─── Errors ───────────────────────────────────────────────────────────────

    error Soulbound();
    error AlreadyMinted();
    error NotFound();

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyAdmin() {
        require(msg.sender == admin || msg.sender == orchestrator, "SBT: not admin");
        _;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────

    constructor(address _orchestrator) {
        require(_orchestrator != address(0), "SBT: zero orchestrator");
        admin = msg.sender;
        orchestrator = _orchestrator;
    }

    // ─── Mint ─────────────────────────────────────────────────────────────────

    /// @notice Mint SBT for a newly registered agent. Called once per agent.
    function mint(address agentOwner, string calldata uri)
        external
        onlyAdmin
        returns (uint256 tokenId)
    {
        if (tokenOf[agentOwner] != 0) revert AlreadyMinted();
        require(agentOwner != address(0), "SBT: zero address");

        tokenCount++;
        tokenId = tokenCount;

        _owners[tokenId]     = agentOwner;
        tokenOf[agentOwner]  = tokenId;
        _tokenURI[tokenId]   = uri;

        // Initialize with zero scores
        scores[tokenId] = ReputationScore(0, 0, 0, 0, 0, block.timestamp);

        emit Minted(tokenId, agentOwner, uri);
    }

    // ─── Score Update ─────────────────────────────────────────────────────────

    /// @notice Update reputation score for a single agent.
    ///         Called daily by the Reputation Agent via batched multicall.
    function updateScore(
        uint256 tokenId,
        uint16  successRate,
        uint16  roiScore,
        uint16  uptimeScore,
        uint16  hallucRate
    ) external onlyAdmin {
        if (_owners[tokenId] == address(0)) revert NotFound();
        _validateScores(successRate, roiScore, uptimeScore, hallucRate);

        // Composite: 40% success, 20% roi, 25% uptime, 15% inverse(halluc)
        uint16 composite = uint16(
            (uint32(successRate) * 40 +
             uint32(roiScore)    * 20 +
             uint32(uptimeScore) * 25 +
             uint32(1000 - hallucRate) * 15) / 100
        );

        scores[tokenId] = ReputationScore({
            successRate: successRate,
            roiScore:    roiScore,
            uptimeScore: uptimeScore,
            hallucRate:  hallucRate,
            composite:   composite,
            updatedAt:   block.timestamp
        });

        emit ScoreUpdated(tokenId, successRate, roiScore, uptimeScore, hallucRate, composite);
    }

    /// @notice Batch update multiple agents in one tx.
    ///         Gas cost: ~5K gas * N agents. For N=50: ~250K gas, well within block limit.
    function batchUpdateScores(
        uint256[] calldata tokenIds,
        uint16[]  calldata successRates,
        uint16[]  calldata roiScores,
        uint16[]  calldata uptimeScores,
        uint16[]  calldata hallucRates
    ) external onlyAdmin nonReentrant {
        uint256 len = tokenIds.length;
        require(
            successRates.length == len &&
            roiScores.length    == len &&
            uptimeScores.length == len &&
            hallucRates.length  == len,
            "SBT: array length mismatch"
        );

        for (uint256 i = 0; i < len; i++) {
            uint256 tid = tokenIds[i];
            if (_owners[tid] == address(0)) continue; // skip invalid
            _validateScores(successRates[i], roiScores[i], uptimeScores[i], hallucRates[i]);

            uint16 composite = uint16(
                (uint32(successRates[i]) * 40 +
                 uint32(roiScores[i])    * 20 +
                 uint32(uptimeScores[i]) * 25 +
                 uint32(1000 - hallucRates[i]) * 15) / 100
            );

            scores[tid] = ReputationScore({
                successRate: successRates[i],
                roiScore:    roiScores[i],
                uptimeScore: uptimeScores[i],
                hallucRate:  hallucRates[i],
                composite:   composite,
                updatedAt:   block.timestamp
            });

            emit ScoreUpdated(tid, successRates[i], roiScores[i], uptimeScores[i], hallucRates[i], composite);
        }
    }

    // ─── Soulbound Enforcement ────────────────────────────────────────────────

    /// @notice Reverts any transfer attempt — tokens are soulbound.
    function transferFrom(address, address, uint256) external pure {
        revert Soulbound();
    }

    function safeTransferFrom(address, address, uint256) external pure {
        revert Soulbound();
    }

    function safeTransferFrom(address, address, uint256, bytes calldata) external pure {
        revert Soulbound();
    }

    // ─── Views ────────────────────────────────────────────────────────────────

    function ownerOf(uint256 tokenId) external view returns (address) {
        address owner = _owners[tokenId];
        if (owner == address(0)) revert NotFound();
        return owner;
    }

    function tokenURI(uint256 tokenId) external view returns (string memory) {
        if (_owners[tokenId] == address(0)) revert NotFound();
        return _tokenURI[tokenId];
    }

    function getScore(uint256 tokenId) external view returns (ReputationScore memory) {
        if (_owners[tokenId] == address(0)) revert NotFound();
        return scores[tokenId];
    }

    function getComposite(address agentOwner) external view returns (uint16) {
        uint256 tid = tokenOf[agentOwner];
        if (tid == 0) return 0;
        return scores[tid].composite;
    }

    // ─── Internal ─────────────────────────────────────────────────────────────

    function _validateScores(
        uint16 s, uint16 r, uint16 u, uint16 h
    ) internal pure {
        require(s <= 1000 && r <= 1000 && u <= 1000 && h <= 1000, "SBT: score out of range");
    }

    // ─── Admin ────────────────────────────────────────────────────────────────

    function transferAdmin(address newAdmin) external {
        require(msg.sender == admin, "SBT: not admin");
        require(newAdmin != address(0), "SBT: zero address");
        admin = newAdmin;
    }
}
