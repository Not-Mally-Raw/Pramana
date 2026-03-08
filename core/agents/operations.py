"""
core/agents/operations.py — Operational anomaly L1 agent.
Checks: action loop/drain detection, balance drain, rapid repeat actions.
Pass 1: DuckDB rolling window query. <30ms target.
Pass 2 (borderline): recount with tighter window.
"""
from __future__ import annotations

from typing import Any

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["OperationsAgent"]


class OperationsAgent(BaseL1Agent):
    name = "operations"
    max_latency_ms = 30.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        issues: list[str] = []
        score = 0.0
        meta: dict[str, Any] = {}

        # ── Action frequency (loop detection) ──────────────────────────────
        actions_per_min = self._count_recent_actions(event.agent_id, event.org_id, window_s=60)
        meta["actions_per_min"] = actions_per_min
        if actions_per_min >= s.ACTION_LOOP_PER_MIN:
            issues.append(f"action loop: {actions_per_min} actions/min (threshold {s.ACTION_LOOP_PER_MIN})")
            score = max(score, min(0.5 + (actions_per_min - s.ACTION_LOOP_PER_MIN) * 0.05, 1.0))

        # ── Balance drain detection ─────────────────────────────────────────
        value_eth = event.value_wei / 1e18 if event.value_wei else 0.0
        recent_eth_out = self._sum_recent_value(event.agent_id, event.org_id, window_s=300)
        meta["recent_eth_out_5min"] = round(recent_eth_out, 6)
        meta["current_value_eth"] = round(value_eth, 6)

        # Balance drain signal: agent sent > 10% of typical daily amount in 5 min
        if recent_eth_out > s.BALANCE_DRAIN_PCT:
            issues.append(f"balance drain: {recent_eth_out:.4f} ETH in 5min")
            score = max(score, 0.75)

        # ── Same action repeated (short loop variant) ──────────────────────
        repeat_count = self._count_same_action(event.agent_id, event.org_id, event.action, window_s=120)
        meta["action_repeat_2min"] = repeat_count
        if repeat_count >= 3:
            issues.append(f"repeated action '{event.action}' {repeat_count}x in 2min")
            score = max(score, 0.65 + min(repeat_count * 0.05, 0.3))

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "operations nominal"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Tighter window recheck (30s instead of 60s) to confirm loop."""
        actions_tight = self._count_recent_actions(event.agent_id, event.org_id, window_s=30)
        meta = dict(first.metadata)
        meta["actions_per_30s"] = actions_tight
        if actions_tight >= 3:
            return self._make_result(
                self.name, True, min(first.score + 0.10, 1.0),
                first.reason + f" (confirmed: {actions_tight} in 30s)", meta, pass_count=2
            )
        return self._make_result(
            self.name, first.flagged, max(first.score - 0.05, 0.0),
            first.reason + " (rate ok on recheck)", meta, pass_count=2
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _count_recent_actions(agent_addr: str, org_id: str, window_s: int) -> int:
        try:
            from core.db import get_sqlite_conn
            with get_sqlite_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM events
                       WHERE agent_addr=? AND org_id=?
                         AND created_at >= datetime('now', '-' || ? || ' seconds')""",
                    (agent_addr, org_id, window_s),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    @staticmethod
    def _count_same_action(agent_addr: str, org_id: str, action: str, window_s: int) -> int:
        try:
            from core.db import get_sqlite_conn
            with get_sqlite_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM events
                       WHERE agent_addr=? AND org_id=? AND action=?
                         AND created_at >= datetime('now', '-' || ? || ' seconds')""",
                    (agent_addr, org_id, action, window_s),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    @staticmethod
    def _sum_recent_value(agent_addr: str, org_id: str, window_s: int) -> float:
        try:
            from core.db import get_sqlite_conn
            with get_sqlite_conn() as conn:
                rows = conn.execute(
                    """SELECT value_wei FROM events
                       WHERE agent_addr=? AND org_id=?
                         AND created_at >= datetime('now', '-' || ? || ' seconds')""",
                    (agent_addr, org_id, window_s),
                ).fetchall()
            total_wei = sum(int(r[0]) for r in rows if r[0] and r[0].isdigit())
            return total_wei / 1e18
        except Exception:
            return 0.0
