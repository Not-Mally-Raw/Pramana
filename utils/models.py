"""
utils/models.py — Pydantic models shared across the entire Pramãna system.
Single source of truth for all data structures.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field
import time


# ─── Enums ────────────────────────────────────────────────────────────────────

class RiskTier(int, Enum):
    LOW    = 0
    MEDIUM = 1
    HIGH   = 2

class FlowAction(str, Enum):
    PASS     = "pass"
    ESCALATE = "escalate"
    PAUSE    = "pause"

class AgentStatus(str, Enum):
    ACTIVE  = "active"
    PAUSED  = "paused"
    UNKNOWN = "unknown"

class DegradedMode(str, Enum):
    NORMAL       = "normal"
    NO_LLM       = "no_llm"       # Groq down
    NO_CHAIN     = "no_chain"     # Alchemy down → Anvil only
    NO_VECTORS   = "no_vectors"   # Chroma down


# ─── Core Event ───────────────────────────────────────────────────────────────

class ChainEvent(BaseModel):
    """Raw event from WebSocket / Anvil listener."""
    agent_id:   int
    org_id:     str
    tx_hash:    str
    action:     str           # e.g. "swap", "transfer", "call"
    calldata:   str           # hex-encoded
    value_wei:  int = 0
    gas_price:  int = 0
    timestamp:  float = Field(default_factory=time.time)
    network:    str = "anvil" # anvil | sepolia | base


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


# ─── Trace ────────────────────────────────────────────────────────────────────

class CoTTrace(BaseModel):
    """Chain-of-Thought trace captured by SDK."""
    agent_id:   int
    org_id:     str
    flow_id:    str
    prompt:     str
    context:    str
    retrieval:  str = ""
    llm_output: str = ""
    tool_calls: list[str] = Field(default_factory=list)
    final_output: str = ""
    timestamp:  float = Field(default_factory=time.time)


# ─── Council ──────────────────────────────────────────────────────────────────

class CouncilVote(BaseModel):
    """Single vote from one Groq LLM call (Council Agent)."""
    temp:     float
    approve:  bool
    reason:   str
    confidence: float   # 0.0–1.0 from structured JSON output
    risk_score: int     # 0-100


class CouncilResult(BaseModel):
    """Aggregated Council decision."""
    approved:    bool
    risk_score:  int       # weighted aggregate 0-100
    confidence:  float
    reason:      str
    votes:       list[CouncilVote] = Field(default_factory=list)
    degraded:    bool = False   # True if fallback deterministic mode was used


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
    agent_id:   int
    org_id:     str
    rules:      list[GuardRule] = Field(default_factory=list)
    version:    int = 1
    rule_hash:  str = ""    # keccak256 of JSON, for on-chain sync


# ─── Recovery ─────────────────────────────────────────────────────────────────

class RecoveryStep(BaseModel):
    action:   str           # "retry" | "pause" | "refuel" | "alert"
    params:   dict[str, Any] = Field(default_factory=dict)


class RecoveryResult(BaseModel):
    success:  bool
    steps_taken: list[str] = Field(default_factory=list)
    error:    str = ""


# ─── Reputation ───────────────────────────────────────────────────────────────

class ReputationData(BaseModel):
    agent_id:     int
    success_rate: float   # 0.0–1.0
    roi_score:    float   # 0.0–1.0
    uptime:       float   # 0.0–1.0
    halluc_rate:  float   # 0.0–1.0 (lower = better)
    composite:    float   # weighted aggregate
    period_days:  int = 1


# ─── API Response ─────────────────────────────────────────────────────────────

class HealthStatus(BaseModel):
    status:           str = "ok"
    agent_count:      int = 0
    last_event_ts:    float = 0.0
    groq_calls_min:   int = 0
    degraded_mode:    DegradedMode = DegradedMode.NORMAL
    uptime_seconds:   float = 0.0


class ApiResponse(BaseModel):
    success: bool
    data:    Any = None
    error:   str = ""
