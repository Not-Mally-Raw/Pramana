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

    listener = None
    listener_task = None
    cfg = get_settings()
    if cfg.LISTENER_ENABLED:
        try:
            from core.flow import PramanaFlow
            from core.listener import ChainListener
            from utils.models import PramanaState

            async def _on_chain_event(event):
                loop = asyncio.get_running_loop()
                flow = PramanaFlow()
                await loop.run_in_executor(None, lambda: flow.kickoff(inputs={"event": event.model_dump()}))

            listener = ChainListener(on_event=_on_chain_event, org_id=cfg.DEFAULT_ORG_ID)
            listener_task = asyncio.create_task(listener.start())
            logger.info("Chain listener started")
        except Exception as e:
            logger.warning("Listener startup failed: %s", e)

    logger.info("Pramana API started")
    yield
    # Shutdown
    if listener is not None:
        listener.stop()
    if listener_task is not None:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
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


# ── JWT Auth & Rate Limiting ──────────────────────────────────────────────────

import time
import jwt
from fastapi.responses import JSONResponse

# Simple in-memory Token Bucket / Sliding Window: {org_id: (count, reset_epoch)}
_RATE_LIMITS: dict[str, tuple[int, float]] = {}

@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path
    if path.startswith("/health") or path.startswith("/docs") or path.startswith("/openapi.json"):
        return await call_next(request)

    cfg = get_settings()
    auth_header = request.headers.get("Authorization")
    
    # 1. JWT Authentication
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"status": "error", "detail": "Missing JWT Bearer token"})
        
    token = auth_header.split(" ")[1]
    
    try:
        payload = jwt.decode(token, cfg.JWT_SECRET, algorithms=["HS256"])
        org_id = payload.get("org_id")
        if not org_id:
            raise Exception("Missing org_id claim in token")
    except Exception as e:
        return JSONResponse(status_code=401, content={"status": "error", "detail": f"Invalid token: {e}"})

    # 2. Rate Limiting: 10 req/s per organization
    now = time.time()
    count, reset_time = _RATE_LIMITS.get(org_id, (0, 0.0))
    if now > reset_time:
        # Reset 1-second window
        _RATE_LIMITS[org_id] = (1, now + 1.0)
    else:
        if count >= 10:
            return JSONResponse(status_code=429, content={"status": "error", "detail": "Rate limit exceeded (10 req/s per org). Slow down."})
        _RATE_LIMITS[org_id] = (count + 1, reset_time)

    # 3. Inject verified org_id into ASGI scope headers (so x_org_id=Header() dependencies work unmodified)
    headers = dict(request.scope.get("headers", []))
    headers[b"x-org-id"] = org_id.encode()
    request.scope["headers"] = [(k, v) for k, v in headers.items()]

    return await call_next(request)


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
    agent_profile: str = "generic"  # Step 1: treasury_bot | keeper_bot | arb_bot | rebalancer | generic
    metadata: dict = {}


@app.post("/agents/register", response_model=ApiResponse)
async def register_agent(req: RegisterAgentRequest, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    if not req.agent_addr.startswith("0x") or len(req.agent_addr) != 42:
        raise HTTPException(status_code=400, detail="Invalid agent address")
    upsert_agent(req.agent_addr, org, req.did, agent_profile=req.agent_profile, metadata=req.metadata)
    return ApiResponse(status="created", data={
        "agent_addr": req.agent_addr,
        "org_id": org,
        "agent_profile": req.agent_profile
    })


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


# ── Bundler AA Validation ──────────────────────────────────────────────────────

@app.post("/bundler/validate", response_model=ApiResponse)
async def validate_user_op(request: Request, bg_tasks: __import__('fastapi').BackgroundTasks):
    """
    High-throughput ERC-4337 intercept layer for Bundlers.
    Returns 200 OK with HMAC-signed PASS/PAUSE/ESCALATE decision so bundlers can securely include/reject the op.
    """
    from core.parsers.userop import UserOperationParser, MalformedUserOpError
    from core.attestation import AttestationService, AttestationReplayError
    from core.flow import PramanaFlow
    from utils.models import DegradedMode, PramanaState
    import asyncio

    # Get injected verified org_id from the JWT middleware
    org_id = request.state.org_id if hasattr(request.state, "org_id") else _org_id(request.headers.get("x-org-id"))
    bundler_id = request.headers.get("x-bundler-id", "unknown_bundler")

    try:
        body = await request.json()
        payload = body.get("userOperation") or body
        dry_run = request.query_params.get("dry_run", "false").lower() == "true"
        
        # 1. Parse and Adapt
        user_op = UserOperationParser.parse(payload)
        chain_id = int(request.headers.get("x-chain-id", "31337"))
        entry_point = request.headers.get("x-entry-point", "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789")
        chain_event = UserOperationParser.adapt_to_chain_event(user_op, entry_point=entry_point, chain_id=chain_id, bundler_id=bundler_id, org_id=org_id)
        
        # 2. Idempotency and Replay Protection - check early
        try:
            with get_sqlite_conn() as conn:
                row = conn.execute("SELECT nonce, decision, risk_score, trace_hash, expires_at FROM attestation_log WHERE user_op_hash=?", (chain_event.tx_hash,)).fetchone()
                if row:
                    # Idempotency hit: If not expired, return cached decision natively
                    if row["expires_at"] >= time.time():
                        return ApiResponse(status="ok", data={
                            "attestation": {
                                "payload": {
                                    "user_op_hash": chain_event.tx_hash,
                                    "decision": row["decision"],
                                    "risk_score": row["risk_score"],
                                    "trace_hash": row["trace_hash"],
                                    "bundler_id": bundler_id,
                                    "expires_at": row["expires_at"],
                                    "nonce": row["nonce"]
                                },
                                "signature": "v1=hmac-sha256:cached",
                                "idempotent_hit": True
                            }
                        })
        except Exception:
            pass # continue fresh
            
        AttestationService.check_and_redeem_replay(chain_event.tx_hash)

        # 3. Lookup Fallback Policy and Chain Auth
        fallback_policy = "ESCALATE"
        with get_sqlite_conn() as conn:
            row = conn.execute("SELECT fallback_policy, supported_chains FROM bundler_registry WHERE bundler_url=? OR bundler_url=?", (bundler_id, org_id)).fetchone()
            if row:
                fallback_policy = row["fallback_policy"]
                if row["supported_chains"]:
                    allowed_chains = [c.strip() for c in row["supported_chains"].split(",")]
                    if str(chain_id) not in allowed_chains and "*" not in allowed_chains:
                        return ApiResponse(status="error", data={"reason": f"UNAUTHORIZED: Bundler disconnected from chain_id {chain_id}.", "decision": "PAUSE"})


        # 4. Execute MonitoringCrew (L1 only) via Flow bypass or standard kicks
        # The user requested <500ms p95. Running all L1s synchronously in an executor.
        state = PramanaState(event=chain_event, degraded_mode=DegradedMode.NORMAL)
        flow = PramanaFlow()
        
        try:
            # We want to wait tightly for flow completion. 
            # In Phase 2, wait_for handles the 800ms SLA fallback
            result_state = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: flow.kickoff(inputs={"event": chain_event.model_dump()})
                ),
                timeout=0.8
            )
            final = result_state if isinstance(result_state, PramanaState) else (flow.state if hasattr(flow, "state") else state)
            
            # The action is final.action.
            action_raw = getattr(final.action, "value", final.action)
            action = str(action_raw).upper()
            score = final.risk_score
            l1_results = [r.model_dump() for r in getattr(flow, "l1_results", [])]
            
        except asyncio.TimeoutError:
            action = fallback_policy
            score = 0.5
            l1_results = []
            logger.warning(f"Bundler validation timeout for OPHash {chain_event.tx_hash}. Returning fallback {action}.")
            
        # 5. Save to user_operations DB
        def _save_db():
            if dry_run: 
                return
            try:
                with get_sqlite_conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_operations (user_op_hash, sender, entry_point, paymaster, nonce_key, nonce_sequence, call_gas_limit, verification_gas_limit, pre_verification_gas, decision, risk_score, trace_hash, bundler_id, chain_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                        (
                            chain_event.tx_hash, user_op.sender, None, 
                            user_op.paymaster or user_op.paymasterAndData, 
                            user_op.nonce_key, user_op.nonce_sequence, 
                            user_op.callGasLimit, user_op.verificationGasLimit, user_op.preVerificationGas, 
                            action, score, chain_event.tx_hash, bundler_id, chain_id
                        )
                    )
            except Exception as e:
                logger.error(f"Failed to log userOp: {e}")
                
        bg_tasks.add_task(_save_db)

        # 6. Sign and Return
        if not dry_run:
            attestation = AttestationService.sign(
                user_op_hash=chain_event.tx_hash,
                decision=action,
                risk_score=score,
                trace_hash=chain_event.tx_hash,
                bundler_id=bundler_id
            )
        else:
            attestation = {
                "payload": {"decision": action, "risk_score": score, "dry_run": True}, 
                "signature": "unsigned"
            }

        return ApiResponse(status="ok", data={"attestation": attestation, "l1_results": l1_results})

    except MalformedUserOpError as e:
        return ApiResponse(status="error", data={"reason": str(e), "decision": "PAUSE"})
    except AttestationReplayError as e:
        return ApiResponse(status="error", data={"reason": str(e), "decision": "PAUSE"})
    except Exception as e:
        # 5xx Fallback
        logger.error(f"Validation crash: {e}")
        return ApiResponse(status="error", data={"reason": "Internal Server Error", "decision": "ESCALATE"})


class ConfirmRequest(BaseModel):
    attestation_nonce: str
    tx_hash: str

@app.post("/bundler/confirm", response_model=ApiResponse)
async def confirm_user_op(req: ConfirmRequest):
    """Marks an attestation as fully consumed on L1 to finalize replay protection."""
    from core.attestation import AttestationService
    try:
        with get_sqlite_conn() as conn:
            row = conn.execute("SELECT user_op_hash FROM attestation_log WHERE attestation_nonce=?", (req.attestation_nonce,)).fetchone()
            if row:
                user_op_hash = row["user_op_hash"]
                AttestationService.mark_redeemed(user_op_hash)
                # optionally hook `tx_hash` to DB
                return ApiResponse(status="ok", data={"redeemed": True, "user_op_hash": user_op_hash})
        return ApiResponse(status="error", data={"reason": "attestation_nonce not found"})
    except Exception as e:
        return ApiResponse(status="error", data={"reason": str(e)})


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

    cfg = get_settings()
    fail_mode = (cfg.GUARD_FAIL_MODE or "fail_open").lower()
    threshold = float(cfg.GUARD_BLOCK_THRESHOLD)

    try:
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

        allowed = (not result.flagged) or (result.score < threshold)
        return ApiResponse(
            status="ok",
            data={
                "allowed": allowed,
                "score": result.score,
                "reason": result.reason,
                "patterns": result.metadata.get("matched_patterns", []),
                "threshold": threshold,
                "fail_mode": fail_mode,
            },
        )
    except Exception as e:
        if fail_mode == "fail_closed":
            return ApiResponse(
                status="ok",
                data={
                    "allowed": False,
                    "score": 1.0,
                    "reason": f"guard unavailable (fail closed): {str(e)[:120]}",
                    "patterns": [],
                    "threshold": threshold,
                    "fail_mode": fail_mode,
                },
            )
        return ApiResponse(
            status="ok",
            data={
                "allowed": True,
                "score": 0.0,
                "reason": "guard unavailable (fail open)",
                "patterns": [],
                "threshold": threshold,
                "fail_mode": fail_mode,
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
    try:
        # kickoff can block if a downstream dependency stalls; protect API latency.
        result_state = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: flow.kickoff(inputs={"event": event.model_dump()})
            ),
            timeout=25,
        )
    except asyncio.TimeoutError:
        return ApiResponse(
            status="accepted",
            data={
                "flow_id": state.flow_id,
                "action": str(state.action),
                "flag_count": state.flag_count,
                "risk_score": state.risk_score,
                "trace_hash": state.trace_hash,
                "note": "flow execution timed out at API layer",
            },
        )

    # Prefer explicit return value, then flow.state, then local fallback.
    final = result_state if isinstance(result_state, PramanaState) else (flow.state if hasattr(flow, "state") else state)

    return ApiResponse(
        status="ok",
        data={
            "flow_id": final.flow_id,
            "action": str(final.action),
            "flag_count": final.flag_count,
            "risk_score": final.risk_score,
            "trace_hash": final.trace_hash,
        },
    )
