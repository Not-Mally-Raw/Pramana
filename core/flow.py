"""
core/flow.py — PramanaFlow: top-level CrewAI Flow[PramanaState] orchestrator.
Level-0 coordinator wrapping Level-1 and Level-2 crews.

Flow graph:
  ingest_event
    → run_l1_crew
      → escalation_check (router)
        ─ "pass"     → log_and_continue
        ─ "escalate" → run_l2_council
        ─ "pause"    → pause_agent_flow

All routing decisions are O(1) dict lookups from RoutingTable. No LLM in routing.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from types import SimpleNamespace

from crewai.flow.flow import Flow, listen, or_, router, start

from core.config.settings import get_settings
from core.crews.intervention import MonitoringAndInterventionCrew
from core.db import (
    get_routing_table,
    insert_agent_metric,
    insert_council_decision,
    insert_event,
    insert_lifecycle_transition,
    insert_trace,
    set_agent_paused,
)
from core.realtime import publish_event
from utils.crypto import eth_address_to_uint256, sha256_hex
from utils.graph import TraceDAG
from utils.models import DegradedMode, FlowAction, PramanaState
from utils.safe_eval import evaluate_boolean_expression

__all__ = ["PramanaFlow"]

logger = logging.getLogger("pramana.flow")
guardian_logger = logging.getLogger("pramana.guardian")


class PramanaFlow(Flow[PramanaState]):
    """
    Level-0 orchestrator. Receives a ChainEvent, runs L1 monitoring crew,
    decides escalation, runs L2 council if needed, finalizes TraceDAG.
    """

    def __init__(self) -> None:
        super().__init__()
        self._crews = MonitoringAndInterventionCrew()

    # ── Step 1: Ingest ────────────────────────────────────────────────────────

    @start()
    def ingest_event(self) -> PramanaState:
        """Entry point. Receives event from listener/API. Initializes state."""
        s = get_settings()
        state = self.state
        state.pipeline_start_ms = time.perf_counter() * 1000
        if not state.flow_id:
            state.flow_id = str(uuid.uuid4())

        # Persist event to SQLite
        if state.event:
            insert_event(state.event.model_dump())

        # Initialize TraceDAG for this flow
        agent_id = state.event.agent_id if state.event else "unknown"
        org_id = state.event.org_id if state.event else s.DEFAULT_ORG_ID
        dag = TraceDAG(flow_id=state.flow_id, agent_id=agent_id, org_id=org_id)
        dag.add_node(
            "ingest",
            action="ingest_event",
            score=0.0,
            flagged=False,
            metadata={"event_action": state.event.action if state.event else ""},
        )
        state.trace_dag_json = dag.to_json()

        # Load agent profile from registry (Step 1)
        from core.db import get_agent_profile

        state.agent_profile = get_agent_profile(agent_id, org_id)

        return state

    # ── Step 2: L1 Monitoring ─────────────────────────────────────────────────

    @listen(ingest_event)
    def run_l1_crew(self, prev: PramanaState) -> PramanaState:
        """Run all L1 monitoring agents via MonitoringCrew."""
        state = self.state
        state = self._crews.run_monitoring(state)

        # Update TraceDAG with L1 results
        dag = TraceDAG.from_json(state.trace_dag_json)
        for r in state.l1_results:
            dag.add_node(
                r.agent_name,
                action="l1_check",
                score=r.score,
                flagged=r.flagged,
                metadata={
                    "reason": r.reason,
                    "latency_ms": r.latency_ms,
                    "pass_count": r.pass_count,
                },
            )
            dag.add_edge(
                "ingest",
                r.agent_name,
                payload=json.dumps({"flagged": r.flagged, "score": r.score}),
            )

        state.trace_dag_json = dag.to_json()
        return state

    # ── Step 3: Routing ───────────────────────────────────────────────────────

    @router(run_l1_crew)
    def escalation_check(self, prev: PramanaState) -> str:
        """
        Declarative O(1) routing. Checks RoutingTable from SQLite (cached 30s).
        Returns: "pass" | "escalate" | "pause"
        """
        state = self.state
        s = get_settings()

        # 1) Declarative routing from DB-backed routing_table (hot-reloadable)
        route = self._route_from_rules(state)
        if route:
            state.final_decision_reason = f"routing_rule:{route}"
            return route

        # Injection hard-pause fallback
        injection_result = next(
            (r for r in state.l1_results if r.agent_name == "injection"), None
        )
        if (
            injection_result
            and injection_result.flagged
            and injection_result.score >= 0.85
        ):
            state.final_decision_reason = (
                f"injection hard-pause fallback score={injection_result.score:.3f}"
            )
            return "pause"

        # 2) Fallback hard thresholds if no rule matched
        if state.flag_count >= s.ESCALATION_FLAG_THRESHOLD:
            state.final_decision_reason = (
                f"flag_count {state.flag_count} >= {s.ESCALATION_FLAG_THRESHOLD}"
            )
            return "escalate"
        if state.risk_score >= s.RISK_SCORE_PAUSE:
            state.final_decision_reason = f"risk_score {state.risk_score:.3f} >= pause_threshold {s.RISK_SCORE_PAUSE:.3f}"
            return "pause"

        # Single-agent high-confidence escalation (avoid requiring 2 flags when
        # one detector is already very confident). Excludes injection (handled
        # as a pause above).
        max_flagged_score = 0.0
        for r in state.l1_results:
            if r.agent_name == "injection":
                continue
            if r.flagged:
                max_flagged_score = max(max_flagged_score, float(r.score))
        if max_flagged_score >= s.SINGLE_AGENT_ESCALATE_SCORE:
            state.final_decision_reason = (
                f"single_agent_score {max_flagged_score:.3f} >= {s.SINGLE_AGENT_ESCALATE_SCORE:.3f}"
            )
            return "escalate"

        if state.risk_score >= s.RISK_SCORE_ESCALATE:
            state.final_decision_reason = f"risk_score {state.risk_score:.3f} >= escalate_threshold {s.RISK_SCORE_ESCALATE:.3f}"
            return "escalate"

        state.final_decision_reason = "no escalation thresholds matched"
        return "pass"

    # ── Step 4A: Escalate (L2 Council) ───────────────────────────────────────

    @listen("escalate")
    def run_l2_council(self) -> PramanaState:
        """Run L2 InterventionCrew: Council + Recovery + Reputation."""
        state = self.state
        state = self._crews.run_intervention(state)

        if state.council_result and state.council_result.reason:
            state.final_decision_reason = state.council_result.reason

        # Update TraceDAG with council node
        dag = TraceDAG.from_json(state.trace_dag_json)
        cr = state.council_result
        if cr:
            dag.add_node(
                "council",
                action="council_vote",
                score=cr.risk_score,
                flagged=not cr.approved,
                metadata={"approved": cr.approved, "action": str(cr.action)},
            )
            for r in state.l1_results:
                if r.flagged:
                    dag.add_edge(
                        r.agent_name,
                        "council",
                        payload=json.dumps({"reason": r.reason}),
                    )

        state.trace_dag_json = dag.to_json()
        return state

    # ── Step 4B: Pause agent ──────────────────────────────────────────────────

    @listen("pause")
    def pause_agent_flow(self) -> PramanaState:
        """Emergency pause: write to contract and set action = PAUSE."""
        state = self.state
        state.action = FlowAction.PAUSE
        if not state.final_decision_reason:
            state.final_decision_reason = "escalation_router: pause path"

        dag = TraceDAG.from_json(state.trace_dag_json)
        dag.add_node(
            "emergency_pause",
            action="pause",
            score=1.0,
            flagged=True,
            metadata={"reason": "escalation_router: pause path"},
        )
        state.trace_dag_json = dag.to_json()

        # Phase 4: Pause governance path. Use pause_ref at decision time, then
        # link finalized trace_hash later in finalize_trace().
        try:
            if state.event:
                state.pause_ref = self._compute_pause_ref(
                    agent_addr=state.event.agent_id,
                    tx_hash=state.event.tx_hash,
                    risk_score=state.risk_score or 0.0,
                    reason=state.final_decision_reason or "L1/L2 escalation",
                )
                guardian_voted = self._request_pause_via_guardian(
                    agent_addr=state.event.agent_id,
                    pause_ref=state.pause_ref,
                    risk_score=state.risk_score or 0.0,
                    reason=state.final_decision_reason or "L1/L2 escalation",
                )
                if not guardian_voted:
                    fallback_ok = self._direct_pause_via_orchestrator(
                        agent_addr=state.event.agent_id,
                        reason=(state.final_decision_reason or "L1/L2 escalation")[
                            :200
                        ],
                    )
                    if fallback_ok:
                        logger.info(
                            "Guardian vote unavailable for %s — direct Orchestrator pause executed",
                            state.event.agent_id,
                        )
                    else:
                        logger.warning(
                            "Guardian vote unavailable for %s and direct Orchestrator pause failed",
                            state.event.agent_id,
                        )
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.warning("Pause governance path failed: %s", e)
            # Do NOT re-raise — governance path failure must not block the pause

        # Phase 3: Financial recovery alert generation
        try:
            if state.event:
                import asyncio as _asyncio

                from core.agents.financial_recovery import (
                    FinancialRecoveryService,
                    _fetch_open_positions,
                    _get_operator_webhook,
                    _persist_recovery_alert,
                )

                positions = _fetch_open_positions(
                    state.event.agent_id, state.event.org_id
                )
                if positions:
                    recovery_svc = FinancialRecoveryService()
                    alert = recovery_svc.generate_alert(
                        agent_addr=state.event.agent_id,
                        org_id=state.event.org_id,
                        trace_hash=state.trace_hash or state.pause_ref or "",
                        positions=positions,
                        rpc_url=get_settings().active_rpc,
                    )
                    _persist_recovery_alert(alert)
                    state.recovery_alert = alert

                    # Non-blocking webhook delivery
                    webhook_url = _get_operator_webhook(state.event.org_id)
                    if webhook_url:
                        _asyncio.create_task(
                            recovery_svc.deliver_webhook(alert, webhook_url)
                        )
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.error(
                "Recovery alert generation failed for %s: %s",
                state.event.agent_id if state.event else "unknown",
                e,
            )
            # Do NOT re-raise — recovery alert failure must not block the pause

        return state

    # ── Step 4C: Pass ─────────────────────────────────────────────────────────

    @listen("pass")
    def log_and_continue(self) -> PramanaState:
        """Clean path: log trace and continue."""
        state = self.state
        state.action = FlowAction.PASS
        if not state.final_decision_reason:
            state.final_decision_reason = "escalation_router: pass path"
        return state

    # ── Step 5: Finalize (common terminal step) ───────────────────────────────

    @listen(or_(run_l2_council, log_and_continue, pause_agent_flow))
    def finalize_trace(self, prev: PramanaState) -> PramanaState:
        """Finalize TraceDAG → SHA-256 hash → emit TraceFinalized on-chain."""
        state = self.state
        s = get_settings()
        now_ms = int(time.time() * 1000)

        dag = TraceDAG.from_json(state.trace_dag_json)
        dag.add_node(
            "finalize",
            action="trace_finalized",
            score=state.risk_score,
            flagged=state.flag_count > 0,
            metadata={"action": str(state.action)},
        )
        state.trace_hash = dag.finalize()
        state.trace_dag_json = dag.to_json()

        # If pause vote used a pre-finalization pause_ref, attach final trace hash now.
        if state.action == FlowAction.PAUSE and state.pause_ref:
            self._link_trace_via_guardian(state.pause_ref, state.trace_hash)

        # Persist trace to SQLite
        insert_trace(
            flow_id=state.flow_id or str(uuid.uuid4()),
            org_id=state.event.org_id if state.event else s.DEFAULT_ORG_ID,
            agent_addr=state.event.agent_id if state.event else "unknown",
            trace_hash=state.trace_hash,
            dag_json=state.trace_dag_json,
            flag_count=state.flag_count,
            risk_score=state.risk_score,
            council_action=str(state.action),
        )

        # Persist analytics feedback loop window (used by reputation and threshold tuning)
        if state.event:
            insert_agent_metric(
                agent_addr=state.event.agent_id,
                org_id=state.event.org_id,
                risk_score=state.risk_score,
                flag_count=state.flag_count,
                avg_gas_gwei=(
                    (state.event.gas_price / 1e9) if state.event.gas_price else 0.0
                ),
                success_pct=1.0 if state.action == FlowAction.PASS else 0.0,
            )

        if state.event and state.council_result:
            insert_council_decision(
                flow_id=state.flow_id or str(uuid.uuid4()),
                agent_addr=state.event.agent_id,
                org_id=state.event.org_id,
                approved=state.council_result.approved,
                risk_score=state.council_result.risk_score,
                confidence=state.council_result.confidence,
                action=str(state.council_result.action),
                reason=state.council_result.reason,
            )

        if state.event:
            top_flag = next((r.agent_name for r in state.l1_results if r.flagged), "")
            publish_event(
                {
                    "type": "decision",
                    "trace_id": state.trace_hash,
                    "agent_addr": state.event.agent_id,
                    "org_id": state.event.org_id,
                    "decision": state.action.value.upper(),
                    "risk_score": float(state.risk_score),
                    "top_agent": top_flag,
                    "timestamp": now_ms,
                }
            )

            for result in state.l1_results:
                publish_event(
                    {
                        "type": "dag_node",
                        "trace_id": state.trace_hash,
                        "org_id": state.event.org_id,
                        "node_name": result.agent_name,
                        "score": float(result.score),
                        "flagged": bool(result.flagged),
                        "latency_ms": float(result.latency_ms),
                        "reason": result.reason,
                        "timestamp": now_ms,
                    }
                )

        if state.action == FlowAction.PAUSE and state.event:
            try:
                set_agent_paused(
                    agent_addr=state.event.agent_id,
                    org_id=state.event.org_id,
                    paused=True,
                )
            except (sqlite3.Error, RuntimeError, ValueError, TypeError):
                pass

            try:
                insert_lifecycle_transition(
                    agent_addr=state.event.agent_id,
                    org_id=state.event.org_id,
                    from_state="ACTIVE",
                    to_state="PAUSED",
                    reason=state.final_decision_reason or "pause_decision",
                    capital_at_risk_usd=(
                        float(state.recovery_alert.total_usd_at_risk)
                        if state.recovery_alert is not None
                        else None
                    ),
                )
            except (sqlite3.Error, RuntimeError, ValueError, TypeError):
                pass

            publish_event(
                {
                    "type": "lifecycle",
                    "agent_addr": state.event.agent_id,
                    "org_id": state.event.org_id,
                    "from_state": "ACTIVE",
                    "to_state": "PAUSED",
                    "reason": state.final_decision_reason or "pause_decision",
                    "timestamp": now_ms,
                }
            )

        if state.recovery_alert is not None:
            publish_event(
                {
                    "type": "recovery_alert",
                    "alert_id": state.recovery_alert.alert_id,
                    "agent_addr": state.recovery_alert.agent_addr,
                    "org_id": state.recovery_alert.org_id,
                    "critical_count": int(state.recovery_alert.critical_count),
                    "warning_count": int(state.recovery_alert.warning_count),
                    "safe_count": int(state.recovery_alert.safe_count),
                    "total_usd_at_risk": float(state.recovery_alert.total_usd_at_risk),
                    "timestamp": now_ms,
                }
            )

        # Emit TraceFinalized on-chain (non-blocking; ~3K gas, event only)
        if state.degraded_mode != DegradedMode.NO_CHAIN:
            self._emit_trace_finalized(state)

        return state

    @staticmethod
    def _request_pause_via_guardian(
        agent_addr: str, pause_ref: str, risk_score: float, reason: str
    ) -> bool:
        """
        Submit a pause vote to Guardian.sol multisig contract.

        Threshold rules (matches Guardian.sol constants):
        - Emergency (risk_score >= 0.95): EMERGENCY_THRESHOLD = 1 signature required
        - Standard  (risk_score < 0.95):  PAUSE_THRESHOLD = 2 signatures required

        This backend wallet is one of the council members. In production,
        other council members (human operators, DAO multisig) cast additional
        votes. For testnet, a single wallet can trigger emergency pauses.

        FALLBACK: If Guardian is unreachable or not deployed, logs the error
        and returns False. The caller must handle this — the pause must still
        execute via the existing Orchestrator path as fallback.
        """
        s = get_settings()

        # If Guardian address not configured, skip silently
        guardian_addr = getattr(s, "GUARDIAN_ADDR", "") or ""
        if (
            not guardian_addr
            or guardian_addr == "0x0000000000000000000000000000000000000000"
        ):
            logger.warning(
                "GUARDIAN_ADDR not configured — skipping Guardian vote, using direct pause"
            )
            return False

        is_emergency = risk_score >= 0.95

        GUARDIAN_ABI = json.loads(
            '[{"inputs":[{"name":"agent","type":"address"},{"name":"reason","type":"string"},{"name":"isEmergency","type":"bool"},{"name":"traceHash","type":"bytes32"}],"name":"requestPause","outputs":[{"name":"pauseId","type":"bytes32"}],"stateMutability":"nonpayable","type":"function"}]'
        )

        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(s.active_rpc, request_kwargs={"timeout": 10}))

            if not w3.is_connected():
                logger.error("RPC not connected — Guardian pause skipped")
                return False

            guardian = w3.eth.contract(
                address=w3.to_checksum_address(guardian_addr), abi=GUARDIAN_ABI
            )
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)

            # pause_ref is a deterministic pre-finalization correlation id.
            clean_ref = pause_ref.replace("0x", "").ljust(64, "0")[:64]
            pause_ref_bytes = bytes.fromhex(clean_ref)

            tx = guardian.functions.requestPause(
                w3.to_checksum_address(agent_addr),
                reason[:200],
                is_emergency,
                pause_ref_bytes,
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": w3.eth.get_transaction_count(acct.address),
                    "gas": 200000,
                    "maxFeePerGas": w3.eth.gas_price * 2,
                    "maxPriorityFeePerGas": w3.eth.gas_price,
                }
            )

            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)

            if receipt.status == 1:
                logger.info(
                    "Guardian pause vote submitted for %s — tx %s pause_ref=%s",
                    agent_addr,
                    tx_hash.hex(),
                    pause_ref,
                )
                return True
            logger.error("Guardian pause tx reverted for %s", agent_addr)
            return False

        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.error("Guardian.requestPause failed for %s: %s", agent_addr, e)
            return False

    @staticmethod
    def _link_trace_via_guardian(pause_ref: str, trace_hash: str) -> bool:
        """Link finalized trace hash back to pause_ref for governance auditability."""
        s = get_settings()

        guardian_addr = getattr(s, "GUARDIAN_ADDR", "") or ""
        if (
            not guardian_addr
            or guardian_addr == "0x0000000000000000000000000000000000000000"
        ):
            return False

        if not pause_ref or not trace_hash:
            return False

        ABI = json.loads(
            '[{"inputs":[{"name":"pauseRef","type":"bytes32"},{"name":"traceHash","type":"bytes32"}],"name":"linkTrace","outputs":[],"stateMutability":"nonpayable","type":"function"}]'
        )
        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(s.active_rpc, request_kwargs={"timeout": 10}))
            if not w3.is_connected() or not s.DEPLOYER_KEY:
                return False

            contract = w3.eth.contract(
                address=w3.to_checksum_address(guardian_addr), abi=ABI
            )
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)

            pause_ref_bytes = bytes.fromhex(
                pause_ref.replace("0x", "").ljust(64, "0")[:64]
            )
            trace_hash_bytes = bytes.fromhex(
                trace_hash.replace("0x", "").ljust(64, "0")[:64]
            )

            tx = contract.functions.linkTrace(
                pause_ref_bytes,
                trace_hash_bytes,
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": w3.eth.get_transaction_count(acct.address),
                    "gas": 120000,
                    "maxFeePerGas": w3.eth.gas_price * 2,
                    "maxPriorityFeePerGas": w3.eth.gas_price,
                }
            )
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            ok = receipt.status == 1
            if ok:
                logger.info("Guardian trace link submitted for pause_ref=%s", pause_ref)
            return ok
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            guardian_logger.warning(
                "Guardian.linkTrace failed for pause_ref=%s: %s", pause_ref, e
            )
            return False

    @staticmethod
    def _direct_pause_via_orchestrator(agent_addr: str, reason: str) -> bool:
        """Fallback path: issue direct Orchestrator pause transaction."""
        s = get_settings()
        if not s.ORCHESTRATOR_ADDR or not s.DEPLOYER_KEY:
            return False

        try:
            from web3 import Web3

            w3 = Web3(Web3.HTTPProvider(s.active_rpc, request_kwargs={"timeout": 10}))
            if not w3.is_connected():
                return False

            abi = json.loads(
                '[{"inputs":[{"name":"agentId","type":"uint256"},{"name":"reason","type":"string"}],"name":"pauseAgent","outputs":[],"stateMutability":"nonpayable","type":"function"}]'
            )
            contract = w3.eth.contract(
                address=w3.to_checksum_address(s.ORCHESTRATOR_ADDR), abi=abi
            )
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)
            agent_id = eth_address_to_uint256(agent_addr)
            if agent_id == 0:
                return False

            tx = contract.functions.pauseAgent(
                agent_id, reason[:200]
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": w3.eth.get_transaction_count(acct.address),
                    "gas": 120000,
                    "maxFeePerGas": w3.eth.gas_price * 2,
                    "maxPriorityFeePerGas": w3.eth.gas_price,
                }
            )
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            return receipt.status == 1
        except (RuntimeError, OSError, ValueError, TypeError):
            return False

    @staticmethod
    def _compute_pause_ref(
        agent_addr: str, tx_hash: str, risk_score: float, reason: str
    ) -> str:
        """Deterministic pre-finalization pause correlation id."""
        seed = f"{agent_addr.lower()}:{tx_hash}:{risk_score:.6f}:{reason[:200]}"
        return sha256_hex(seed)

    @staticmethod
    def _emit_trace_finalized(state: PramanaState) -> None:
        try:
            from web3 import Web3
            s = get_settings()
            if not s.ORCHESTRATOR_ADDR or not s.DEPLOYER_KEY or not state.event:
                return

            w3 = Web3(Web3.HTTPProvider(s.active_rpc))
            abi = json.loads(
                """[
              {"inputs":[{"name":"agentId","type":"uint256"},{"name":"traceHash","type":"bytes32"}],
               "name":"finalizeTrace","outputs":[],"stateMutability":"nonpayable","type":"function"}
            ]"""
            )
            contract = w3.eth.contract(
                address=w3.to_checksum_address(s.ORCHESTRATOR_ADDR), abi=abi
            )
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)

            dag = TraceDAG.from_json(state.trace_dag_json)
            hash_bytes = dag.finalize_bytes32()

            agent_id = eth_address_to_uint256(state.event.agent_id)

            tx = contract.functions.finalizeTrace(
                agent_id, hash_bytes
            ).build_transaction(
                {
                    "from": acct.address,
                    "nonce": w3.eth.get_transaction_count(acct.address),
                    "gas": 50_000,
                }
            )
            signed = acct.sign_transaction(tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
        except (RuntimeError, OSError, ValueError, TypeError):
            pass

    @staticmethod
    def _route_from_rules(state: PramanaState) -> str | None:
        try:
            rules = get_routing_table()
            if not rules:
                return None

            l1_map = {r.agent_name: r for r in state.l1_results}
            operations = l1_map.get("operations")
            network = l1_map.get("network")
            injection = l1_map.get("injection")
            drift = l1_map.get("drift_monitor")

            ctx = {
                "flag_count": state.flag_count,
                "risk_score": state.risk_score,
                "true": True,
                "false": False,
                "gas_average": (
                    float(network.metadata.get("avg_gas_gwei", 1.0)) if network else 1.0
                ),
                "metadata": {
                    "gas_price_gwei": (
                        float(network.metadata.get("gas_gwei", 0.0)) if network else 0.0
                    ),
                    "balance_delta_pct": (
                        -float(operations.metadata.get("recent_eth_out_5min", 0.0))
                        if operations
                        else 0.0
                    ),
                    "actions_per_min": (
                        int(operations.metadata.get("actions_per_min", 0))
                        if operations
                        else 0
                    ),
                },
                "l1_results": {
                    "injection": {
                        "flagged": bool(injection.flagged) if injection else False,
                        "score": float(injection.score) if injection else 0.0,
                    },
                    "drift": {
                        "flagged": bool(drift.flagged) if drift else False,
                        "score": float(drift.score) if drift else 0.0,
                    },
                },
            }
            ns_ctx = PramanaFlow._to_namespace(ctx)

            ordered = sorted(
                rules.values(), key=lambda x: int(x.get("priority", 0)), reverse=True
            )
            for rule in ordered:
                cond = str(rule.get("condition", "")).strip()
                if not cond:
                    continue
                if PramanaFlow._eval_condition(cond, ns_ctx):
                    action = str(rule.get("action", "")).lower()
                    if action in ("pause", "pause_and_escalate"):
                        return "pause"
                    if action == "escalate":
                        return "escalate"
                    if action == "pass":
                        return "pass"
            return None
        except (TypeError, ValueError, KeyError):
            return None

    @staticmethod
    def _eval_condition(condition: str, ctx) -> bool:
        """Evaluate a constrained boolean expression from routing config."""
        expr = condition
        expr = re.sub(r"\bAND\b", "and", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bOR\b", "or", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bNOT\b", "not", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\btrue\b", "True", expr, flags=re.IGNORECASE)
        expr = re.sub(r"\bfalse\b", "False", expr, flags=re.IGNORECASE)
        local_ctx = ctx if isinstance(ctx, dict) else vars(ctx)
        return evaluate_boolean_expression(expr, local_ctx)

    @staticmethod
    def _to_namespace(obj):
        if isinstance(obj, dict):
            return SimpleNamespace(
                **{k: PramanaFlow._to_namespace(v) for k, v in obj.items()}
            )
        if isinstance(obj, list):
            return [PramanaFlow._to_namespace(v) for v in obj]
        return obj
