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
import time
import uuid
from typing import List, Optional

import httpx

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

    Liquidation time estimate uses 3% hourly volatility assumption
    (conservative for ETH/major assets, may underestimate for altcoins).
    """

    HOURLY_VOLATILITY = 0.03  # 3% per hour — conservative ETH estimate

    def classify_position(
        self,
        pos: OpenPosition,
        rpc_url: str,
    ) -> PositionClassification:
        """Classify a single position by liquidation risk."""
        if pos.position_type in (PositionType.LENDING, PositionType.COLLATERAL, PositionType.PERP):
            return self._classify_leveraged(pos, rpc_url)
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

        hours = self._estimate_hours_to_liquidation(hf)

        if hf < 1.05 and not pos.is_finalized:
            return PositionClassification(
                position=pos,
                risk_level=PositionRisk.WARNING,
                hours_to_liquidation=hours,
                suggested_action=SuggestedAction.REDUCE,
                reasoning=(
                    f"Health factor {hf:.3f} is low, but position is not finalized yet. "
                    "Classified as recoverable under proactive fee/bundle strategy."
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

    def _estimate_hours_to_liquidation(self, health_factor: float) -> float:
        """
        Rough estimate: buffer / hourly_volatility.
        Buffer = (hf - 1.0) / hf as fraction of collateral value.
        At 3% hourly vol: hf=1.05 -> ~1.7h, hf=1.10 -> ~3.2h, hf=1.20 -> ~6.7h
        """
        if health_factor <= 1.0:
            return 0.0
        buffer = (health_factor - 1.0) / health_factor
        return buffer / self.HOURLY_VOLATILITY

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
                aave_v3_pool = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
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

    def generate_alert(
        self,
        agent_addr: str,
        org_id: str,
        trace_hash: str,
        positions: List[OpenPosition],
        rpc_url: str,
    ) -> RecoveryAlert:
        """
        Generate a complete RecoveryAlert for all open positions.
        Called immediately when an agent transitions to PAUSED.
        Must complete within 5 seconds — RPC calls have 2s timeout each.
        """
        classifications = [self.classify_position(pos, rpc_url) for pos in positions]

        critical = [c for c in classifications if c.risk_level == PositionRisk.CRITICAL]
        warning = [c for c in classifications if c.risk_level == PositionRisk.WARNING]
        safe = [c for c in classifications if c.risk_level == PositionRisk.SAFE]

        total_at_risk = sum(c.position.current_usd_value for c in critical + warning)

        return RecoveryAlert(
            alert_id=str(uuid.uuid4()),
            agent_addr=agent_addr,
            org_id=org_id,
            triggered_by_trace=trace_hash,
            pause_timestamp=int(time.time()),
            classifications=classifications,
            total_usd_at_risk=total_at_risk,
            critical_count=len(critical),
            warning_count=len(warning),
            safe_count=len(safe),
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


def _fetch_open_positions(agent_addr: str, org_id: str) -> List[OpenPosition]:
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

    positions = []
    for row in rows:
        positions.append(
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
        )
    return positions


def _get_operator_webhook(org_id: str) -> str:
    """Fetch operator webhook URL from bundler_registry."""
    from core.db import get_sqlite_conn

    with get_sqlite_conn() as conn:
        row = conn.execute(
            "SELECT webhook_url FROM bundler_registry WHERE org_id=? LIMIT 1",
            (org_id,),
        ).fetchone()
    return row[0] if row and row[0] else ""
