"""
utils/models.py — Pydantic models shared across the entire Pramãna system.
Single source of truth for all data structures.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ─── Enums ────────────────────────────────────────────────────────────────────


class RiskTier(int, Enum):
    LOW = 0
    MEDIUM = 1
    HIGH = 2


class FlowAction(str, Enum):
    PASS = "pass"
    ESCALATE = "escalate"
    PAUSE = "pause"
    EMERGENCY = "emergency"


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class DegradedMode(str, Enum):
    NORMAL = "normal"
    NO_LLM = "no_llm"  # Groq down
    NO_CHAIN = "no_chain"  # Alchemy down → Anvil only
    NO_VECTORS = "no_vectors"  # Chroma down


class AgentProfile(str, Enum):
    """Agent behavior profile for threshold tuning."""

    TREASURY = "treasury_bot"
    KEEPER = "keeper_bot"
    ARB = "arb_bot"
    REBALANCER = "rebalancer"
    GENERIC = "generic"


# ─── ERC-4337 Account Abstraction ─────────────────────────────────────────────

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserOperation(BaseModel):
    """ERC-4337 UserOperation schema supporting both v0.6 and v0.7 structs."""

    version: Literal["v0.6", "v0.7"] = "v0.6"
    sender: str
    nonce: int
    callData: str
    callGasLimit: int
    verificationGasLimit: int
    preVerificationGas: int
    maxFeePerGas: int
    maxPriorityFeePerGas: int
    signature: str

    # v0.6 specific
    initCode: Optional[str] = None
    paymasterAndData: Optional[str] = None

    # v0.7 specific
    factory: Optional[str] = None
    factoryData: Optional[str] = None
    paymaster: Optional[str] = None
    paymasterVerificationGasLimit: Optional[int] = None
    paymasterPostOpGasLimit: Optional[int] = None
    paymasterData: Optional[str] = None

    # Computed fields (extracted from 256-bit nonce)
    nonce_key: int = 0
    nonce_sequence: int = 0

    @model_validator(mode="after")
    def split_nonce(self) -> "UserOperation":
        # Nonce is 256-bit: upper 192 bits are nonce_key (lane), lower 64 bits are sequence
        self.nonce_key = self.nonce >> 64
        self.nonce_sequence = self.nonce & ((1 << 64) - 1)
        return self


# ─── Core Event ───────────────────────────────────────────────────────────────


class ChainEvent(BaseModel):
    """Raw event from WebSocket / Anvil listener or UserOperation adapter."""

    agent_id: str  # Ethereum address or identifier
    org_id: str
    tx_hash: str
    action: str  # e.g. "swap", "transfer", "call", "userop"
    calldata: str  # hex-encoded
    value_wei: int = 0
    gas_price: int = 0
    timestamp: float = Field(default_factory=time.time)
    network: str = "anvil"  # anvil | sepolia | base
    bundler_id: Optional[str] = None
    user_operation: Optional[UserOperation] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── L1 Agent Results ─────────────────────────────────────────────────────────


class L1Result(BaseModel):
    """Output from a single L1 monitoring agent."""

    agent_name: str
    flagged: bool
    score: float  # 0.0–1.0
    reason: str = ""
    latency_ms: float = 0.0
    pass_count: int = 1  # 1 = first pass, 2 = second pass (two-pass verify)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ─── Council ──────────────────────────────────────────────────────────────────


class CouncilVote(BaseModel):
    """Single vote from one Groq LLM call (Council Agent)."""

    judge_id: str = ""
    approved: bool = False
    risk_score: int = 50  # 0-100
    confidence: float = 0.5  # 0.0–1.0 from structured JSON output
    action: str = "allow"
    reason: str = ""
    weight: float = 1.0
    model: str = ""  # groq model name or "fallback"/"deterministic"
    temperature: float = 0.0


class CouncilResult(BaseModel):
    """Aggregated Council decision."""

    approved: bool
    risk_score: float  # weighted aggregate 0.0-1.0
    confidence: float
    reason: str
    action: FlowAction = FlowAction.PASS
    votes: list[CouncilVote] = Field(default_factory=list)
    quorum_met: bool = False
    degraded: bool = False  # True if fallback deterministic mode was used


# ─── Pramãna Flow State ───────────────────────────────────────────────────────

# ─── Phase 3: Financial State and Recovery ────────────────────────────────────


class PositionType(str, Enum):
    """Types of DeFi positions an agent can hold."""

    LENDING = "LENDING"
    COLLATERAL = "COLLATERAL"
    LP = "LP"
    PERP = "PERP"
    STAKING = "STAKING"
    VAULT = "VAULT"


class PositionRisk(str, Enum):
    """Liquidation risk classification for a position."""

    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class SuggestedAction(str, Enum):
    """Recommended action for a position at risk."""

    HOLD = "HOLD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    HEDGE = "HEDGE"


class OpenPosition(BaseModel):
    """A single open DeFi position held by an agent."""

    position_id: str  # UUID generated at registration
    agent_addr: str
    org_id: str
    protocol: str  # "aave_v3", "compound_v3", "morpho", "gmx", etc.
    position_type: PositionType
    asset: str  # token address
    amount_wei: int
    usd_value_at_entry: float
    current_usd_value: float
    health_factor: Optional[float] = None  # None for non-lending positions
    liquidation_threshold: Optional[float] = None
    chain_id: int
    block_entered: int
    last_updated_block: int
    last_updated_ts: int
    is_finalized: bool = False


class PositionClassification(BaseModel):
    """Risk classification of a single position."""

    position: OpenPosition
    risk_level: PositionRisk
    hours_to_liquidation: Optional[float] = None  # None if not liquidatable
    suggested_action: SuggestedAction
    reasoning: str


class RecoveryAlert(BaseModel):
    """Alert generated when an agent is paused with open positions."""

    alert_id: str
    agent_addr: str
    org_id: str
    triggered_by_trace: str  # trace_hash that triggered the PAUSE
    pause_timestamp: int
    classifications: list[PositionClassification]
    total_usd_at_risk: float
    critical_count: int
    warning_count: int
    safe_count: int
    webhook_delivered: bool = False
    webhook_ts: Optional[int] = None


class FinancialSnapshot(BaseModel):
    """Financial state snapshot for a paused agent."""

    total_usd_exposure: float
    positions: list[OpenPosition]
    net_unrealized_pnl: float
    liquidation_risk_score: float  # 0.0–1.0 composite
    snapshot_block: int
    snapshot_ts: int


# ─── Pramãna Flow State ───────────────────────────────────────────────────────


class PramanaState(BaseModel):
    """Type-safe state passed through the CrewAI Flow.
    All fields have defaults so the flow can start with just an event.
    """

    event: ChainEvent | None = None
    l1_results: list[L1Result] = Field(default_factory=list)
    flag_count: int = 0
    risk_score: float = 0.0  # 0.0–1.0
    action: FlowAction = FlowAction.PASS
    final_decision_reason: str = ""
    council_result: CouncilResult | None = None
    trace_dag_json: str = ""  # serialized adjacency list
    trace_hash: str = ""  # SHA-256 hex
    pause_ref: str = ""  # pre-finalization pause correlation id
    degraded_mode: DegradedMode = DegradedMode.NORMAL
    flow_id: str = ""  # UUID per execution
    agent_profile: str = (
        "generic"  # treasury_bot | keeper_bot | arb_bot | rebalancer | generic
    )
    open_positions: list[dict] | None = None  # For Step 2: position recovery data
    agent_stake: float = 0.0  # For Step 6: Sybil guard (ETH, Web3.from_wei)
    financial_snapshot: Optional[FinancialSnapshot] = None  # Phase 3: Financial state
    recovery_alert: Optional[RecoveryAlert] = None  # Phase 3: Recovery classification


# ─── Trace ────────────────────────────────────────────────────────────────────


class CoTTrace(BaseModel):
    """Chain-of-Thought trace captured by SDK."""

    agent_id: str
    org_id: str
    flow_id: str
    prompt: str
    context: str
    retrieval: str = ""
    llm_output: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    final_output: str = ""
    timestamp: float = Field(default_factory=time.time)


# ─── Guard Policy ─────────────────────────────────────────────────────────────


class GuardRule(BaseModel):
    """Single guard rule parsed from NL input."""

    id: int
    description: str  # original NL text
    rule_type: str  # "threshold" | "pattern" | "schema"
    params: dict[str, Any] = Field(default_factory=dict)
    # e.g. {"field": "value_wei", "op": "lt", "value": 500_000_000_000_000_000}
    version: int = 1
    active: bool = True
    created_at: float = Field(default_factory=time.time)


class GuardPolicy(BaseModel):
    """Full policy for an agent — list of rules."""

    agent_id: str
    org_id: str
    rules: list[GuardRule] = Field(default_factory=list)
    version: int = 1
    rule_hash: str = ""  # keccak256 of JSON, for on-chain sync


# ─── Recovery ─────────────────────────────────────────────────────────────────


class RecoveryStep(BaseModel):
    step_id: str = ""
    action: str = ""  # "retry" | "pause" | "refuel" | "alert"
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    result: str = ""
    success: bool = False


class RecoveryResult(BaseModel):
    failure_type: str = ""
    steps_executed: list[RecoveryStep] = Field(default_factory=list)
    success: bool = False
    final_mode: str = "NORMAL"
    message: str = ""


# ─── Reputation ───────────────────────────────────────────────────────────────


class ReputationData(BaseModel):
    agent_addr: str
    org_id: str = "default"
    success_rate: float = 0.0  # 0.0–1.0
    roi_score: float = 0.0  # 0.0–1.0
    uptime_score: float = 0.0  # 0.0–1.0
    halluc_rate: float = 0.0  # 0.0–1.0 (lower = better)
    composite_score: float = 0.0  # weighted aggregate
    tx_count: int = 0
    period_days: int = 1


# ─── API Response ─────────────────────────────────────────────────────────────


class HealthStatus(BaseModel):
    status: str = "ok"
    agent_count: int = 0
    last_event_ts: float = 0.0
    groq_calls_min: int = 0
    degraded_mode: DegradedMode = DegradedMode.NORMAL
    uptime_seconds: float = 0.0


class ApiResponse(BaseModel):
    success: bool = True
    status: str = "ok"
    data: Any = None
    error: str = ""


# ─── Rebuild forward refs ─────────────────────────────────────────────────────
PramanaState.model_rebuild()
