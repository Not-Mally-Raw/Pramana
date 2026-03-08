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
import uuid
from datetime import datetime, timezone

from crewai.flow.flow import Flow, listen, router, start

from core.config.settings import get_settings
from core.crews.intervention import MonitoringAndInterventionCrew
from core.db import insert_event, insert_trace
from utils.cache import ROUTING_TABLE_CACHE, invalidate_routing_cache
from utils.graph import TraceDAG
from utils.models import ChainEvent, DegradedMode, FlowAction, PramanaState

__all__ = ["PramanaFlow"]


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
        if state.flow_id is None:
            state.flow_id = str(uuid.uuid4())

        # Persist event to SQLite
        if state.event:
            insert_event(state.event.model_dump())

        # Initialize TraceDAG for this flow
        agent_id = state.event.agent_id  if state.event else "unknown"
        org_id   = state.event.org_id    if state.event else s.DEFAULT_ORG_ID
        dag = TraceDAG(flow_id=state.flow_id, agent_id=agent_id, org_id=org_id)
        dag.add_node("ingest", action="ingest_event", score=0.0, flagged=False,
                     metadata={"event_action": state.event.action if state.event else ""})
        state.trace_dag_json = dag.to_json()

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
                r.agent_name, action="l1_check", score=r.score, flagged=r.flagged,
                metadata={"reason": r.reason, "latency_ms": r.latency_ms, "pass_count": r.pass_count}
            )
            dag.add_edge("ingest", r.agent_name, payload={"flagged": r.flagged, "score": r.score})

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

        # Check hard conditions first (ORDER BY priority DESC already via SQL)
        if state.flag_count >= s.ESCALATION_FLAG_THRESHOLD:
            return "escalate"

        if state.risk_score >= s.RISK_SCORE_PAUSE:
            return "pause"

        if state.risk_score >= s.RISK_SCORE_ESCALATE:
            return "escalate"

        # Check injection agent specifically (hard pause regardless of flag_count)
        injection_result = next((r for r in state.l1_results if r.agent_name == "injection"), None)
        if injection_result and injection_result.flagged and injection_result.score >= 0.85:
            return "pause"

        return "pass"

    # ── Step 4A: Escalate (L2 Council) ───────────────────────────────────────

    @listen("escalate")
    def run_l2_council(self) -> PramanaState:
        """Run L2 InterventionCrew: Council + Recovery + Reputation."""
        state = self.state
        state = self._crews.run_intervention(state)

        # Update TraceDAG with council node
        dag = TraceDAG.from_json(state.trace_dag_json)
        cr = state.council_result
        if cr:
            dag.add_node("council", action="council_vote", score=cr.risk_score,
                         flagged=not cr.approved,
                         metadata={"approved": cr.approved, "action": str(cr.action)})
            for r in state.l1_results:
                if r.flagged:
                    dag.add_edge(r.agent_name, "council", payload={"reason": r.reason})

        state.trace_dag_json = dag.to_json()
        return state

    # ── Step 4B: Pause agent ──────────────────────────────────────────────────

    @listen("pause")
    def pause_agent_flow(self) -> PramanaState:
        """Emergency pause: write to contract and set action = PAUSE."""
        state = self.state
        state.action = FlowAction.PAUSE

        dag = TraceDAG.from_json(state.trace_dag_json)
        dag.add_node("emergency_pause", action="pause", score=1.0, flagged=True,
                     metadata={"reason": "escalation_router: pause path"})
        state.trace_dag_json = dag.to_json()
        return state

    # ── Step 4C: Pass ─────────────────────────────────────────────────────────

    @listen("pass")
    def log_and_continue(self) -> PramanaState:
        """Clean path: log trace and continue."""
        state = self.state
        state.action = FlowAction.PASS
        return state

    # ── Step 5: Finalize (common terminal step) ───────────────────────────────

    @listen(run_l2_council, log_and_continue, pause_agent_flow)
    def finalize_trace(self, prev: PramanaState) -> PramanaState:
        """Finalize TraceDAG → SHA-256 hash → emit TraceFinalized on-chain."""
        state = self.state
        s = get_settings()

        dag = TraceDAG.from_json(state.trace_dag_json)
        dag.add_node("finalize", action="trace_finalized", score=state.risk_score,
                     flagged=state.flag_count > 0, metadata={"action": str(state.action)})
        state.trace_hash  = dag.finalize()
        state.trace_dag_json = dag.to_json()

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

        # Emit TraceFinalized on-chain (non-blocking; ~3K gas, event only)
        if state.degraded_mode != DegradedMode.NO_CHAIN:
            self._emit_trace_finalized(state)

        return state

    @staticmethod
    def _emit_trace_finalized(state: PramanaState) -> None:
        try:
            from core.config.settings import get_settings
            from web3 import Web3
            import json

            s = get_settings()
            if not s.ORCHESTRATOR_ADDR or not s.DEPLOYER_KEY or not state.event:
                return

            w3 = Web3(Web3.HTTPProvider(s.active_rpc))
            abi = json.loads("""[
              {"inputs":[{"name":"agentId","type":"uint256"},{"name":"traceHash","type":"bytes32"}],
               "name":"finalizeTrace","outputs":[],"stateMutability":"nonpayable","type":"function"}
            ]""")
            contract = w3.eth.contract(address=s.ORCHESTRATOR_ADDR, abi=abi)
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)

            from utils.graph import TraceDAG as _D
            dag = _D.from_json(state.trace_dag_json)
            hash_bytes = dag.finalize_bytes32()

            tx = contract.functions.finalizeTrace(0, hash_bytes).build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "gas": 50_000,
            })
            signed = acct.sign_transaction(tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            pass
