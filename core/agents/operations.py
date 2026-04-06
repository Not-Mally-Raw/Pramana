"""
core/agents/operations.py — Operational anomaly L1 agent.
Checks: action loop/drain detection, balance drain, rapid repeat actions.
Pass 1: DuckDB rolling window query. <30ms target.
Pass 2 (borderline): recount with tighter window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
        profile = state.agent_profile or "generic"
        thresholds = s.AGENT_PROFILE_THRESHOLDS.get(
            profile, s.AGENT_PROFILE_THRESHOLDS["generic"]
        )

        issues: list[str] = []
        score = 0.0
        meta: dict[str, Any] = {
            "profile": profile,
            "thresholds_applied": thresholds,
        }

        # ── Action frequency (loop detection) by profile ──────────────────────
        actions_per_min = self._count_recent_actions(
            event.agent_id, event.org_id, window_s=60, reference_ts=event.timestamp
        )
        action_loop_threshold = thresholds["ACTION_LOOP_PER_MIN"]
        meta["actions_per_min"] = actions_per_min
        if actions_per_min >= action_loop_threshold:
            issues.append(
                f"action loop: {actions_per_min} actions/min (threshold {action_loop_threshold})"
            )
            score = max(
                score, min(0.5 + (actions_per_min - action_loop_threshold) * 0.05, 1.0)
            )

        # ── Balance drain detection by profile ────────────────────────────────
        value_eth = event.value_wei / 1e18 if event.value_wei else 0.0
        recent_eth_out = self._sum_recent_value(
            event.agent_id, event.org_id, window_s=300, reference_ts=event.timestamp
        )
        balance_drain_threshold = thresholds["BALANCE_DRAIN_PCT"]
        meta["recent_eth_out_5min"] = round(recent_eth_out, 6)
        meta["current_value_eth"] = round(value_eth, 6)
        meta["balance_drain_threshold"] = balance_drain_threshold

        if recent_eth_out > balance_drain_threshold:
            issues.append(
                f"balance drain: {recent_eth_out:.4f} ETH in 5min (threshold {balance_drain_threshold})"
            )
            score = max(score, 0.75)

        # ── Same action repeated (short loop variant) ──────────────────────
        repeat_count = self._count_same_action(
            event.agent_id,
            event.org_id,
            event.action,
            window_s=120,
            reference_ts=event.timestamp,
        )
        meta["action_repeat_2min"] = repeat_count
        if repeat_count >= 3:
            issues.append(f"repeated action '{event.action}' {repeat_count}x in 2min")
            score = max(score, 0.65 + min(repeat_count * 0.05, 0.3))

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "operations nominal"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(
        self, event: ChainEvent, state: PramanaState, first: L1Result
    ) -> L1Result:
        """Tighter window recheck (30s instead of 60s) to confirm loop."""
        actions_tight = self._count_recent_actions(
            event.agent_id, event.org_id, window_s=30, reference_ts=event.timestamp
        )
        meta = dict(first.metadata)
        meta["actions_per_30s"] = actions_tight
        if actions_tight >= 3:
            return self._make_result(
                self.name,
                True,
                min(first.score + 0.10, 1.0),
                first.reason + f" (confirmed: {actions_tight} in 30s)",
                meta,
                pass_count=2,
            )
        return self._make_result(
            self.name,
            first.flagged,
            max(first.score - 0.05, 0.0),
            first.reason + " (rate ok on recheck)",
            meta,
            pass_count=2,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _count_recent_actions(
        agent_addr: str, org_id: str, window_s: int, reference_ts: float
    ) -> int:
        try:
            from core.db import get_sqlite_conn

            threshold = (
                datetime.fromtimestamp(float(reference_ts), timezone.utc)
                - timedelta(seconds=int(window_s))
            ).strftime("%Y-%m-%d %H:%M:%S")

            with get_sqlite_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM events
                       WHERE agent_addr=? AND org_id=?
                         AND created_at >= ?""",
                    (agent_addr, org_id, threshold),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    @staticmethod
    def _count_same_action(
        agent_addr: str,
        org_id: str,
        action: str,
        window_s: int,
        reference_ts: float,
    ) -> int:
        try:
            from core.db import get_sqlite_conn

            threshold = (
                datetime.fromtimestamp(float(reference_ts), timezone.utc)
                - timedelta(seconds=int(window_s))
            ).strftime("%Y-%m-%d %H:%M:%S")

            with get_sqlite_conn() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) FROM events
                       WHERE agent_addr=? AND org_id=? AND action=?
                         AND created_at >= ?""",
                    (agent_addr, org_id, action, threshold),
                ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    @staticmethod
    def _sum_recent_value(
        agent_addr: str, org_id: str, window_s: int, reference_ts: float
    ) -> float:
        """Sum recent value_wei for balance drain detection.

        Fix 7: The old code used r[0].isdigit() which fails for negative
        numbers, decimal strings, large wei values, and non-string types.
        Replaced with try/float for robust numeric parsing.
        """
        try:
            from core.db import get_sqlite_conn

            threshold = (
                datetime.fromtimestamp(float(reference_ts), timezone.utc)
                - timedelta(seconds=int(window_s))
            ).strftime("%Y-%m-%d %H:%M:%S")

            with get_sqlite_conn() as conn:
                rows = conn.execute(
                    """SELECT value_wei FROM events
                       WHERE agent_addr=? AND org_id=?
                         AND created_at >= ?""",
                    (agent_addr, org_id, threshold),
                ).fetchall()
            total_wei = 0
            for r in rows:
                if r[0] is None:
                    continue
                try:
                    total_wei += int(float(r[0]))
                except (ValueError, TypeError):
                    continue
            return total_wei / 1e18
        except Exception:
            return 0.0
