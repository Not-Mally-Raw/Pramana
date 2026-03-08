// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Pramãna Orchestrator Registry
/// @notice Register, pause, and trace on-chain AI agents with multi-org support.
///         Traces are stored off-chain (SQLite/Chroma); only 32-byte hashes land here.
///         Gas design: events only — no mapping storage for trace data.
contract OrchestratorRegistry {
    // ─── Structs ──────────────────────────────────────────────────────────────

    struct AgentRecord {
        address owner;
        bytes32 orgId;       // keccak256 of org identifier
        string  did;         // e.g. "did:pramana:base:0x..."
        uint256 registeredAt;
        bool    paused;
        uint8   riskTier;    // 0=low 1=medium 2=high
    }

    // ─── State ────────────────────────────────────────────────────────────────

    address public admin;
    uint256 public agentCount;

    // agentId → record
    mapping(uint256 => AgentRecord) private _agents;
    // address → agentId (1-indexed, 0 = unregistered)
    mapping(address => uint256) public agentIndex;
    // orgId → list of agentIds
    mapping(bytes32 => uint256[]) public orgAgents;

    // ─── Events ───────────────────────────────────────────────────────────────

    event AgentRegistered(
        uint256 indexed agentId,
        address indexed agentAddr,
        bytes32 indexed orgId,
        string did
    );

    event AgentPaused(uint256 indexed agentId, address indexed by, string reason);
    event AgentResumed(uint256 indexed agentId, address indexed by);

    /// @notice Emitted for every completed monitoring flow execution.
    ///         traceHash = SHA-256(TraceDAG adjacency list JSON), 32 bytes.
    event TraceFinalized(
        uint256 indexed agentId,
        bytes32           traceHash,
        uint256           blockTimestamp
    );

    /// @notice Emitted when a Council decision is recorded on-chain.
    event CouncilDecision(
        uint256 indexed agentId,
        bool    approved,
        uint8   riskScore,    // 0-100
        bytes32 reasonHash    // SHA-256 of reason string
    );

    event RiskTierUpdated(uint256 indexed agentId, uint8 oldTier, uint8 newTier);
    event AdminTransferred(address indexed oldAdmin, address indexed newAdmin);

    // ─── Modifiers ────────────────────────────────────────────────────────────

    modifier onlyAdmin() {
        require(msg.sender == admin, "Orchestrator: not admin");
        _;
    }

    modifier onlyOwnerOrAdmin(uint256 agentId) {
        AgentRecord storage rec = _agents[agentId];
        require(
            msg.sender == rec.owner || msg.sender == admin,
            "Orchestrator: not owner or admin"
        );
        _;
    }

    modifier agentExists(uint256 agentId) {
        require(agentId > 0 && agentId <= agentCount, "Orchestrator: agent not found");
        _;
    }

    // ─── Constructor ──────────────────────────────────────────────────────────

    constructor() {
        admin = msg.sender;
    }

    // ─── Registration ─────────────────────────────────────────────────────────

    /// @notice Register an agent. One address → one agentId.
    function registerAgent(
        address agentAddr,
        string  calldata orgIdStr,
        string  calldata did
    ) external returns (uint256 agentId) {
        require(agentAddr != address(0), "Orchestrator: zero address");
        require(agentIndex[agentAddr] == 0, "Orchestrator: already registered");
        require(bytes(did).length > 0, "Orchestrator: empty DID");

        agentCount++;
        agentId = agentCount;

        bytes32 orgId = keccak256(abi.encodePacked(orgIdStr));

        _agents[agentId] = AgentRecord({
            owner:        agentAddr,
            orgId:        orgId,
            did:          did,
            registeredAt: block.timestamp,
            paused:       false,
            riskTier:     0
        });

        agentIndex[agentAddr] = agentId;
        orgAgents[orgId].push(agentId);

        emit AgentRegistered(agentId, agentAddr, orgId, did);
    }

    // ─── Pause / Resume ───────────────────────────────────────────────────────

    function pauseAgent(uint256 agentId, string calldata reason)
        external
        agentExists(agentId)
        onlyOwnerOrAdmin(agentId)
    {
        _agents[agentId].paused = true;
        emit AgentPaused(agentId, msg.sender, reason);
    }

    function resumeAgent(uint256 agentId)
        external
        agentExists(agentId)
        onlyOwnerOrAdmin(agentId)
    {
        _agents[agentId].paused = false;
        emit AgentResumed(agentId, msg.sender);
    }

    /// @notice Pause all agents in an org — used by "Pause All" dashboard button.
    function pauseAllInOrg(string calldata orgIdStr, string calldata reason)
        external
        onlyAdmin
    {
        bytes32 orgId = keccak256(abi.encodePacked(orgIdStr));
        uint256[] storage ids = orgAgents[orgId];
        for (uint256 i = 0; i < ids.length; i++) {
            if (!_agents[ids[i]].paused) {
                _agents[ids[i]].paused = true;
                emit AgentPaused(ids[i], msg.sender, reason);
            }
        }
    }

    // ─── Trace Finalization ───────────────────────────────────────────────────

    /// @notice Record the SHA-256 hash of a completed TraceDAG on-chain.
    ///         Called by off-chain core service after each flow execution.
    ///         Cost: ~3K gas (event emit only, no storage write).
    function finalizeTrace(uint256 agentId, bytes32 traceHash)
        external
        agentExists(agentId)
        onlyOwnerOrAdmin(agentId)
    {
        require(!_agents[agentId].paused, "Orchestrator: agent paused");
        emit TraceFinalized(agentId, traceHash, block.timestamp);
    }

    // ─── Council Decision ─────────────────────────────────────────────────────

    function recordCouncilDecision(
        uint256 agentId,
        bool    approved,
        uint8   riskScore,
        string  calldata reason
    )
        external
        agentExists(agentId)
        onlyAdmin
    {
        bytes32 reasonHash = keccak256(abi.encodePacked(reason));
        emit CouncilDecision(agentId, approved, riskScore, reasonHash);

        // Auto-pause if Council rejects with high risk
        if (!approved && riskScore >= 70) {
            _agents[agentId].paused = true;
            emit AgentPaused(agentId, msg.sender, "Council: high risk rejection");
        }
    }

    // ─── Risk Tier ────────────────────────────────────────────────────────────

    function updateRiskTier(uint256 agentId, uint8 newTier)
        external
        agentExists(agentId)
        onlyAdmin
    {
        require(newTier <= 2, "Orchestrator: invalid tier");
        uint8 oldTier = _agents[agentId].riskTier;
        _agents[agentId].riskTier = newTier;
        emit RiskTierUpdated(agentId, oldTier, newTier);
    }

    // ─── Views ────────────────────────────────────────────────────────────────

    function getAgent(uint256 agentId)
        external
        view
        agentExists(agentId)
        returns (AgentRecord memory)
    {
        return _agents[agentId];
    }

    function getOrgAgents(string calldata orgIdStr)
        external
        view
        returns (uint256[] memory)
    {
        return orgAgents[keccak256(abi.encodePacked(orgIdStr))];
    }

    function isRegistered(address agentAddr) external view returns (bool) {
        return agentIndex[agentAddr] != 0;
    }

    function isPaused(uint256 agentId) external view agentExists(agentId) returns (bool) {
        return _agents[agentId].paused;
    }

    // ─── Admin ────────────────────────────────────────────────────────────────

    function transferAdmin(address newAdmin) external onlyAdmin {
        require(newAdmin != address(0), "Orchestrator: zero address");
        emit AdminTransferred(admin, newAdmin);
        admin = newAdmin;
    }
}
