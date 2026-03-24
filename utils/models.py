"""
utils/models.py — Pydantic models shared across the entire Pramãna system.
Single source of truth for all data structures.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field
import time


# ─── Enums ────────────────────────────────────────────────────────────────────

class RiskTier(int, Enum):
    LOW    = 0
    MEDIUM = 1
    HIGH   = 2

class FlowAction(str, Enum):
    PASS      = "pass"
    ESCALATE  = "escalate"
    PAUSE     = "pause"
    EMERGENCY = "emergency"

class AgentStatus(str, Enum):
    ACTIVE  = "active"
    PAUSED  = "paused"
    UNKNOWN = "unknown"

class DegradedMode(str, Enum):
    NORMAL       = "normal"
    NO_LLM       = "no_llm"       # Groq down
    NO_CHAIN     = "no_chain"     # Alchemy down → Anvil only
    NO_VECTORS   = "no_vectors"   # Chroma down

class AgentProfile(str, Enum):
    """Agent behavior profile for threshold tuning."""
    TREASURY   = "treasury_bot"
    KEEPER     = "keeper_bot"
    ARB        = "arb_bot"
    REBALANCER = "rebalancer"
    GENERIC    = "generic"


# ─── Core Event ───────────────────────────────────────────────────────────────

class ChainEvent(BaseModel):
    """Raw event from WebSocket / Anvil listener."""
    agent_id:   str           # Ethereum address or identifier
    org_id:     str
    tx_hash:    str
    action:     str           # e.g. "swap", "transfer", "call"
    calldata:   str           # hex-encoded
    value_wei:  int = 0
    gas_price:  int = 0
    timestamp:  float = Field(default_factory=time.time)
    network:    str = "anvil" # anvil | sepolia | base


# ─── L1 Agent Results ─────────────────────────────────────────────────────────

class L1Result(BaseModel):
    """Output from a single L1 monitoring agent."""
    agent_name: str
    flagged:    bool
    score:      float          # 0.0–1.0
    reason:     str = ""
    latency_ms: float = 0.0
    pass_count: int = 1        # 1 = first pass, 2 = second pass (two-pass verify)
    metadata:   dict[str, Any] = Field(default_factory=dict)


# ─── Council ──────────────────────────────────────────────────────────────────

class CouncilVote(BaseModel):
    """Single vote from one Groq LLM call (Council Agent)."""
    judge_id:    str = ""
    approved:    bool = False
    risk_score:  int = 50          # 0-100
    confidence:  float = 0.5      # 0.0–1.0 from structured JSON output
    action:      str = "allow"
    reason:      str = ""
    weight:      float = 1.0
    model:       str = ""         # groq model name or "fallback"/"deterministic"
    temperature: float = 0.0


class CouncilResult(BaseModel):
    """Aggregated Council decision."""
    approved:    bool
    risk_score:  float     # weighted aggregate 0.0-1.0
    confidence:  float
    reason:      str
    action:      FlowAction = FlowAction.PASS
    votes:       list[CouncilVote] = Field(default_factory=list)
    quorum_met:  bool = False
    degraded:    bool = False   # True if fallback deterministic mode was used


# ─── Pramãna Flow State ───────────────────────────────────────────────────────

class PramanaState(BaseModel):
    """Type-safe state passed through the CrewAI Flow.
    All fields have defaults so the flow can start with just an event.
    """
    event:          ChainEvent | None = None
    l1_results:     list[L1Result] = Field(default_factory=list)
    flag_count:     int = 0
    risk_score:     float = 0.0        # 0.0–1.0
    action:         FlowAction = FlowAction.PASS
    council_result: CouncilResult | None = None
    trace_dag_json: str = ""            # serialized adjacency list
    trace_hash:     str = ""            # SHA-256 hex
    degraded_mode:  DegradedMode = DegradedMode.NORMAL
    flow_id:        str = ""            # UUID per execution
    agent_profile:  str = "generic"     # treasury_bot | keeper_bot | arb_bot | rebalancer | generic
    open_positions: list[dict] | None = None  # For Step 2: position recovery data
    agent_stake:    float = 0.0         # For Step 6: Sybil guard (ETH, Web3.from_wei)


# ─── Trace ────────────────────────────────────────────────────────────────────

class CoTTrace(BaseModel):
    """Chain-of-Thought trace captured by SDK."""
    agent_id:   str
    org_id:     str
    flow_id:    str
    prompt:     str
    context:    str
    retrieval:  str = ""
    llm_output: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    final_output: str = ""
    timestamp:  float = Field(default_factory=time.time)


# ─── Guard Policy ─────────────────────────────────────────────────────────────

class GuardRule(BaseModel):
    """Single guard rule parsed from NL input."""
    id:           int
    description:  str          # original NL text
    rule_type:    str          # "threshold" | "pattern" | "schema"
    params:       dict[str, Any] = Field(default_factory=dict)
    # e.g. {"field": "value_wei", "op": "lt", "value": 500_000_000_000_000_000}
    version:      int = 1
    active:       bool = True
    created_at:   float = Field(default_factory=time.time)


class GuardPolicy(BaseModel):
    """Full policy for an agent — list of rules."""
    agent_id:   str
    org_id:     str
    rules:      list[GuardRule] = Field(default_factory=list)
    version:    int = 1
    rule_hash:  str = ""    # keccak256 of JSON, for on-chain sync


# ─── Recovery ─────────────────────────────────────────────────────────────────

class RecoveryStep(BaseModel):
    step_id:     str = ""
    action:      str = ""        # "retry" | "pause" | "refuel" | "alert"
    params:      dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    result:      str = ""
    success:     bool = False


class RecoveryResult(BaseModel):
    failure_type:   str = ""
    steps_executed:  list[RecoveryStep] = Field(default_factory=list)
    success:         bool = False
    final_mode:      str = "NORMAL"
    message:         str = ""


# ─── Reputation ───────────────────────────────────────────────────────────────

class ReputationData(BaseModel):
    agent_addr:      str
    org_id:          str = "default"
    success_rate:    float = 0.0   # 0.0–1.0
    roi_score:       float = 0.0  # 0.0–1.0
    uptime_score:    float = 0.0  # 0.0–1.0
    halluc_rate:     float = 0.0  # 0.0–1.0 (lower = better)
    composite_score: float = 0.0  # weighted aggregate
    tx_count:        int = 0
    period_days:     int = 1


# ─── API Response ─────────────────────────────────────────────────────────────

class HealthStatus(BaseModel):
    status:           str = "ok"
    agent_count:      int = 0
    last_event_ts:    float = 0.0
    groq_calls_min:   int = 0
    degraded_mode:    DegradedMode = DegradedMode.NORMAL
    uptime_seconds:   float = 0.0


class ApiResponse(BaseModel):
    success: bool = True
    status:  str  = "ok"
    data:    Any  = None
    error:   str  = ""


# ─── Rebuild forward refs ─────────────────────────────────────────────────────
PramanaState.model_rebuild()
