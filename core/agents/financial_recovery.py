"""
core/agents/financial_recovery.py

Phase 3B: Recovery alert classification engine.
Invoked automatically when any agent transitions to PAUSED state.
Classifies each open position by liquidation risk and delivers
a structured RecoveryAlert to the operator webhook.

This service NEVER executes position actions. Advisory only.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
import uuid
from typing import Optional

import httpx

from core.config.settings import get_settings

from utils.models import (
    OpenPosition,
    PositionClassification,
    PositionRisk,
    PositionType,
    RecoveryAlert,
    SuggestedAction,
)

logger = logging.getLogger("pramana.financial_recovery")


class FinancialRecoveryService:
    """
    Classifies open positions by liquidation risk when an agent is paused.

    Risk classification thresholds:
    - CRITICAL: health_factor < 1.05 OR hours_to_liquidation < 4
    - WARNING:  health_factor 1.05-1.20 OR hours_to_liquidation 4-24
    - SAFE:     health_factor > 1.20 OR no liquidation risk

    Liquidation time estimate uses dynamic hourly volatility derived from
    recent position health-factor changes, with conservative fallback.
    """

    VOLATILITY_FALLBACK = 0.05  # 5% per hour conservative fallback
    LEVERAGED_TYPES = {
        PositionType.LENDING,
        PositionType.COLLATERAL,
        PositionType.PERP,
    }

    def classify_position(
        self,
        pos: OpenPosition,
        rpc_url: str,
        current_block: int | None = None,
    ) -> PositionClassification:
        """Classify a single position by liquidation risk."""
        if pos.position_type in self.LEVERAGED_TYPES:
            return self._classify_leveraged(pos, rpc_url, current_block=current_block)
        if pos.position_type == PositionType.LP:
            return self._classify_lp(pos)
        return PositionClassification(
            position=pos,
            risk_level=PositionRisk.SAFE,
            hours_to_liquidation=None,
            suggested_action=SuggestedAction.HOLD,
            reasoning="Staking/vault position has no liquidation risk. Monitor for slashing or lock expiry.",
        )

    def _classify_leveraged(
        self,
        pos: OpenPosition,
        rpc_url: str,
        current_block: int | None = None,
    ) -> PositionClassification:
        hf = pos.health_factor

        # Try to fetch live health factor if not cached.
        if hf is None:
            hf = self._fetch_health_factor_safe(pos, rpc_url)

        if hf is None:
            # Cannot determine — conservative treatment.
            return PositionClassification(
                position=pos,
                risk_level=PositionRisk.WARNING,
                hours_to_liquidation=None,
                suggested_action=SuggestedAction.REDUCE,
                reasoning="Health factor unavailable from RPC. Treating as WARNING — reduce exposure.",
            )

        hours = self._estimate_hours_to_liquidation(
            health_factor=hf,
            protocol=pos.protocol,
            asset=pos.asset,
        )

        # Finality-aware confidence: treat as effectively-final once confirmed.
        s = get_settings()
        finality_depth: int | None = None
        if current_block is not None and pos.last_updated_block:
            try:
                finality_depth = max(0, int(current_block) - int(pos.last_updated_block))
            except Exception:
                finality_depth = None
        effectively_final = bool(pos.is_finalized) or (
            finality_depth is not None and finality_depth >= int(s.FINALITY_DEPTH_BLOCKS)
        )

        if hf < 1.05 and not effectively_final:
            depth_hint = (
                f" (depth {finality_depth} < {int(s.FINALITY_DEPTH_BLOCKS)})"
                if finality_depth is not None
                else ""
            )
            return PositionClassification(
                position=pos,
                risk_level=PositionRisk.WARNING,
                hours_to_liquidation=hours,
                suggested_action=SuggestedAction.REDUCE,
                reasoning=(
                    f"Health factor {hf:.3f} is low, but position is not finalized yet. "
                    f"Classified as recoverable under proactive fee/bundle strategy{depth_hint}."
                ),
            )

        if hf < 1.05:
            return PositionClassification(
                position=pos,
                risk_level=PositionRisk.CRITICAL,
                hours_to_liquidation=hours,
                suggested_action=SuggestedAction.CLOSE,
                reasoning=f"Health factor {hf:.3f} — imminent liquidation. Estimated {hours:.1f}h remaining.",
            )
        if hf < 1.20:
            return PositionClassification(
                position=pos,
                risk_level=PositionRisk.WARNING,
                hours_to_liquidation=hours,
                suggested_action=SuggestedAction.REDUCE,
                reasoning=f"Health factor {hf:.3f} — approaching liquidation threshold. Estimated {hours:.1f}h.",
            )
        return PositionClassification(
            position=pos,
            risk_level=PositionRisk.SAFE,
            hours_to_liquidation=None,
            suggested_action=SuggestedAction.HOLD,
            reasoning=f"Health factor {hf:.3f} — adequate buffer. Monitor during recovery.",
        )

    def _classify_lp(self, pos: OpenPosition) -> PositionClassification:
        """LP positions have no liquidation risk but may be out-of-range."""
        return PositionClassification(
            position=pos,
            risk_level=PositionRisk.SAFE,
            hours_to_liquidation=None,
            suggested_action=SuggestedAction.HOLD,
            reasoning="LP position. No liquidation risk. Check if price is within active range.",
        )

    def _estimate_hours_to_liquidation(
        self,
        health_factor: float,
        protocol: str,
        asset: str,
    ) -> float:
        """
        Rough estimate: buffer / hourly_volatility.
        Buffer = (hf - 1.0) / hf as fraction of collateral value.
        Volatility is computed from recent position history and clamped.
        """
        if health_factor <= 1.0:
            return 0.0
        buffer = (health_factor - 1.0) / health_factor
        return buffer / self._compute_dynamic_volatility(protocol=protocol, asset=asset)

    def _compute_dynamic_volatility(
        self,
        protocol: str,
        asset: str,
        lookback_hours: int = 24,
    ) -> float:
        """
        Compute hourly volatility using observed health-factor changes.
        Falls back conservatively when insufficient data is available.
        """
        try:
            from core.db import get_sqlite_conn

            cutoff_ts = int(time.time()) - (lookback_hours * 3600)

            with get_sqlite_conn() as conn:
                rows = conn.execute(
                    """
                    SELECT health_factor
                    FROM open_positions
                    WHERE protocol = ? AND asset = ?
                      AND last_updated_ts > ?
                      AND is_closed = 0
                    ORDER BY last_updated_ts ASC
                    """,
                    (protocol, asset, cutoff_ts),
                ).fetchall()

            hf_values = [float(v) for (v,) in rows if v is not None and float(v) > 0.0]
            if len(hf_values) < 10:
                return self.VOLATILITY_FALLBACK

            changes = [
                abs(curr - prev) / prev
                for prev, curr in zip(hf_values, hf_values[1:])
                if prev > 0.0
            ]
            if not changes:
                return self.VOLATILITY_FALLBACK

            computed = statistics.stdev(changes) if len(changes) > 1 else changes[0]
            # Clamp to avoid unstable near-zero and unrealistic spikes.
            return max(min(computed, 0.50), 0.005)
        except Exception as e:
            logger.warning(
                "Dynamic volatility failed for %s/%s: %s. Using fallback %.3f",
                protocol,
                asset,
                e,
                self.VOLATILITY_FALLBACK,
            )
            return self.VOLATILITY_FALLBACK

    def _fetch_health_factor_safe(
        self,
        pos: OpenPosition,
        rpc_url: str,
    ) -> Optional[float]:
        """
        Attempt to fetch live health factor from protocol via eth_call.
        Returns None on any failure — caller handles conservatively.
        Timeout: 2 seconds. Do not block the pause flow.
        """
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 2}))

            # Aave V3 — getUserAccountData returns healthFactor as last field.
            if "aave" in pos.protocol.lower():
                aave_v3_pool = w3.to_checksum_address(
                    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
                )
                abi = [
                    {
                        "inputs": [{"name": "user", "type": "address"}],
                        "name": "getUserAccountData",
                        "outputs": [
                            {"name": "totalCollateralBase", "type": "uint256"},
                            {"name": "totalDebtBase", "type": "uint256"},
                            {"name": "availableBorrowsBase", "type": "uint256"},
                            {"name": "currentLiquidationThreshold", "type": "uint256"},
                            {"name": "ltv", "type": "uint256"},
                            {"name": "healthFactor", "type": "uint256"},
                        ],
                        "stateMutability": "view",
                        "type": "function",
                    }
                ]
                pool = w3.eth.contract(address=aave_v3_pool, abi=abi)
                result = pool.functions.getUserAccountData(pos.agent_addr).call()
                raw_hf = result[5]  # healthFactor is 18-decimal fixed point.
                return raw_hf / 1e18

        except Exception as e:
            logger.warning("Health factor fetch failed for %s: %s", pos.agent_addr, e)
        return None

    @staticmethod
    def _get_current_block_safe(rpc_url: str) -> int | None:
        """Fetch latest block number with a short timeout.

        Uses JSON-RPC directly to avoid heavyweight client init.
        Returns None on any failure.
        """
        if not rpc_url:
            return None
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_blockNumber",
                "params": [],
            }
            with httpx.Client(timeout=1.5) as client:
                resp = client.post(rpc_url, json=payload)
            if resp.status_code >= 300:
                return None
            data = resp.json()
            block_hex = data.get("result")
            if not isinstance(block_hex, str) or not block_hex.startswith("0x"):
                return None
            return int(block_hex, 16)
        except Exception:
            return None

    def generate_alert(
        self,
        agent_addr: str,
        org_id: str,
        trace_hash: str,
        positions: list[OpenPosition],
        rpc_url: str,
    ) -> RecoveryAlert:
        """
        Generate a complete RecoveryAlert for all open positions.
        Called immediately when an agent transitions to PAUSED.
        Must complete within 5 seconds — RPC calls have 2s timeout each.
        """
        current_block = self._get_current_block_safe(rpc_url)
        classifications = [
            self.classify_position(pos, rpc_url, current_block=current_block)
            for pos in positions
        ]
        critical = warning = safe = 0
        total_at_risk = 0.0
        for cls in classifications:
            if cls.risk_level == PositionRisk.CRITICAL:
                critical += 1
                total_at_risk += cls.position.current_usd_value
            elif cls.risk_level == PositionRisk.WARNING:
                warning += 1
                total_at_risk += cls.position.current_usd_value
            else:
                safe += 1

        return RecoveryAlert(
            alert_id=str(uuid.uuid4()),
            agent_addr=agent_addr,
            org_id=org_id,
            triggered_by_trace=trace_hash,
            pause_timestamp=int(time.time()),
            classifications=classifications,
            total_usd_at_risk=total_at_risk,
            critical_count=critical,
            warning_count=warning,
            safe_count=safe,
            webhook_delivered=False,
            webhook_ts=None,
        )

    async def deliver_webhook(
        self,
        alert: RecoveryAlert,
        webhook_url: str,
    ) -> bool:
        """
        Async webhook delivery with 5s timeout.
        Non-blocking — failure does not affect pause execution.
        """
        if not webhook_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    webhook_url,
                    json=alert.model_dump(),
                    headers={"Content-Type": "application/json"},
                )
                return resp.status_code < 300
        except Exception as e:
            logger.warning("Webhook delivery failed to %s: %s", webhook_url, e)
            return False


def _persist_recovery_alert(alert: RecoveryAlert) -> None:
    """Write RecoveryAlert to SQLite recovery_alerts table."""
    from core.db import get_sqlite_conn

    with get_sqlite_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO recovery_alerts
            (alert_id, agent_addr, org_id, triggered_by_trace,
             pause_timestamp, classifications_json, total_usd_at_risk,
             critical_count, warning_count, safe_count,
             webhook_delivered, webhook_ts, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                alert.alert_id,
                alert.agent_addr,
                alert.org_id,
                alert.triggered_by_trace,
                alert.pause_timestamp,
                json.dumps([c.model_dump() for c in alert.classifications]),
                alert.total_usd_at_risk,
                alert.critical_count,
                alert.warning_count,
                alert.safe_count,
                int(alert.webhook_delivered),
                alert.webhook_ts,
                int(time.time()),
            ),
        )


def _fetch_open_positions(agent_addr: str, org_id: str) -> list[OpenPosition]:
    """Fetch all non-closed positions for an agent from SQLite."""
    from core.db import get_sqlite_conn

    with get_sqlite_conn() as conn:
        rows = conn.execute(
            """
            SELECT position_id, agent_addr, org_id, protocol, position_type,
                   asset, amount_wei, usd_value_at_entry, current_usd_value,
                   health_factor, liquidation_threshold, chain_id,
                   block_entered, last_updated_block, last_updated_ts,
                   is_finalized
            FROM open_positions
            WHERE agent_addr=? AND org_id=? AND is_closed=0
        """,
            (agent_addr, org_id),
        ).fetchall()

    return [
        OpenPosition(
            position_id=row[0],
            agent_addr=row[1],
            org_id=row[2],
            protocol=row[3],
            position_type=PositionType(row[4]),
            asset=row[5],
            amount_wei=int(row[6]),
            usd_value_at_entry=row[7] or 0.0,
            current_usd_value=row[8] or 0.0,
            health_factor=row[9],
            liquidation_threshold=row[10],
            chain_id=row[11],
            block_entered=row[12] or 0,
            last_updated_block=row[13] or 0,
            last_updated_ts=row[14] or 0,
            is_finalized=bool(row[15]),
        )
        for row in rows
    ]


def _get_operator_webhook(org_id: str) -> str:
    """Fetch operator webhook URL from bundler_registry."""
    from core.db import get_sqlite_conn

    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT webhook_url FROM bundler_registry WHERE org_id=? LIMIT 1",
            (org_id,),
        ).fetchone()
    return row[0] if row and row[0] else ""
