// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Pramãna Guardian
/// @notice Per-agent policy enforcer. Stores guard rules as versioned hashes.
///         Full rule JSON lives off-chain (SQLite); only keccak256(rule_json) stored here.
///         Uses standard delegatecall upgrade pattern (EIP-7702 stub for future).
contract Guardian {
    // ─── Structs ──────────────────────────────────────────────────────────────

    struct Policy {
        bytes32 ruleHash;       // keccak256(JSON policy string)
        uint256 version;
        uint256 updatedAt;
        bool    active;
    }

    struct GuardResult {
        uint256 agentId;
        bool    blocked;
        uint8   matchedRule;    // rule index that triggered block (0 = none)
        bytes32 txHash;         // keccak256 of transaction data
        uint256 timestamp;
    }

    // ─── State ────────────────────────────────────────────────────────────────

    address public orchestrator;
    address public admin;

    // agentId → latest policy
    mapping(uint256 => Policy)   public policies;
    // agentId → version → ruleHash (for rollback)
    mapping(uint256 => mapping(uint256 => bytes32)) public policyHistory;
    // agentId → policy version count
    mapping(uint256 => uint256)  public policyVersions;

    // ─── Events ───────────────────────────────────────────────────────────────

    event PolicyUpdated(
        uint256 indexed agentId,
        bytes32 ruleHash,
        uint256 version
    );

    event PolicyRolledBack(
        uint256 indexed agentId,
        uint256 toVersion
    );

    event TxBlocked(
        uint256 indexed agentId,
        bytes32 txHash,
        uint8   matchedRule,
        uint256 timestamp
    );

    event TxApproved(
        uint256 indexed agentId,
        bytes32 txHash,
        uint256 timestamp
    );

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyAdmin() {
        require(msg.sender == admin, "Guardian: not admin");
        _;
    }

    modifier onlyOrchestrator() {
        require(msg.sender == orchestrator, "Guardian: not orchestrator");
        _;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────

    constructor(address _orchestrator) {
        require(_orchestrator != address(0), "Guardian: zero orchestrator");
        orchestrator = _orchestrator;
        admin = msg.sender;
    }

    // ─── Policy Management ────────────────────────────────────────────────────

    /// @notice Store a versioned policy hash for an agent.
    ///         Off-chain service calls this when dashboard updates a policy.
    function setPolicy(uint256 agentId, bytes32 ruleHash) external onlyAdmin {
        require(agentId > 0, "Guardian: invalid agent");

        uint256 newVersion = policyVersions[agentId] + 1;
        policyHistory[agentId][newVersion] = ruleHash;
        policyVersions[agentId] = newVersion;

        policies[agentId] = Policy({
            ruleHash:  ruleHash,
            version:   newVersion,
            updatedAt: block.timestamp,
            active:    true
        });

        emit PolicyUpdated(agentId, ruleHash, newVersion);
    }

    /// @notice Rollback to a previous policy version by version number.
    function rollbackPolicy(uint256 agentId, uint256 targetVersion) external onlyAdmin {
        require(targetVersion > 0 && targetVersion < policyVersions[agentId], "Guardian: invalid version");
        bytes32 oldHash = policyHistory[agentId][targetVersion];
        require(oldHash != bytes32(0), "Guardian: version not found");

        policies[agentId] = Policy({
            ruleHash:  oldHash,
            version:   targetVersion,
            updatedAt: block.timestamp,
            active:    true
        });

        emit PolicyRolledBack(agentId, targetVersion);
    }

    // ─── Guard Recording ─────────────────────────────────────────────────────

    /// @notice Record a blocked transaction. Called by off-chain Guard agent.
    ///         Actual blocking is done off-chain — this is the audit trail.
    function recordBlock(
        uint256 agentId,
        bytes32 txHash,
        uint8   matchedRule
    ) external onlyAdmin {
        emit TxBlocked(agentId, txHash, matchedRule, block.timestamp);
    }

    /// @notice Record an approved transaction for auditability.
    function recordApproval(uint256 agentId, bytes32 txHash) external onlyAdmin {
        emit TxApproved(agentId, txHash, block.timestamp);
    }

    // ─── Views ────────────────────────────────────────────────────────────────

    function getPolicy(uint256 agentId) external view returns (Policy memory) {
        return policies[agentId];
    }

    function getPolicyHash(uint256 agentId, uint256 version)
        external
        view
        returns (bytes32)
    {
        return policyHistory[agentId][version];
    }

    function hasActivePolicy(uint256 agentId) external view returns (bool) {
        return policies[agentId].active;
    }

    // ─── Admin ────────────────────────────────────────────────────────────────

    function updateOrchestrator(address newOrchestrator) external onlyAdmin {
        require(newOrchestrator != address(0), "Guardian: zero address");
        orchestrator = newOrchestrator;
    }

    function transferAdmin(address newAdmin) external onlyAdmin {
        require(newAdmin != address(0), "Guardian: zero address");
        admin = newAdmin;
    }

    // ─── ZK Stub (Phase 2) ───────────────────────────────────────────────────
    // TODO Phase 2: Replace ruleHash verification with Circom-based ZK proof.
    // Stub: function verifyZKProof(bytes calldata proof, bytes32 publicInputHash)
    //       external view returns (bool) { revert("ZK: not implemented"); }
}
