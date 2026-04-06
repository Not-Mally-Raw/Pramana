"""
core/app.py — FastAPI service.
Endpoints: health, agents, traces, policies, config, guard, replay, org.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import requests
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.config.settings import get_settings
from core.db import (
    get_duck_conn,
    get_sqlite_conn,
    init_duckdb,
    init_sqlite,
    insert_lifecycle_transition,
    upsert_agent,
    upsert_policy,
    upsert_routing_rule,
)
from core.flow import PramanaFlow
from core.listener import ChainListener
from core.realtime import publish_event, realtime_hub
from core.scheduler import start_scheduler, stop_scheduler
from utils.crypto import policy_hash
from utils.models import (
    ApiResponse,
    ChainEvent,
    DegradedMode,
    OpenPosition,
    PramanaState,
)

logger = logging.getLogger("pramana.api")

# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_sqlite()
    init_duckdb()
    start_scheduler()
    realtime_hub.bind_loop(asyncio.get_running_loop())

    listener = None
    listener_task = None
    cfg = get_settings()
    if cfg.LISTENER_ENABLED:
        try:
            async def _on_chain_event(event):
                loop = asyncio.get_running_loop()
                flow = PramanaFlow()
                await loop.run_in_executor(
                    None, lambda: flow.kickoff(inputs={"event": event.model_dump()})
                )

            listener = ChainListener(
                on_event=_on_chain_event, org_id=cfg.DEFAULT_ORG_ID
            )
            listener_task = asyncio.create_task(listener.start())
            logger.info("Chain listener started")
        except (ImportError, RuntimeError, OSError, ValueError) as e:
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


def _check_rate_limit_sqlite(org_id: str, limit: int = 10) -> bool:
    """
    Sliding window rate limiter backed by SQLite (Phase 4).
    Window: 1 second. Persists across restarts.
    Cleans old entries on each call.
    Returns True if request is allowed, False if rate limited.
    """
    now = time.time()
    window_start = now - 1.0

    with get_sqlite_conn() as conn:
        conn.execute(
            "DELETE FROM rate_limit_windows WHERE org_id=? AND ts < ?",
            (org_id, window_start),
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limit_windows WHERE org_id=? AND ts >= ?",
            (org_id, window_start),
        ).fetchone()[0]

        if count >= limit:
            return False  # Rate limited

        conn.execute(
            "INSERT INTO rate_limit_windows (org_id, ts) VALUES (?,?)", (org_id, now)
        )
    return True


@app.middleware("http")
async def auth_and_rate_limit(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/health")
        or path.startswith("/docs")
        or path.startswith("/openapi.json")
        or path.startswith("/metrics/realtime")
    ):
        return await call_next(request)

    cfg = get_settings()
    auth_header = request.headers.get("Authorization")

    # 1. JWT Authentication
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"status": "error", "detail": "Missing JWT Bearer token"},
        )

    token = auth_header.split(" ")[1]

    try:
        payload = jwt.decode(token, cfg.JWT_SECRET, algorithms=["HS256"])
        org_id = payload.get("org_id")
        if not org_id:
            raise ValueError("Missing org_id claim in token")
    except (jwt.PyJWTError, ValueError) as e:
        return JSONResponse(
            status_code=401,
            content={"status": "error", "detail": f"Invalid token: {e}"},
        )

    # 2. Rate Limiting: 10 req/s per organization (SQLite-backed, persistent)
    if not _check_rate_limit_sqlite(org_id, limit=10):
        return JSONResponse(
            status_code=429,
            content={
                "status": "error",
                "detail": "Rate limit exceeded (10 req/s per org). Slow down.",
            },
        )

    # 3. Inject verified org_id into ASGI scope headers (so x_org_id=Header() dependencies work unmodified)
    headers = dict(request.scope.get("headers", []))
    headers[b"x-org-id"] = org_id.encode()
    request.scope["headers"] = list(headers.items())
    request.state.org_id = org_id

    return await call_next(request)


# ── Helper ────────────────────────────────────────────────────────────────────


def _org_id(x_org_id: str | None) -> str:
    s = get_settings()
    return (x_org_id or "").strip() or s.DEFAULT_ORG_ID


def _safe_parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_ms(dt: datetime | None) -> int:
    if dt is None:
        return int(time.time() * 1000)
    return int(dt.timestamp() * 1000)


def _decision_kind(action: str | None) -> str:
    value = (action or "").upper()
    if "PAUSE" in value:
        return "PAUSE"
    if "ESCALATE" in value:
        return "ESCALATE"
    return "PASS"


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
    except sqlite3.Error as e:
        checks["sqlite"] = str(e)

    # Node
    try:
        s = get_settings()
        resp = requests.post(
            s.active_rpc,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            timeout=2,
        )
        checks["node"] = "ok" if resp.status_code == 200 else f"HTTP {resp.status_code}"
    except requests.RequestException as e:
        checks["node"] = str(e)

    return ApiResponse(status="ok", data=checks)


@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    org_id = websocket.query_params.get("org_id")
    trace_id = websocket.query_params.get("trace_id")
    since_raw = websocket.query_params.get("since", "0")

    try:
        since = int(since_raw)
    except ValueError:
        since = 0

    await realtime_hub.connect(websocket, org_id=org_id, trace_id=trace_id)

    try:
        if since > 0:
            backlog = realtime_hub.events_since(
                since=since,
                org_id=org_id,
                trace_id=trace_id,
                limit=1000,
            )
            for event in backlog:
                await websocket.send_json(event)

        while True:
            msg = await websocket.receive_text()
            if msg.lower().strip() == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": int(time.time() * 1000)}
                )
    except WebSocketDisconnect:
        await realtime_hub.disconnect(websocket)
    except (RuntimeError, ValueError, TypeError):
        await realtime_hub.disconnect(websocket)


@app.get("/metrics/realtime", response_model=ApiResponse)
async def metrics_realtime(x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    now = datetime.now(timezone.utc)
    one_min_ago = now - timedelta(minutes=1)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    with get_sqlite_conn() as conn:
        agents_rows = conn.execute(
            "SELECT agent_addr, did, agent_profile, paused FROM agents WHERE org_id=?",
            (org,),
        ).fetchall()
        trace_rows = conn.execute(
            "SELECT trace_hash, agent_addr, dag_json, risk_score, council_action, created_at "
            "FROM traces WHERE org_id=? ORDER BY created_at DESC LIMIT 1200",
            (org,),
        ).fetchall()
        alerts_rows = conn.execute(
            "SELECT alert_id, agent_addr, org_id, critical_count, warning_count, safe_count, "
            "total_usd_at_risk, created_at, webhook_delivered "
            "FROM recovery_alerts WHERE org_id=? ORDER BY created_at DESC LIMIT 300",
            (org,),
        ).fetchall()
        lifecycle_rows = conn.execute(
            "SELECT agent_addr, org_id, from_state, to_state, reason, created_at "
            "FROM lifecycle_transitions WHERE org_id=? ORDER BY created_at DESC LIMIT 200",
            (org,),
        ).fetchall()

    traces = [dict(row) for row in trace_rows]

    def _top_flagging_agent(dag_json: str | None) -> str:
        if not dag_json:
            return ""
        try:
            parsed = json.loads(dag_json)
            nodes = parsed.get("nodes", {})
            ranked: list[tuple[str, float]] = []
            for node_name, payload in nodes.items():
                score = float((payload or {}).get("score", 0.0))
                if score > 0.5:
                    ranked.append((node_name, score))
            if not ranked:
                return ""
            ranked.sort(key=lambda item: item[1], reverse=True)
            return ranked[0][0]
        except (TypeError, ValueError, json.JSONDecodeError):
            return ""

    timeline: list[tuple[datetime, str]] = []
    recent_decisions: list[dict[str, Any]] = []
    risk_by_agent: dict[str, list[float]] = {}

    for row in traces:
        created_dt = _safe_parse_iso(row.get("created_at"))
        if created_dt is None:
            continue
        decision_kind = _decision_kind(row.get("council_action"))
        timeline.append((created_dt, decision_kind))

        agent_addr = str(row.get("agent_addr") or "")
        risk_by_agent.setdefault(agent_addr, []).append(
            float(row.get("risk_score") or 0.0)
        )

        if len(recent_decisions) < 20:
            recent_decisions.append(
                {
                    "type": "decision",
                    "trace_id": row.get("trace_hash") or "",
                    "agent_addr": agent_addr,
                    "org_id": org,
                    "decision": decision_kind,
                    "risk_score": float(row.get("risk_score") or 0.0),
                    "top_agent": _top_flagging_agent(row.get("dag_json")),
                    "timestamp": _to_ms(created_dt),
                }
            )

    active_agents = sum(1 for row in agents_rows if not bool(row["paused"]))
    paused_agents = sum(1 for row in agents_rows if bool(row["paused"]))

    decisions_last_min = sum(1 for dt, _ in timeline if dt >= one_min_ago)
    decisions_prev_min = sum(
        1
        for dt, _ in timeline
        if (one_min_ago - timedelta(minutes=1)) <= dt < one_min_ago
    )

    current_hour = [(dt, decision) for dt, decision in timeline if dt >= one_hour_ago]
    previous_hour = [
        (dt, decision)
        for dt, decision in timeline
        if two_hours_ago <= dt < one_hour_ago
    ]

    def _rate(entries: list[tuple[datetime, str]], target: str) -> float:
        if not entries:
            return 0.0
        hits = sum(1 for _, decision in entries if decision == target)
        return (hits / len(entries)) * 100.0

    escalation_rate_pct = _rate(current_hour, "ESCALATE")
    pause_rate_pct = _rate(current_hour, "PAUSE")
    prev_escalation_rate = _rate(previous_hour, "ESCALATE")
    prev_pause_rate = _rate(previous_hour, "PAUSE")

    active_alerts: list[dict[str, Any]] = []
    alert_timeline: list[tuple[datetime, int]] = []

    for row in alerts_rows:
        created_at_raw = row["created_at"]
        alert_dt = datetime.fromtimestamp(created_at_raw, tz=timezone.utc)
        alert_timeline.append((alert_dt, int(row["critical_count"] or 0)))
        if int(row["critical_count"] or 0) <= 0 and int(row["warning_count"] or 0) <= 0:
            continue
        active_alerts.append(
            {
                "type": "recovery_alert",
                "alert_id": row["alert_id"],
                "agent_addr": row["agent_addr"],
                "org_id": row["org_id"],
                "critical_count": int(row["critical_count"] or 0),
                "warning_count": int(row["warning_count"] or 0),
                "safe_count": int(row["safe_count"] or 0),
                "total_usd_at_risk": float(row["total_usd_at_risk"] or 0.0),
                "timestamp": int(created_at_raw) * 1000,
            }
        )

    recovery_backlog = sum(
        1
        for row in alerts_rows
        if int(row["critical_count"] or 0) > 0
        and int(row["webhook_delivered"] or 0) == 0
    )
    recovery_recent = sum(
        1 for dt, critical in alert_timeline if dt >= one_hour_ago and critical > 0
    )
    recovery_prev = sum(
        1
        for dt, critical in alert_timeline
        if two_hours_ago <= dt < one_hour_ago and critical > 0
    )

    lifecycle_transitions = []
    for row in lifecycle_rows:
        ts = _safe_parse_iso(row["created_at"])
        lifecycle_transitions.append(
            {
                "type": "lifecycle",
                "agent_addr": row["agent_addr"],
                "org_id": row["org_id"],
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "reason": row["reason"] or "",
                "timestamp": _to_ms(ts),
            }
        )

    minute_buckets = [now - timedelta(minutes=i) for i in range(59, -1, -1)]
    spark_decisions: list[int] = []
    spark_escalate: list[float] = []
    spark_pause: list[float] = []

    for bucket in minute_buckets:
        window_end = bucket + timedelta(minutes=1)
        entries = [decision for dt, decision in timeline if bucket <= dt < window_end]
        spark_decisions.append(len(entries))
        if entries:
            spark_escalate.append(
                (sum(1 for item in entries if item == "ESCALATE") / len(entries)) * 100
            )
            spark_pause.append(
                (sum(1 for item in entries if item == "PAUSE") / len(entries)) * 100
            )
        else:
            spark_escalate.append(0.0)
            spark_pause.append(0.0)

    fleet = []
    for row in agents_rows:
        agent_addr = row["agent_addr"]
        trend = risk_by_agent.get(agent_addr, [])[:100]
        lifecycle_state = "PAUSED" if bool(row["paused"]) else "ACTIVE"
        fleet.append(
            {
                "agent_addr": agent_addr,
                "did": row["did"],
                "profile_type": row["agent_profile"] or "generic",
                "lifecycle_state": lifecycle_state,
                "latest_risk": trend[0] if trend else 0.0,
                "risk_trend": trend,
            }
        )

    priority_order = {
        "PAUSED": 0,
        "DEGRADED": 1,
        "RECOVERING": 2,
        "ACTIVE": 3,
        "UNKNOWN": 4,
    }
    fleet.sort(
        key=lambda item: (
            priority_order.get(item.get("lifecycle_state", "UNKNOWN"), 4),
            -float(item.get("latest_risk", 0.0)),
        )
    )

    return ApiResponse(
        status="ok",
        data={
            "api_status": "healthy",
            "active_agents": active_agents,
            "paused_agents": paused_agents,
            "decisions_per_min": decisions_last_min,
            "escalation_rate_pct": escalation_rate_pct,
            "pause_rate_pct": pause_rate_pct,
            "recovery_backlog": recovery_backlog,
            "pending_guardian_votes": 0,
            "connected_stream_clients": realtime_hub.connected_clients(),
            "now_ts": int(now.timestamp() * 1000),
            "sparkline": {
                "active_agents": [active_agents] * 60,
                "decisions_per_min": spark_decisions,
                "escalation_rate_pct": spark_escalate,
                "pause_rate_pct": spark_pause,
                "recovery_backlog": [recovery_backlog] * 60,
            },
            "delta": {
                "active_agents": 0,
                "decisions_per_min": decisions_last_min - decisions_prev_min,
                "escalation_rate_pct": escalation_rate_pct - prev_escalation_rate,
                "pause_rate_pct": pause_rate_pct - prev_pause_rate,
                "recovery_backlog": recovery_recent - recovery_prev,
            },
            "recent_decisions": recent_decisions,
            "fleet": fleet,
            "active_recovery_alerts": active_alerts,
            "lifecycle_transitions": lifecycle_transitions,
        },
    )


# ── Agents ────────────────────────────────────────────────────────────────────


class RegisterAgentRequest(BaseModel):
    agent_addr: str
    did: str = ""
    agent_profile: str = (
        "generic"  # Step 1: treasury_bot | keeper_bot | arb_bot | rebalancer | generic
    )
    metadata: dict = {}


class RotateKeyRequest(BaseModel):
    new_did: str
    old_did: str | None = None
    reason: str = "scheduled_rotation"
    rotation_block: int | None = None


@app.post("/agents/register", response_model=ApiResponse)
async def register_agent(
    req: RegisterAgentRequest, x_org_id: str | None = Header(None)
):
    org = _org_id(x_org_id)
    if not req.agent_addr.startswith("0x") or len(req.agent_addr) != 42:
        raise HTTPException(status_code=400, detail="Invalid agent address")
    upsert_agent(
        req.agent_addr,
        org,
        req.did,
        agent_profile=req.agent_profile,
        metadata=req.metadata,
    )
    return ApiResponse(
        status="created",
        data={
            "agent_addr": req.agent_addr,
            "org_id": org,
            "agent_profile": req.agent_profile,
        },
    )


@app.get("/agents/{agent_addr}", response_model=ApiResponse)
def get_agent(agent_addr: str, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE agent_addr=? AND org_id=?", (agent_addr, org)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ApiResponse(status="ok", data=dict(row))


@app.get("/agents", response_model=ApiResponse)
def list_agents(x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        rows = conn.execute(
            "SELECT agent_addr, did, risk_tier, paused, reputation FROM agents WHERE org_id=?",
            (org,),
        ).fetchall()
    return ApiResponse(status="ok", data=[dict(r) for r in rows])


@app.get("/agents/{agent_addr}/deep", response_model=ApiResponse)
def get_agent_deep(agent_addr: str, x_org_id: str | None = Header(None)):
    """Deep dive payload for Agent details UI (Task 1)."""
    org = _org_id(x_org_id)

    with get_sqlite_conn() as conn:
        agent_row = conn.execute(
            "SELECT * FROM agents WHERE agent_addr=? AND org_id=?", (agent_addr, org)
        ).fetchone()
        if not agent_row:
            raise HTTPException(status_code=404, detail="Agent not found")

        traces = conn.execute(
            "SELECT flow_id, trace_hash, risk_score, flag_count, council_action, created_at "
            "FROM traces WHERE agent_addr=? AND org_id=? ORDER BY created_at DESC LIMIT 10",
            (agent_addr, org),
        ).fetchall()

        alerts = conn.execute(
            "SELECT alert_id, total_usd_at_risk, critical_count, warning_count, created_at "
            "FROM recovery_alerts WHERE org_id=? ORDER BY created_at DESC LIMIT 5",
            (org,),
        ).fetchall()

    return ApiResponse(
        status="ok",
        data={
            "agent": dict(agent_row),
            "recent_traces": [dict(t) for t in traces],
            "alerts": [dict(a) for a in alerts],
        },
    )


@app.post("/agents/{agent_addr}/keys/rotate", response_model=ApiResponse)
def rotate_agent_key(
    agent_addr: str,
    req: RotateKeyRequest,
    x_org_id: str | None = Header(None),
):
    """Rotate an agent key/DID and reset drift baseline to avoid false positives."""
    org = _org_id(x_org_id)
    if req.old_did and req.old_did == req.new_did:
        raise HTTPException(
            status_code=400, detail="old_did and new_did cannot be the same"
        )

    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT did, metadata FROM agents WHERE agent_addr=? AND org_id=?",
            (agent_addr, org),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")

        previous_did = row["did"] or ""
        if req.old_did and previous_did and req.old_did != previous_did:
            raise HTTPException(
                status_code=409, detail="old_did does not match current DID"
            )

        metadata: dict[str, Any]
        try:
            metadata = json.loads(row["metadata"] or "{}")
            if not isinstance(metadata, dict):
                metadata = {}
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}

        metadata["key_rotation"] = {
            "old_did": previous_did,
            "new_did": req.new_did,
            "reason": req.reason,
            "rotation_block": req.rotation_block,
            "rotated_at_ts": int(time.time()),
        }

        conn.execute(
            "UPDATE agents SET did=?, metadata=?, updated_at=datetime('now') WHERE agent_addr=? AND org_id=?",
            (req.new_did, json.dumps(metadata), agent_addr, org),
        )

    baseline_reset = False
    baseline_error = ""
    try:
        import chromadb

        cfg = get_settings()
        client = chromadb.PersistentClient(path=cfg.CHROMA_PATH)
        collection_name = f"baseline_{agent_addr}_{org}"
        try:
            client.delete_collection(collection_name)
            baseline_reset = True
        except (RuntimeError, ValueError) as exc:
            baseline_error = str(exc)
    except (ImportError, RuntimeError, OSError, ValueError) as exc:
        baseline_error = str(exc)

    try:
        insert_lifecycle_transition(
            agent_addr=agent_addr,
            org_id=org,
            from_state="ACTIVE",
            to_state="KEY_ROTATED",
            reason=f"key rotation: {req.reason}",
            capital_at_risk_usd=0.0,
        )
        publish_event(
            {
                "type": "lifecycle",
                "agent_addr": agent_addr,
                "org_id": org,
                "from_state": "ACTIVE",
                "to_state": "KEY_ROTATED",
                "reason": f"key rotation: {req.reason}",
                "timestamp": int(time.time() * 1000),
            }
        )
    except (sqlite3.Error, RuntimeError, ValueError) as exc:
        logger.warning("Failed to persist key rotation lifecycle transition: %s", exc)

    return ApiResponse(
        status="ok",
        data={
            "agent_addr": agent_addr,
            "org_id": org,
            "previous_did": previous_did,
            "new_did": req.new_did,
            "baseline_reset": baseline_reset,
            "baseline_reset_error": baseline_error,
        },
    )


# ── Traces ────────────────────────────────────────────────────────────────────

# ── Phase 3: Position Registration ─────────────────────────────────────────


@app.post("/agents/{agent_id}/positions", response_model=ApiResponse)
async def register_positions(
    agent_id: str, positions: list[OpenPosition], x_org_id: str | None = Header(None)
):
    """Register or update open positions for an agent. Called continuously by agents."""
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        for pos in positions:
            conn.execute(
                """
                INSERT INTO open_positions
                (position_id, agent_addr, org_id, protocol, position_type,
                 asset, amount_wei, usd_value_at_entry, current_usd_value,
                 health_factor, liquidation_threshold, chain_id,
                 block_entered, last_updated_block, last_updated_ts,
                 is_finalized, is_closed)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                ON CONFLICT(position_id) DO UPDATE SET
                    current_usd_value=excluded.current_usd_value,
                    health_factor=excluded.health_factor,
                    last_updated_block=excluded.last_updated_block,
                    last_updated_ts=excluded.last_updated_ts,
                    is_finalized=excluded.is_finalized
            """,
                (
                    pos.position_id,
                    agent_id,
                    org,
                    pos.protocol,
                    pos.position_type.value,
                    pos.asset,
                    str(pos.amount_wei),
                    pos.usd_value_at_entry,
                    pos.current_usd_value,
                    pos.health_factor,
                    pos.liquidation_threshold,
                    pos.chain_id,
                    pos.block_entered,
                    pos.last_updated_block,
                    pos.last_updated_ts,
                    int(pos.is_finalized),
                ),
            )
    return ApiResponse(
        status="ok", data={"registered": len(positions), "agent_id": agent_id}
    )


@app.delete("/agents/{agent_id}/positions/{position_id}", response_model=ApiResponse)
async def close_position(
    agent_id: str, position_id: str, x_org_id: str | None = Header(None)
):
    """Mark a position as closed."""
    org = _org_id(x_org_id)
    with get_sqlite_conn() as conn:
        conn.execute(
            "UPDATE open_positions SET is_closed=1, closed_ts=? "
            "WHERE position_id=? AND agent_addr=? AND org_id=?",
            (int(time.time()), position_id, agent_id, org),
        )
    return ApiResponse(status="ok", data={"closed": position_id})


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


@app.get("/decisions/{trace_hash}/export", response_model=ApiResponse)
async def export_decision_bundle(trace_hash: str, x_org_id: str | None = Header(None)):
    org = _org_id(x_org_id)

    with get_sqlite_conn() as conn:
        trace_row = conn.execute(
            "SELECT flow_id, trace_hash, agent_addr, org_id, dag_json, flag_count, risk_score, council_action, created_at "
            "FROM traces WHERE trace_hash=? AND org_id=? ORDER BY created_at DESC LIMIT 1",
            (trace_hash, org),
        ).fetchone()

        if not trace_row:
            raise HTTPException(status_code=404, detail="Trace not found")

        user_op_row = conn.execute(
            "SELECT user_op_hash, sender, paymaster, nonce_key, nonce_sequence, call_gas_limit, "
            "verification_gas_limit, pre_verification_gas, decision, risk_score, bundler_id, chain_id, created_at "
            "FROM user_operations WHERE trace_hash=? ORDER BY created_at DESC LIMIT 1",
            (trace_hash,),
        ).fetchone()

        lifecycle_rows = conn.execute(
            "SELECT agent_addr, from_state, to_state, reason, capital_at_risk_usd, created_at "
            "FROM lifecycle_transitions WHERE org_id=? AND agent_addr=? ORDER BY created_at DESC LIMIT 50",
            (org, trace_row["agent_addr"]),
        ).fetchall()

        recovery_rows = conn.execute(
            "SELECT alert_id, pause_timestamp, classifications_json, total_usd_at_risk, critical_count, warning_count, safe_count, created_at "
            "FROM recovery_alerts WHERE org_id=? AND triggered_by_trace=? ORDER BY created_at DESC",
            (org, trace_hash),
        ).fetchall()

        policy_row = conn.execute(
            "SELECT policy_id, rule_hash, version, updated_at "
            "FROM policies WHERE org_id=? AND agent_addr=? AND active=1 ORDER BY version DESC LIMIT 1",
            (org, trace_row["agent_addr"]),
        ).fetchone()

    council_decision: dict[str, Any] | None = None
    try:
        with get_duck_conn() as duck_conn:
            row = duck_conn.execute(
                "SELECT flow_id, approved, risk_score, confidence, action, reason, decided_at "
                "FROM council_decisions WHERE flow_id=? LIMIT 1",
                [trace_row["flow_id"]],
            ).fetchone()
        if row:
            council_decision = {
                "flow_id": row[0],
                "approved": bool(row[1]),
                "risk_score": float(row[2] or 0.0),
                "confidence": float(row[3] or 0.0),
                "action": row[4],
                "reason": row[5],
                "decided_at": str(row[6]),
            }
    except (RuntimeError, OSError, ValueError, TypeError):
        council_decision = None

    trace_payload = dict(trace_row)
    dag = {}
    if trace_payload.get("dag_json"):
        try:
            dag = json.loads(trace_payload["dag_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            dag = {}

    user_operation = dict(user_op_row) if user_op_row else None
    lifecycle = [dict(row) for row in lifecycle_rows]

    recovery_alerts = []
    for row in recovery_rows:
        record = dict(row)
        try:
            record["classifications"] = json.loads(
                record.pop("classifications_json") or "[]"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            record["classifications"] = []
        recovery_alerts.append(record)

    payload = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trace_hash": trace_payload.get("trace_hash"),
        "flow_id": trace_payload.get("flow_id"),
        "org_id": trace_payload.get("org_id"),
        "agent_addr": trace_payload.get("agent_addr"),
        "decision": {
            "action": _decision_kind(trace_payload.get("council_action")),
            "risk_score": float(trace_payload.get("risk_score") or 0.0),
            "flag_count": int(trace_payload.get("flag_count") or 0),
            "created_at": trace_payload.get("created_at"),
        },
        "dag": dag,
        "policy": dict(policy_row) if policy_row else None,
        "council": council_decision,
        "user_operation": user_operation,
        "recovery_alerts": recovery_alerts,
        "lifecycle_context": lifecycle,
    }

    return ApiResponse(status="ok", data=payload)


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
    upsert_policy(
        req.policy_id, org, req.agent_addr, rules_json, rule_hash, req.version
    )
    return ApiResponse(
        status="created", data={"policy_id": req.policy_id, "rule_hash": rule_hash}
    )


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
async def validate_user_op(
    request: Request, bg_tasks: __import__("fastapi").BackgroundTasks
):
    """
    High-throughput ERC-4337 intercept layer for Bundlers.
    Returns 200 OK with HMAC-signed PASS/PAUSE/ESCALATE decision so bundlers can securely include/reject the op.
    """
    from core.attestation import AttestationReplayError, AttestationService
    from core.parsers.userop import MalformedUserOpError, UserOperationParser

    # Get injected verified org_id from the JWT middleware
    org_id = (
        request.state.org_id
        if hasattr(request.state, "org_id")
        else _org_id(request.headers.get("x-org-id"))
    )
    bundler_id = request.headers.get("x-bundler-id", "unknown_bundler")

    try:
        body = await request.json()
        payload = body.get("userOperation") or body
        dry_run = request.query_params.get("dry_run", "false").lower() == "true"

        # 1. Parse and Adapt
        user_op = UserOperationParser.parse(payload)
        chain_id = int(request.headers.get("x-chain-id", "31337"))
        entry_point = request.headers.get(
            "x-entry-point", "0x5FF137D4b0FDCD49DcA30c7CF57E578a026d2789"
        )
        chain_event = UserOperationParser.adapt_to_chain_event(
            user_op,
            entry_point=entry_point,
            chain_id=chain_id,
            bundler_id=bundler_id,
            org_id=org_id,
        )

        # 2. Idempotency and Replay Protection - check early
        try:
            with get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT nonce, decision, risk_score, trace_hash, bundler_id, expires_at, expires_at_block, redeemed "
                    "FROM attestation_log WHERE user_op_hash=?",
                    (chain_event.tx_hash,),
                ).fetchone()
                if row:
                    if not row["redeemed"]:
                        # Idempotency hit: accept if block expiry is still valid, else fallback to legacy wall-clock expiry.
                        now_block = AttestationService.get_current_block()
                        expires_at_block = row["expires_at_block"] or 0
                        expires_at = row["expires_at"] or 0
                        is_valid = (
                            expires_at_block > 0 and now_block <= expires_at_block
                        ) or (expires_at >= time.time())

                        if is_valid:
                            return ApiResponse(
                                status="ok",
                                data={
                                    "attestation": {
                                        "payload": {
                                            "user_op_hash": chain_event.tx_hash,
                                            "decision": row["decision"],
                                            "risk_score": row["risk_score"],
                                            "trace_hash": row["trace_hash"],
                                            "bundler_id": row["bundler_id"]
                                            or bundler_id,
                                            "expires_at": expires_at,
                                            "expires_at_block": expires_at_block,
                                            "nonce": row["nonce"],
                                        },
                                        "signature": "v1=hmac-sha256:cached",
                                        "idempotent_hit": True,
                                    }
                                },
                            )
        except (sqlite3.Error, TypeError, ValueError, KeyError):
            pass  # continue fresh

        AttestationService.check_and_redeem_replay(chain_event.tx_hash)

        # 3. Lookup Fallback Policy and Chain Auth
        fallback_policy = "ESCALATE"
        degraded_policy = "ESCALATE"
        with get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT fallback_policy, on_pramana_degraded, supported_chains "
                "FROM bundler_registry "
                "WHERE bundler_url=? OR org_id=? "
                "ORDER BY CASE WHEN bundler_url=? THEN 0 ELSE 1 END "
                "LIMIT 1",
                (bundler_id, org_id, bundler_id),
            ).fetchone()
            if row:
                fallback_policy = str(row["fallback_policy"] or fallback_policy).upper()
                degraded_policy = str(
                    row["on_pramana_degraded"] or fallback_policy
                ).upper()
                if row["supported_chains"]:
                    allowed_chains = [
                        c.strip() for c in row["supported_chains"].split(",")
                    ]
                    if (
                        str(chain_id) not in allowed_chains
                        and "*" not in allowed_chains
                    ):
                        return ApiResponse(
                            status="error",
                            data={
                                "reason": f"UNAUTHORIZED: Bundler disconnected from chain_id {chain_id}.",
                                "decision": "PAUSE",
                            },
                        )

        # 4. Execute MonitoringCrew (L1 only) via Flow bypass or standard kicks
        # The user requested <500ms p95. Running all L1s synchronously in an executor.
        state = PramanaState(event=chain_event, degraded_mode=DegradedMode.NORMAL)
        flow = PramanaFlow()
        degraded = False
        degraded_reason = ""
        decision_source = "flow_l1"

        try:
            # We want to wait tightly for flow completion.
            # In Phase 2, wait_for handles the 800ms SLA fallback
            result_state = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: flow.kickoff(inputs={"event": chain_event.model_dump()}),
                ),
                timeout=0.8,
            )
            final = (
                result_state
                if isinstance(result_state, PramanaState)
                else (flow.state if hasattr(flow, "state") else state)
            )

            # The action is final.action.
            action_raw = getattr(final.action, "value", final.action)
            action = str(action_raw).upper()
            score = final.risk_score
            l1_results = [r.model_dump() for r in getattr(flow, "l1_results", [])]

        except asyncio.TimeoutError:
            action = degraded_policy
            score = 0.5
            l1_results = []
            degraded = True
            degraded_reason = "pipeline_timeout_800ms"
            decision_source = "timeout_degraded_policy"
            logger.warning(
                "Bundler validation timeout for OPHash %s. Returning degraded policy %s.",
                chain_event.tx_hash,
                action,
            )

        # 5. Save to user_operations DB
        def _save_db():
            if dry_run:
                return
            try:
                with get_sqlite_conn() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO user_operations (user_op_hash, sender, entry_point, paymaster, nonce_key, nonce_sequence, call_gas_limit, verification_gas_limit, pre_verification_gas, decision, risk_score, trace_hash, bundler_id, chain_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                        (
                            chain_event.tx_hash,
                            user_op.sender,
                            None,
                            user_op.paymaster or user_op.paymasterAndData,
                            user_op.nonce_key,
                            user_op.nonce_sequence,
                            user_op.callGasLimit,
                            user_op.verificationGasLimit,
                            user_op.preVerificationGas,
                            action,
                            score,
                            chain_event.tx_hash,
                            bundler_id,
                            chain_id,
                        ),
                    )
            except (sqlite3.Error, TypeError, ValueError, KeyError) as e:
                logger.error("Failed to log userOp: %s", e)

        bg_tasks.add_task(_save_db)

        # 6. Sign and Return
        if not dry_run and not degraded:
            attestation = AttestationService.sign(
                user_op_hash=chain_event.tx_hash,
                decision=action,
                risk_score=score,
                trace_hash=chain_event.tx_hash,
                bundler_id=bundler_id,
            )
        elif dry_run:
            attestation = {
                "payload": {"decision": action, "risk_score": score, "dry_run": True},
                "signature": "unsigned",
            }
        else:
            attestation = None

        return ApiResponse(
            status="ok",
            data={
                "decision": action,
                "risk_score": score,
                "degraded": degraded,
                "degraded_reason": degraded_reason,
                "decision_source": decision_source,
                "attestation": attestation,
                "l1_results": l1_results,
            },
        )

    except MalformedUserOpError as e:
        return ApiResponse(status="error", data={"reason": str(e), "decision": "PAUSE"})
    except AttestationReplayError as e:
        return ApiResponse(status="error", data={"reason": str(e), "decision": "PAUSE"})
    except (RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError, OSError) as e:
        # 5xx Fallback
        logger.error("Validation crash: %s", e)
        return ApiResponse(
            status="error",
            data={"reason": "Internal Server Error", "decision": "ESCALATE"},
        )


class ConfirmRequest(BaseModel):
    attestation_nonce: str
    tx_hash: str


@app.post("/bundler/confirm", response_model=ApiResponse)
async def confirm_user_op(req: ConfirmRequest):
    """Marks an attestation as fully consumed on L1 to finalize replay protection."""
    from core.attestation import AttestationService

    try:
        with get_sqlite_conn() as conn:
            row = conn.execute(
                "SELECT user_op_hash FROM attestation_log WHERE nonce=?",
                (req.attestation_nonce,),
            ).fetchone()
            if row:
                user_op_hash = row["user_op_hash"]
                AttestationService.mark_redeemed(user_op_hash)
                # optionally hook `tx_hash` to DB
                return ApiResponse(
                    status="ok", data={"redeemed": True, "user_op_hash": user_op_hash}
                )
        return ApiResponse(
            status="error", data={"reason": "attestation_nonce not found"}
        )
    except (sqlite3.Error, RuntimeError, TypeError, ValueError, KeyError) as e:
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
        result_metadata = getattr(result, "metadata", {})
        matched_patterns = (
            result_metadata.get("matched_patterns", [])
            if isinstance(result_metadata, dict)
            else []
        )

        allowed = (not result.flagged) or (result.score < threshold)
        return ApiResponse(
            status="ok",
            data={
                "allowed": allowed,
                "score": result.score,
                "reason": result.reason,
                "patterns": matched_patterns,
                "threshold": threshold,
                "fail_mode": fail_mode,
            },
        )
    except (RuntimeError, TypeError, ValueError) as e:
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
    final = (
        result_state
        if isinstance(result_state, PramanaState)
        else (flow.state if hasattr(flow, "state") else state)
    )

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
