from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from core.config.settings import get_settings


@dataclass
class DateWindow:
    start: datetime
    end: datetime


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _sqlite_path() -> str:
    return get_settings().SQLITE_PATH


def _duckdb_path() -> str:
    return get_settings().DUCKDB_PATH


def parse_window(choice: str, custom_start=None, custom_end=None) -> DateWindow:
    now = _now_utc()
    if choice == "Last Day":
        return DateWindow(start=now - timedelta(days=1), end=now)
    if choice == "Last 7D":
        return DateWindow(start=now - timedelta(days=7), end=now)
    if choice == "Last 1M":
        return DateWindow(start=now - timedelta(days=30), end=now)

    if custom_start and custom_end:
        start = datetime.combine(custom_start, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(custom_end, datetime.max.time(), tzinfo=timezone.utc)
        return DateWindow(start=start, end=end)

    return DateWindow(start=now - timedelta(days=7), end=now)


def _in_window(ts: str | None, window: DateWindow) -> bool:
    dt = _safe_parse_ts(ts)
    if not dt:
        return False
    return window.start <= dt <= window.end


def _sqlite_query(query: str, params: tuple[Any, ...]) -> list[dict]:
    path = _sqlite_path()
    if not Path(path).exists():
        return []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _duck_query(query: str, params: list[Any] | None = None) -> list[dict]:
    path = _duckdb_path()
    if not Path(path).exists():
        return []
    conn = duckdb.connect(path)
    try:
        cur = conn.execute(query, params or [])
        res = cur.fetchall()
        cols = [d[0] for d in (cur.description or [])]
        if not cols:
            return []
        return [dict(zip(cols, row)) for row in res]
    except Exception:
        return []
    finally:
        conn.close()


def get_agents(org_id: str) -> list[dict]:
    return _sqlite_query(
        """
        SELECT agent_addr, did, risk_tier, paused, reputation, updated_at
        FROM agents WHERE org_id=? ORDER BY updated_at DESC
        """,
        (org_id,),
    )


def get_traces(org_id: str, limit: int = 1000) -> list[dict]:
    return _sqlite_query(
        """
        SELECT flow_id, agent_addr, trace_hash, dag_json, flag_count, risk_score, council_action, created_at
        FROM traces WHERE org_id=? ORDER BY created_at DESC LIMIT ?
        """,
        (org_id, int(limit)),
    )


def get_events(org_id: str, limit: int = 2000) -> list[dict]:
    return _sqlite_query(
        """
        SELECT org_id, agent_addr, tx_hash, action, network, created_at
        FROM events WHERE org_id=? ORDER BY created_at DESC LIMIT ?
        """,
        (org_id, int(limit)),
    )


def get_council_decisions(org_id: str, limit: int = 1000) -> list[dict]:
    return _duck_query(
        """
        SELECT flow_id, agent_addr, decided_at, approved, risk_score, confidence, action, reason
        FROM council_decisions WHERE org_id=? ORDER BY decided_at DESC LIMIT ?
        """,
        [org_id, int(limit)],
    )


def get_agent_metrics(org_id: str, limit: int = 2000) -> list[dict]:
    return _duck_query(
        """
        SELECT agent_addr, window_ts, tx_count, flag_count, avg_gas_gwei, avg_risk, success_pct
        FROM agent_metrics WHERE org_id=? ORDER BY window_ts DESC LIMIT ?
        """,
        [org_id, int(limit)],
    )


def build_overview_metrics(agents: list[dict], traces: list[dict], window: DateWindow) -> dict:
    window_traces = [t for t in traces if _in_window(t.get("created_at"), window)]
    total_agents = len(agents)
    active_agents = sum(1 for a in agents if not bool(a.get("paused")))
    total_executions = len(window_traces)
    successful = sum(1 for t in window_traces if "PASS" in str(t.get("council_action", "")).upper())
    success_rate = (successful / total_executions) * 100 if total_executions else 0.0
    return {
        "total_agents": total_agents,
        "active_agents": active_agents,
        "total_executions": total_executions,
        "success_rate": success_rate,
    }


def _status_for_trace(trace: dict) -> str:
    action = str(trace.get("council_action", "")).upper()
    flags = int(trace.get("flag_count") or 0)
    if "PASS" in action and flags == 0:
        return "completed"
    if "PAUSE" in action or flags > 0:
        return "failed"
    return "started"


def build_runs_over_time(traces: list[dict], window: DateWindow) -> pd.DataFrame:
    rows = []
    for t in traces:
        ts = _safe_parse_ts(t.get("created_at"))
        if not ts or not (window.start <= ts <= window.end):
            continue
        rows.append({"date": ts.date().isoformat(), "status": _status_for_trace(t)})
    if not rows:
        return pd.DataFrame({"date": [], "started": [], "failed": [], "completed": []})
    df = pd.DataFrame(rows)
    agg = df.groupby(["date", "status"]).size().unstack(fill_value=0).reset_index()
    for col in ["started", "failed", "completed"]:
        if col not in agg.columns:
            agg[col] = 0
    return agg.sort_values("date")


def build_token_cost_trend(traces: list[dict], window: DateWindow) -> pd.DataFrame:
    rows = []
    for t in traces:
        ts = _safe_parse_ts(t.get("created_at"))
        if not ts or not (window.start <= ts <= window.end):
            continue
        dag = t.get("dag_json")
        token_estimate = 0
        if dag:
            try:
                parsed = json.loads(dag)
                nodes = parsed.get("nodes", {})
                token_estimate = sum(int((n.get("metadata") or {}).get("token_usage", 0)) for n in nodes.values())
            except Exception:
                token_estimate = 0
        if token_estimate <= 0:
            token_estimate = max(80, len(str(dag or "")) // 8)
        rows.append({
            "date": ts.date().isoformat(),
            "tokens": token_estimate,
            "runs": 1,
            "cost_usd": token_estimate * 0.0000025,
        })
    if not rows:
        return pd.DataFrame({"date": [], "tokens": [], "runs": [], "cost_usd": []})
    df = pd.DataFrame(rows)
    return df.groupby("date", as_index=False).sum().sort_values("date")


def build_tool_calls_chart(traces: list[dict], window: DateWindow) -> pd.DataFrame:
    rows = []
    for t in traces:
        ts = _safe_parse_ts(t.get("created_at"))
        if not ts or not (window.start <= ts <= window.end):
            continue
        tool_calls = 0
        dag = t.get("dag_json")
        if dag:
            try:
                parsed = json.loads(dag)
                tool_calls = len(parsed.get("edges", []))
            except Exception:
                tool_calls = 0
        rows.append({"date": ts.date().isoformat(), "tool_calls": tool_calls, "runs": 1})
    if not rows:
        return pd.DataFrame({"date": [], "tool_calls": [], "runs": []})
    return pd.DataFrame(rows).groupby("date", as_index=False).sum().sort_values("date")


def build_error_rate(traces: list[dict], window: DateWindow) -> pd.DataFrame:
    rows = []
    for t in traces:
        ts = _safe_parse_ts(t.get("created_at"))
        if not ts or not (window.start <= ts <= window.end):
            continue
        rows.append({
            "date": ts.date().isoformat(),
            "error": 1 if int(t.get("flag_count") or 0) > 0 else 0,
            "runs": 1,
        })
    if not rows:
        return pd.DataFrame({"date": [], "error_rate": []})
    df = pd.DataFrame(rows).groupby("date", as_index=False).sum()
    df["error_rate"] = (df["error"] / df["runs"]) * 100
    return df[["date", "error_rate"]].sort_values("date")


def build_recent_agents_table(agents: list[dict], traces: list[dict]) -> pd.DataFrame:
    rows = []
    trace_by_agent: dict[str, list[dict]] = {}
    for t in traces:
        trace_by_agent.setdefault(t.get("agent_addr", ""), []).append(t)

    for a in agents:
        addr = a.get("agent_addr", "")
        agent_traces = trace_by_agent.get(addr, [])
        total = len(agent_traces)
        succ = sum(1 for t in agent_traces if "PASS" in str(t.get("council_action", "")).upper())
        success_rate = round((succ / total) * 100, 2) if total else 0.0
        last_execution = agent_traces[0].get("created_at") if agent_traces else "-"
        rows.append(
            {
                "Agent Name": (a.get("did") or "agent")[:20],
                "Agent ID": addr,
                "Status": "Active" if not bool(a.get("paused")) else "Paused",
                "Last Execution": last_execution,
                "Success Rate": success_rate,
            }
        )

    return pd.DataFrame(rows)


def get_agent_detail(agent_id: str, org_id: str) -> dict:
    traces = [t for t in get_traces(org_id, limit=1500) if t.get("agent_addr") == agent_id]
    events = [e for e in get_events(org_id, limit=2000) if e.get("agent_addr") == agent_id]
    decisions = [d for d in get_council_decisions(org_id, limit=1500) if d.get("agent_addr") == agent_id]

    total_exec = len(traces)
    successes = sum(1 for t in traces if "PASS" in str(t.get("council_action", "")).upper())
    success_rate = round((successes / total_exec) * 100, 2) if total_exec else 0.0

    durations = []
    for t in traces:
        dag = t.get("dag_json")
        if dag:
            try:
                nodes = (json.loads(dag).get("nodes") or {}).values()
                total_ms = sum(float((n.get("metadata") or {}).get("latency_ms", 0.0)) for n in nodes)
                if total_ms > 0:
                    durations.append(total_ms / 1000.0)
            except Exception:
                continue

    avg_exec_time = round(sum(durations) / len(durations), 3) if durations else 0.0
    last_execution = traces[0].get("created_at") if traces else "-"
    deployment_networks = sorted({e.get("network") or "unknown" for e in events})

    return {
        "metrics": {
            "total_executions": total_exec,
            "success_rate": success_rate,
            "avg_execution_time": avg_exec_time,
            "last_execution": last_execution,
        },
        "traces": traces,
        "events": events,
        "decisions": decisions,
        "deployment_networks": deployment_networks,
    }


def build_workflow_waterfall(traces: list[dict]) -> pd.DataFrame:
    step_stats: dict[str, dict[str, float]] = {}
    for trace in traces[:120]:
        dag = trace.get("dag_json")
        if not dag:
            continue
        try:
            nodes = (json.loads(dag).get("nodes") or {}).values()
        except Exception:
            continue
        for node in nodes:
            action = str(node.get("action") or "Step")
            item = step_stats.setdefault(action, {"count": 0.0, "score": 0.0, "duration": 0.0})
            item["count"] += 1
            item["score"] += float(node.get("score") or 0.0)
            item["duration"] += float((node.get("metadata") or {}).get("latency_ms", 0.0))

    rows = []
    for step, stats in step_stats.items():
        count = stats["count"] or 1.0
        reliability = max(0.0, min(100.0, (stats["score"] / count) * 100))
        rows.append(
            {
                "step": step,
                "reliability": reliability,
                "duration_ms": stats["duration"] / count,
                "trace_link": "replay",
            }
        )
    if not rows:
        return pd.DataFrame({"step": [], "reliability": [], "duration_ms": [], "trace_link": []})
    return pd.DataFrame(rows).sort_values("reliability", ascending=False)


def _issue_severity(risk_score: float, flags: int) -> tuple[str, int]:
    base = max(risk_score * 100, flags * 10)
    if base >= 85:
        return "Critical", 4
    if base >= 65:
        return "High", 3
    if base >= 40:
        return "Medium", 2
    return "Low", 1


def _issue_resolution(action: str, flags: int, risk_score: float) -> str:
    u = action.upper()
    if "PAUSE" in u:
        return "Review policy thresholds, run replay, and require council re-approval before resume."
    if flags >= 3:
        return "Investigate repeated anomaly pattern; tighten guard rules and increase eval sampling."
    if risk_score >= 0.7:
        return "Switch to fail-closed mode and run ad-hoc red teaming before next deployment."
    return "Monitor trend and tune thresholds only if recurrence persists."


def build_issues_table(traces: list[dict], decisions: list[dict]) -> pd.DataFrame:
    rows = []
    decisions_by_flow = {d.get("flow_id"): d for d in decisions}
    for t in traces[:200]:
        flags = int(t.get("flag_count") or 0)
        risk = float(t.get("risk_score") or 0.0)
        action = str(t.get("council_action") or "")
        if flags <= 0 and "PAUSE" not in action.upper() and risk < 0.45:
            continue
        severity, depth = _issue_severity(risk, flags)
        where = "Policy engine" if "PAUSE" in action.upper() else "Trace pipeline"
        decision = decisions_by_flow.get(t.get("flow_id"), {})
        reason = decision.get("reason") or "Signal threshold exceeded"
        rows.append(
            {
                "flow_id": t.get("flow_id"),
                "agent_addr": t.get("agent_addr"),
                "issue": reason,
                "where": where,
                "severity": severity,
                "depth": depth,
                "risk_score": round(risk, 4),
                "flags": flags,
                "resolve": _issue_resolution(action, flags, risk),
                "created_at": t.get("created_at"),
            }
        )

    if not rows:
        return pd.DataFrame(
            {"flow_id": [], "agent_addr": [], "issue": [], "where": [], "severity": [], "depth": [], "risk_score": [], "flags": [], "resolve": [], "created_at": []}
        )
    return pd.DataFrame(rows).sort_values(["depth", "risk_score"], ascending=False)


def monitoring_systems_status() -> list[dict]:
    settings = get_settings()
    sqlite_ok = Path(settings.SQLITE_PATH).exists()
    duck_ok = Path(settings.DUCKDB_PATH).exists()
    chroma_ok = Path(settings.CHROMA_PATH).exists()
    listener_mode = "Enabled" if settings.LISTENER_ENABLED else "Disabled"
    return [
        {"system": "SQLite telemetry", "status": "ok" if sqlite_ok else "degraded", "details": settings.SQLITE_PATH},
        {"system": "DuckDB analytics", "status": "ok" if duck_ok else "degraded", "details": settings.DUCKDB_PATH},
        {"system": "Chroma traces", "status": "ok" if chroma_ok else "degraded", "details": settings.CHROMA_PATH},
        {"system": "WebSocket listener", "status": "ok" if settings.LISTENER_ENABLED else "degraded", "details": listener_mode},
    ]


def deployment_summary(org_id: str) -> dict:
    settings = get_settings()
    events = get_events(org_id, limit=3000)
    networks = sorted({e.get("network") or settings.NETWORK for e in events}) or [settings.NETWORK]
    deploy_regions = ["anvil-local"] if settings.NETWORK == "anvil" else [f"{settings.NETWORK}-rpc"]
    return {
        "networks": networks,
        "regions": deploy_regions,
        "contracts": {
            "orchestrator": settings.ORCHESTRATOR_ADDR or "not_set",
            "guardian": settings.GUARDIAN_ADDR or "not_set",
            "sbt": settings.SBT_ADDR or "not_set",
        },
    }
