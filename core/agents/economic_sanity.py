from __future__ import annotations

import json
import time
from typing import Any

import requests

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["EconomicSanityAgent"]


class EconomicSanityAgent(BaseL1Agent):
    name = "economic_sanity"
    max_latency_ms = 120.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        meta: dict[str, Any] = {}
        issues: list[str] = []
        score = 0.0

        oracle_staleness_s = self._get_oracle_staleness(event.agent_id, event.org_id)
        meta["oracle_staleness_s"] = oracle_staleness_s
        if oracle_staleness_s > 300:
            issues.append(f"oracle_staleness {oracle_staleness_s}s > 300s")
            score = max(score, 0.55)

        oracle_divergence_pct = self._estimate_oracle_divergence(event.agent_id)
        meta["oracle_divergence_pct"] = round(oracle_divergence_pct, 2)
        if oracle_divergence_pct > 15.0:
            issues.append(f"oracle_divergence {oracle_divergence_pct:.1f}% > 15%")
            score = max(score, 0.60)

        liquidity_depth_usd = self._get_liquidity_depth(event.calldata or "")
        meta["liquidity_depth_usd"] = liquidity_depth_usd
        if liquidity_depth_usd < 10000:
            issues.append(f"liquidity_depth ${liquidity_depth_usd} < $10k")
            score = max(score, 0.50)

        max_slippage_bps = self._parse_slippage(event.calldata or "")
        meta["max_slippage_bps"] = max_slippage_bps
        if max_slippage_bps > 5000:
            issues.append(f"max_slippage {max_slippage_bps}bps > 5000bps")
            score = max(score, 0.65)

        position_limit_pct = self._get_position_limit_pct(event.agent_id, event.org_id, event.value_wei)
        meta["position_limit_pct"] = round(position_limit_pct, 2)
        if position_limit_pct > 25.0:
            issues.append(f"position_limit {position_limit_pct:.1f}% > 25%")
            score = max(score, 0.70)

        drawdown_limit_pct = self._get_drawdown_limit_pct(event.agent_id, event.org_id)
        meta["drawdown_limit_pct"] = round(drawdown_limit_pct, 2)
        if drawdown_limit_pct > 50.0:
            issues.append(f"drawdown_limit {drawdown_limit_pct:.1f}% > 50%")
            score = max(score, 0.75)

        mev_risk_score = self._estimate_mev_risk(event.gas_price, event.value_wei)
        meta["mev_risk_score"] = round(mev_risk_score, 2)
        if mev_risk_score > 0.70:
            issues.append(f"mev_risk {mev_risk_score:.2f} > 0.70")
            score = max(score, 0.55)

        pga_score, pga_median_gas, pga_bid_ratio, pga_spike_count = self._check_pga_participation(
            event.agent_id,
            event.org_id,
            event.gas_price,
        )
        meta["pga_participation_score"] = round(pga_score, 3)
        meta["pga_median_gas_price"] = int(pga_median_gas)
        meta["pga_bid_ratio"] = round(pga_bid_ratio, 3)
        meta["pga_spike_count"] = pga_spike_count
        if pga_score > 0.65:
            issues.append(
                f"pga_participation score {pga_score:.2f} (ratio {pga_bid_ratio:.2f}, spikes {pga_spike_count})"
            )
            score = max(score, 0.65)

        token_risk_flags = self._check_token_risk(event.calldata or "")
        meta["token_risk_flags"] = token_risk_flags
        if token_risk_flags > 0:
            issues.append(f"token_risk_flags {token_risk_flags} detected")
            score = max(score, 0.50)

        allowance_change_guard = self._check_allowance_security(event.calldata or "")
        meta["allowance_change_guard"] = allowance_change_guard
        if not allowance_change_guard:
            issues.append("allowance_change_guard failed")
            score = max(score, 0.45)

        expected_value_after_gas = self._compute_expected_value(event.value_wei, event.gas_price)
        meta["expected_value_after_gas"] = round(expected_value_after_gas, 6)
        if expected_value_after_gas < 0:
            issues.append(f"expected_value_after_gas negative: {expected_value_after_gas}")
            score = max(score, 0.80)

        min_notional_usd = self._get_min_notional(event.calldata or "")
        meta["min_notional_usd"] = min_notional_usd
        if min_notional_usd < 100:
            issues.append(f"min_notional ${min_notional_usd} < $100")
            score = max(score, 0.35)

        route_simulation_required = self._check_simulation_requirement(event.action, event.calldata or "")
        meta["route_simulation_required"] = route_simulation_required
        if route_simulation_required and state.degraded_mode.name == "NO_CHAIN":
            issues.append("route_simulation_required but degraded_mode=NO_CHAIN")
            score = max(score, 0.40)

        confidence = max(0.0, 1.0 - (score * 0.3))
        meta["confidence"] = round(confidence, 3)

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "economic_sanity nominal"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: deterministic cross-check using historical data.

        Fix 6: The old code called Groq here, violating the L1 zero-Groq
        invariant and burning rate limit before L2 council could use it.
        Replaced with a deterministic comparison against historical median
        risk from DuckDB — no LLM, no rate limit consumption.
        """
        meta = dict(first.metadata)
        confidence = meta.get("confidence", 0.75)

        if confidence < 0.75:
            # Deterministic cross-check: compare current risk indicators
            # against this agent's historical median from DuckDB
            historical_risk = self._get_historical_median_risk(
                event.agent_id, event.org_id
            )
            meta["historical_median_risk"] = round(historical_risk, 3)
            meta["deterministic_crosscheck"] = True

            if historical_risk > 0 and first.score > historical_risk * 1.5:
                # Current score significantly above historical median → escalate
                return self._make_result(
                    self.name, True, min(first.score + 0.10, 1.0),
                    f"{first.reason} (above historical median {historical_risk:.2f})",
                    meta, pass_count=2
                )
            elif first.score < historical_risk * 0.5:
                # Current score well below historical median → likely safe
                return self._make_result(
                    self.name, first.flagged,
                    max(first.score - 0.10, 0.0),
                    first.reason + " (below historical median)", meta, pass_count=2
                )

        return self._make_result(
            self.name, first.flagged, first.score,
            first.reason + " (confidence high)", meta, pass_count=2
        )

    @staticmethod
    def _get_oracle_staleness(agent_id: str, org_id: str) -> int:
        """Get seconds since last event for this agent.

        Fix 4: The old code queried DuckDB for 'events', but that table only
        exists in SQLite. DuckDB has 'agent_metrics'. Changed to SQLite.
        """
        try:
            from core.db import get_sqlite_conn

            with get_sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT MAX(created_at) FROM events WHERE agent_addr=? AND org_id=?",
                    [agent_id, org_id]
                ).fetchone()
            if row and row[0]:
                delta = (time.time() - row[0]) if isinstance(row[0], (int, float)) else 0
                return int(delta)
        except Exception:
            pass
        return 0

    @staticmethod
    def _estimate_oracle_divergence(agent_id: str) -> float:
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                rows = conn.execute(
                    "SELECT avg_risk FROM agent_metrics WHERE agent_addr=? ORDER BY window_ts DESC LIMIT 5",
                    [agent_id]
                ).fetchall()
            if len(rows) >= 2:
                risks = [r[0] or 0.0 for r in rows]
                avg = sum(risks) / len(risks)
                if avg > 0:
                    deviation = max(risks) - min(risks)
                    return (deviation / avg) * 100.0
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _get_liquidity_depth(calldata: str) -> int:
        try:
            if calldata.startswith("{"):
                obj = json.loads(calldata)
                return int(obj.get("liquidity_depth_usd", 100000))
            if "swap" in calldata.lower():
                return 100000
        except Exception:
            pass
        return 100000

    @staticmethod
    def _parse_slippage(calldata: str) -> int:
        try:
            if calldata.startswith("{"):
                obj = json.loads(calldata)
                return int(obj.get("slippage_bps", 500))
            if "slippage" in calldata.lower():
                import re
                m = re.search(r"(\d{3,4})bps", calldata)
                if m:
                    return int(m.group(1))
        except Exception:
            pass
        return 500

    @staticmethod
    def _get_position_limit_pct(agent_id: str, org_id: str, value_wei: int) -> float:
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                row = conn.execute(
                    "SELECT SUM(CAST(value_wei AS REAL)) FROM events WHERE agent_addr=? AND org_id=? AND created_at >= datetime('now', '-30 days')",
                    [agent_id, org_id]
                ).fetchone()
            total_value = (row[0] or 0.0)
            if total_value > 1e18:
                return (value_wei / total_value) * 100.0
        except Exception:
            pass
        return 5.0

    @staticmethod
    def _get_drawdown_limit_pct(agent_id: str, org_id: str) -> float:
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                rows = conn.execute(
                    "SELECT composite FROM reputation_history WHERE agent_addr=? AND org_id=? ORDER BY recorded_at DESC LIMIT 2",
                    [agent_id, org_id]
                ).fetchall()
            if len(rows) >= 2:
                prev, curr = rows[1][0], rows[0][0]
                if prev > 0:
                    return max(0.0, ((prev - curr) / prev) * 100.0)
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _estimate_mev_risk(gas_price: int, value_wei: int) -> float:
        gas_gwei = gas_price / 1e9 if gas_price else 1.0
        value_eth = value_wei / 1e18 if value_wei else 0.0

        if gas_gwei > 100:
            return min(0.80, 0.30 + (gas_gwei - 100) / 1000.0)
        if value_eth > 10:
            return min(0.70, 0.20 + (value_eth / 100.0))
        return 0.15

    @staticmethod
    def _check_pga_participation(agent_id: str, org_id: str, current_gas_price: int) -> tuple[float, float, float, int]:
        """Estimate PGA participation from gas bidding pressure vs historical median."""
        if current_gas_price <= 0:
            return 0.0, 0.0, 0.0, 0

        try:
            from core.db import get_sqlite_conn

            with get_sqlite_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT gas_price
                    FROM events
                    WHERE agent_addr=?
                      AND org_id=?
                      AND gas_price IS NOT NULL
                      AND gas_price != ''
                    ORDER BY created_at DESC
                    LIMIT 50
                    """,
                    [agent_id, org_id],
                ).fetchall()

            prices: list[float] = []
            for row in rows:
                try:
                    prices.append(float(row[0]))
                except Exception:
                    continue

            if len(prices) < 5:
                return 0.0, 0.0, 0.0, 0

            sorted_prices = sorted(prices)
            mid = len(sorted_prices) // 2
            median_gas = (
                sorted_prices[mid]
                if len(sorted_prices) % 2 == 1
                else (sorted_prices[mid - 1] + sorted_prices[mid]) / 2
            )
            if median_gas <= 0:
                return 0.0, 0.0, 0.0, 0

            bid_ratio = current_gas_price / median_gas
            ratio_score = 0.0
            if bid_ratio > 1.25:
                ratio_score = min(1.0, (bid_ratio - 1.25) / 1.75)

            recent_window = prices[:10]
            spike_count = sum(1 for gas in recent_window if gas >= median_gas * 1.8)
            burst_score = min(1.0, spike_count / 4.0)

            return max(ratio_score, burst_score), median_gas, bid_ratio, spike_count
        except Exception:
            return 0.0, 0.0, 0.0, 0

    @staticmethod
    def _check_token_risk(calldata: str) -> int:
        from core.parsers.calldata import decode_all_calls
        calls = decode_all_calls(calldata)
        if not calls:
            # Fallback to simple substring
            calldata_lower = calldata.lower()
        else:
            # Extract all strings across all batch calls deeply
            strings = []
            for c in calls:
                strings.extend(c.strings)
                strings.append(c.name.lower())
                strings.append(c.protocol.lower())
            calldata_lower = " ".join(strings).lower()
            
        risky_keywords = ["honeypot", "rug", "flash", "rebasing"]
        flags = sum(1 for kw in risky_keywords if kw in calldata_lower)
        return flags

    @staticmethod
    def _check_allowance_security(calldata: str) -> bool:
        from core.parsers.calldata import decode_all_calls
        calls = decode_all_calls(calldata)
        
        # New ABI parsing path
        if calls:
            for call in calls:
                if call.name == "approve":
                    amount = call.params.get("amount", 0)
                    # UINT256_MAX check — blocks infinite approvals
                    if isinstance(amount, int) and amount > 115792089237316195423570985008687907853269984665640564000000000000000000000000:
                        return False
            return True

        # Legacy JSON string path (for test compatibility)
        if "approve" in calldata.lower():
            try:
                import json
                obj = json.loads(calldata) if calldata.startswith("{") else {}
                amount_str = str(obj.get("amount", ""))
                if amount_str == "UINT256_MAX" or amount_str == "2**256-1":
                    return False
            except Exception:
                pass
        return True

    @staticmethod
    def _compute_expected_value(value_wei: int, gas_price: int) -> float:
        gas_cost_wei = gas_price * 21000
        net_value = (value_wei - gas_cost_wei) / 1e18
        return net_value

    @staticmethod
    def _get_min_notional(calldata: str) -> int:
        try:
            if calldata.startswith("{"):
                obj = json.loads(calldata)
                return int(obj.get("min_notional_usd", 1000))
            return 1000
        except Exception:
            pass
        return 1000

    @staticmethod
    def _check_simulation_requirement(action: str, calldata: str) -> bool:
        simulation_required_actions = ["swap", "delegatecall", "multicall"]
        return any(a in action.lower() for a in simulation_required_actions)

    @staticmethod
    def _get_historical_median_risk(agent_id: str, org_id: str) -> float:
        """Get median risk score from agent's recent history in DuckDB.

        Fix 6: Replaces _call_groq_fallback. Purely deterministic — no LLM,
        no rate limit consumption. Uses median of last 20 metric windows.
        """
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                rows = conn.execute(
                    """SELECT avg_risk FROM agent_metrics
                       WHERE agent_addr = ? AND org_id = ?
                       ORDER BY window_ts DESC LIMIT 20""",
                    [agent_id, org_id]
                ).fetchall()
            if rows:
                risks = sorted([r[0] or 0.0 for r in rows])
                mid = len(risks) // 2
                return risks[mid] if len(risks) % 2 else (risks[mid - 1] + risks[mid]) / 2
        except Exception:
            pass
        return 0.0
