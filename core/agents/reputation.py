"""
core/agents/reputation.py — Reputation score aggregator (L2).
Reads DuckDB analytics → computes composite score → writes to SBT via batchUpdateScores.
Formula: (success*40 + roi*20 + uptime*25 + (1000-halluc)*15) / 1000
"""
from __future__ import annotations

from typing import Any

from utils.models import ReputationData

__all__ = ["ReputationAgent"]


class ReputationAgent:
    """
    Aggregates per-agent metrics from DuckDB and pushes updated scores
    to the ReputationSBT contract via batchUpdateScores.
    Non-blocking: writes are fire-and-forget via APScheduler.
    """

    name = "reputation"

    def update_org(self, org_id: str) -> list[ReputationData]:
        """Compute and store reputation scores for all agents in an org."""
        agents = self._list_org_agents(org_id)
        results: list[ReputationData] = []

        for agent_addr in agents:
            score = self._compute_score(agent_addr, org_id)
            if score:
                self._write_sqlite(score)
                self._append_duck_history(score)
                results.append(score)

        if results:
            self._batch_update_sbt(results)

        return results

    # ── Score computation ─────────────────────────────────────────────────────

    @staticmethod
    def _compute_score(agent_addr: str, org_id: str) -> ReputationData | None:
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                row = conn.execute(
                    """
                    SELECT
                        AVG(success_pct)  AS success_rate,
                        AVG(avg_risk)     AS avg_risk,
                        COUNT(*)          AS window_count,
                        SUM(tx_count)     AS total_tx,
                        AVG(flag_count * 1.0 / NULLIF(tx_count, 0)) AS halluc_rate
                    FROM agent_metrics
                    WHERE agent_addr = ? AND org_id = ?
                    """,
                    [agent_addr, org_id],
                ).fetchone()

            if not row or row[2] == 0:
                return None

            success = float(row[0] or 0.0)
            roi = max(0.0, 1.0 - float(row[1] or 0.0))  # inverse of avg risk
            uptime = min(float(row[2]) / 10.0, 1.0)       # ~10 windows = full uptime
            halluc = float(row[4] or 0.0)

            # Scale to 0–1000 for on-chain uint16
            s_1000 = int(success * 1000)
            r_1000 = int(roi * 1000)
            u_1000 = int(uptime * 1000)
            h_1000 = int(halluc * 1000)

            # Composite: 40% success + 20% roi + 25% uptime + 15% inverse-halluc
            composite = (s_1000 * 40 + r_1000 * 20 + u_1000 * 25 + (1000 - h_1000) * 15) // 100

            return ReputationData(
                agent_addr=agent_addr,
                org_id=org_id,
                success_rate=success,
                roi_score=roi,
                uptime_score=uptime,
                halluc_rate=halluc,
                composite_score=composite / 1000.0,
                tx_count=int(row[3] or 0),
            )
        except Exception:
            return None

    # ── Persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _write_sqlite(rep: ReputationData) -> None:
        from datetime import datetime, timezone
        from core.db import get_sqlite_conn
        with get_sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO reputation_scores
                  (agent_addr, org_id, success_rate, roi_score, uptime_score, halluc_rate, composite, tx_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_addr, org_id) DO UPDATE SET
                  success_rate=excluded.success_rate, roi_score=excluded.roi_score,
                  uptime_score=excluded.uptime_score, halluc_rate=excluded.halluc_rate,
                  composite=excluded.composite, tx_count=excluded.tx_count, updated_at=excluded.updated_at
                """,
                (
                    rep.agent_addr, rep.org_id,
                    rep.success_rate, rep.roi_score, rep.uptime_score,
                    rep.halluc_rate, rep.composite_score, rep.tx_count,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    @staticmethod
    def _append_duck_history(rep: ReputationData) -> None:
        from datetime import datetime, timezone
        from core.db import get_duck_conn
        with get_duck_conn() as conn:
            conn.execute(
                """
                INSERT INTO reputation_history
                  (agent_addr, org_id, recorded_at, composite, success_rate, roi_score, uptime_score, halluc_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rep.agent_addr, rep.org_id,
                    datetime.now(timezone.utc),
                    rep.composite_score, rep.success_rate,
                    rep.roi_score, rep.uptime_score, rep.halluc_rate,
                ],
            )

    # ── On-chain SBT update ───────────────────────────────────────────────────

    @staticmethod
    def _batch_update_sbt(reps: list[ReputationData]) -> None:
        try:
            from core.config.settings import get_settings
            from web3 import Web3
            import json
            s = get_settings()
            if not s.SBT_ADDR or not s.DEPLOYER_KEY:
                return

            w3 = Web3(Web3.HTTPProvider(s.active_rpc))
            # Minimal ABI for batchUpdateScores
            abi = json.loads("""[
              {"inputs":[
                {"name":"tokenIds","type":"uint256[]"},
                {"name":"successRates","type":"uint16[]"},
                {"name":"roiScores","type":"uint16[]"},
                {"name":"uptimeScores","type":"uint16[]"},
                {"name":"hallucRates","type":"uint16[]"}
              ],"name":"batchUpdateScores","outputs":[],"stateMutability":"nonpayable","type":"function"}
            ]""")
            contract = w3.eth.contract(address=s.SBT_ADDR, abi=abi)

            # Build arrays (token IDs = sequential 1..N for simplicity)
            token_ids = list(range(1, len(reps) + 1))
            fn = contract.functions.batchUpdateScores(
                token_ids,
                [int(r.success_rate * 1000) for r in reps],
                [int(r.roi_score * 1000)    for r in reps],
                [int(r.uptime_score * 1000) for r in reps],
                [int(r.halluc_rate * 1000)  for r in reps],
            )
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)
            tx = fn.build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "gas": 200_000,
            })
            signed = acct.sign_transaction(tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            pass  # SBT update is non-critical; log separately

    @staticmethod
    def _list_org_agents(org_id: str) -> list[str]:
        try:
            from core.db import get_sqlite_conn
            with get_sqlite_conn() as conn:
                rows = conn.execute(
                    "SELECT agent_addr FROM agents WHERE org_id=? AND paused=0", (org_id,)
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []
