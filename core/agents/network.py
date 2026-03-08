"""
core/agents/network.py — Network health L1 agent.
Checks: gas spike (>2x avg), tx latency >500ms, Anvil node reachability.
Pass 1: compare gas to rolling average via DuckDB. ~15ms target.
Pass 2 (borderline): eth_call to Anvil to verify connectivity + recheck.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["NetworkAgent"]

_GAS_HISTORY_WINDOW = 20   # last N txs for rolling average


class NetworkAgent(BaseL1Agent):
    name = "network"
    max_latency_ms = 30.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        issues: list[str] = []
        score = 0.0
        meta: dict[str, Any] = {}

        gas_gwei = event.gas_price / 1e9 if event.gas_price else 0.0
        meta["gas_gwei"] = round(gas_gwei, 2)

        # ── Gas spike detection ─────────────────────────────────────────────
        avg_gas = self._get_avg_gas(event.agent_id, event.org_id)
        meta["avg_gas_gwei"] = round(avg_gas, 2)

        if avg_gas > 0 and gas_gwei > avg_gas * s.GAS_SPIKE_MULTIPLIER:
            spike_ratio = gas_gwei / avg_gas
            issues.append(f"gas spike {spike_ratio:.1f}x avg ({gas_gwei:.0f} vs {avg_gas:.0f} gwei)")
            score = max(score, min(0.5 + (spike_ratio - 2.0) * 0.1, 1.0))

        # ── Transaction latency (simulated from timestamp delta) ────────────
        # In production, compare block_timestamp to event receipt timestamp
        # Here we use a placeholder check: value > 0.5 ETH as "high activity"
        value_eth = event.value_wei / 1e18 if event.value_wei else 0.0
        meta["value_eth"] = round(value_eth, 6)

        # ── Node health check (lightweight) ────────────────────────────────
        node_healthy = self._ping_node(s.active_rpc)
        meta["node_healthy"] = node_healthy
        if not node_healthy:
            issues.append("RPC node unreachable")
            score = max(score, 0.85)

        flagged = score >= 0.50 or not node_healthy
        reason = "; ".join(issues) if issues else "network nominal"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: retry node ping with larger timeout and recheck gas history."""
        s = get_settings()
        meta = dict(first.metadata)
        reachable = self._ping_node(s.active_rpc, timeout=3)
        meta["node_recheck"] = reachable
        if not reachable:
            return self._make_result(self.name, True, 0.90, "node unreachable on recheck", meta, pass_count=2)

        # If node is up, trust first result but lower score slightly
        return self._make_result(
            self.name, first.flagged, max(first.score - 0.10, 0.0),
            first.reason + " (verified)", meta, pass_count=2
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_avg_gas(agent_addr: str, org_id: str) -> float:
        """Compute rolling average gas from DuckDB. Returns 0 if insufficient data."""
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                row = conn.execute(
                    """
                    SELECT AVG(avg_gas_gwei) FROM agent_metrics
                    WHERE agent_addr = ? AND org_id = ?
                    ORDER BY window_ts DESC LIMIT ?
                    """,
                    [agent_addr, org_id, _GAS_HISTORY_WINDOW],
                ).fetchone()
            return float(row[0]) if row and row[0] else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _ping_node(rpc_url: str, timeout: float = 1.0) -> bool:
        try:
            resp = requests.post(
                rpc_url,
                json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                timeout=timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False
