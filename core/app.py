"""
core/app.py — FastAPI service.
Endpoints: health, agents, traces, policies, config, guard, replay, org.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config.settings import get_settings
from core.db import (
    get_sqlite_conn,
    init_duckdb,
    init_sqlite,
    insert_trace,
    upsert_agent,
    upsert_policy,
    upsert_routing_rule,
)
from core.scheduler import start_scheduler, stop_scheduler
from utils.crypto import keccak256_hex, policy_hash
from utils.models import ApiResponse, DegradedMode, FlowAction, GuardPolicy, PramanaState

logger = logging.getLogger("pramana.api")

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_sqlite()
    init_duckdb()
    start_scheduler()
    logger.info("Pramana API started")
    yield
    # Shutdown
    stop_scheduler()
    logger.info("Pramana API stopped")


app = FastAPI(
    title="Pramana AgentOps API",
    version="1.0.0",
    description="On-Chain AgentOps Platform — Governing the Unseen",
    lifespan=lifespan,
)

s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _org_id(x_org_id: str | None) -> str:
    s = get_settings()
    return (x_org_id or "").strip() or s.DEFAULT_ORG_ID


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=ApiResponse)
async def health():
    return ApiResponse(status="ok", data={"version": "1.0.0"})


@app.get("/health/detailed", response_model=ApiResponse)
async def health_detailed():
    checks: dict[str, Any] = {}

    # SQLite
    try:
        with get_sqlite_conn() as conn:
            conn.execute("SELECT 1")
        checks["sqlite"] = "ok"
    except Exception as e:
        checks["sqlite"] = str(e)

    # Node
    try:
        import requests
        s = get_settings()
        resp = requests.post(s.active_rpc, json={"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}, timeout=2)
        checks["node"] = "ok" if resp.status_code == 200 else f"HTTP {resp.status_code}"
    except Exception as e:
        checks["node"] = str(e)

    return ApiResponse(status="ok", data=checks)


# ── Agents ────────────────────────────────────────────────────────────────────

class RegisterAgentRequest(BaseModel):
    agent_addr: str
    did: str = ""
    metadata: dict = {}


@app.post("/agents/register", response_model=ApiResponse)
async def register_agent(req: RegisterAgentRequest, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    if not req.agent_addr.startswith("0x") or len(req.agent_addr) != 42:
        raise HTTPException(status_code=400, detail="Invalid agent address")
    upsert_agent(req.agent_addr, org, req.did, metadata=req.metadata)
    return ApiResponse(status="created", data={"agent_addr": req.agent_addr, "org_id": org})


@app.get("/agents/{agent_addr}", response_model=ApiResponse)
async def get_agent(agent_addr: str, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_addr=? AND org_id=?", (agent_addr, org)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ApiResponse(status="ok", data=dict(row))


@app.get("/agents", response_model=ApiResponse)
async def list_agents(x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT agent_addr, did, risk_tier, paused, reputation FROM agents WHERE org_id=?", (org,)
        ).fetchall()
    return ApiResponse(status="ok", data=[dict(r) for r in rows])


# ── Traces ────────────────────────────────────────────────────────────────────

@app.get("/traces", response_model=ApiResponse)
async def list_traces(x_org_id: str | None = Header(None), limit: int = 50):
    org = _org_id(x_org_id)
    limit = min(limit, 200)
    with get_sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT flow_id, agent_addr, trace_hash, flag_count, risk_score, council_action, created_at "
            "FROM traces WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
            (org, limit),
        ).fetchall()
    return ApiResponse(status="ok", data=[dict(r) for r in rows])


@app.get("/replay/{flow_id}", response_model=ApiResponse)
async def replay_trace(flow_id: str, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT * FROM traces WHERE flow_id=? AND org_id=?", (flow_id, org)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trace not found")
    d = dict(row)
    if d.get("dag_json"):
        d["dag"] = json.loads(d["dag_json"])
    return ApiResponse(status="ok", data=d)


# ── Policies ──────────────────────────────────────────────────────────────────

class PolicyRequest(BaseModel):
    policy_id: str
    agent_addr: str
    rules: list[dict]
    version: int = 1


@app.post("/policies", response_model=ApiResponse)
async def create_policy(req: PolicyRequest, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    rules_json = json.dumps(req.rules, sort_keys=True)
    rule_hash = policy_hash(rules_json)
    upsert_policy(req.policy_id, org, req.agent_addr, rules_json, rule_hash, req.version)
    return ApiResponse(status="created", data={"policy_id": req.policy_id, "rule_hash": rule_hash})


@app.get("/policies/{agent_addr}", response_model=ApiResponse)
async def get_policies(agent_addr: str, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM policies WHERE agent_addr=? AND org_id=? AND active=1 ORDER BY version DESC",
            (agent_addr, org),
        ).fetchall()
    return ApiResponse(status="ok", data=[dict(r) for r in rows])


# ── Routing config ────────────────────────────────────────────────────────────

class RoutingRuleRequest(BaseModel):
    rule_key: str
    condition: str
    action: str
    priority: int = 5


@app.post("/config/routing", response_model=ApiResponse)
async def update_routing_rule(req: RoutingRuleRequest):
    upsert_routing_rule(req.rule_key, req.condition, req.action, req.priority)
    from utils.cache import invalidate_routing_cache
    invalidate_routing_cache()
    return ApiResponse(status="updated", data={"rule_key": req.rule_key})


# ── Guard (pre-action hook for SDK) ──────────────────────────────────────────

class GuardRequest(BaseModel):
    agent_id: str
    org_id: str
    action: str
    calldata: str = ""
    value_wei: int = 0
    gas_price: int = 0


@app.post("/guard/check", response_model=ApiResponse)
async def guard_check(req: GuardRequest):
    """
    Called by SDK @pramana/eliza-plugin before each agent action.
    Runs injection check synchronously. Returns allow/block decision.
    """
    from core.agents.injection import InjectionAgent
    from utils.models import ChainEvent, PramanaState

    event = ChainEvent(
        agent_id=req.agent_id,
        org_id=req.org_id,
        tx_hash="",
        action=req.action,
        calldata=req.calldata,
        value_wei=req.value_wei,
        gas_price=req.gas_price,
        network="guard_check",
    )
    dummy_state = PramanaState(event=event, degraded_mode=DegradedMode.NORMAL)
    agent = InjectionAgent()
    result = agent.run(event, dummy_state)

    allowed = not result.flagged or result.score < 0.70
    return ApiResponse(
        status="ok",
        data={
            "allowed": allowed,
            "score": result.score,
            "reason": result.reason,
            "patterns": result.metadata.get("matched_patterns", []),
        },
    )


# ── Event ingest (for testing / simulation) ────────────────────────────────

class IngestRequest(BaseModel):
    agent_id: str
    org_id: str = "default"
    action: str = ""
    calldata: str = ""
    value_wei: int = 0
    gas_price: int = 0
    tx_hash: str = ""
    network: str = "anvil"


@app.post("/ingest", response_model=ApiResponse)
async def ingest_event(req: IngestRequest):
    """Manually inject an event into the PramanaFlow (for testing/simulation)."""
    from core.flow import PramanaFlow
    from utils.models import ChainEvent, PramanaState

    event = ChainEvent(**req.model_dump())
    state = PramanaState(event=event, degraded_mode=DegradedMode.NORMAL)

    flow = PramanaFlow()
    # kickoff returns final state
    result_state = await asyncio.get_event_loop().run_in_executor(
        None, lambda: flow.kickoff(inputs={"event": event.model_dump()})
    )

    return ApiResponse(
        status="ok",
        data={
            "flow_id": state.flow_id,
            "action": str(state.action),
            "flag_count": state.flag_count,
            "risk_score": state.risk_score,
            "trace_hash": state.trace_hash,
        },
    )
