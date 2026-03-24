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
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from crewai.flow.flow import Flow, listen, or_, router, start

from core.config.settings import get_settings
from core.crews.intervention import MonitoringAndInterventionCrew
from core.db import get_routing_table, insert_agent_metric, insert_council_decision, insert_event, insert_trace
from utils.graph import TraceDAG
from utils.models import ChainEvent, DegradedMode, FlowAction, PramanaState
from utils.safe_eval import evaluate_boolean_expression

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
        if not state.flow_id:
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
                r.agent_name, action="l1_check", score=r.score, flagged=r.flagged,
                metadata={"reason": r.reason, "latency_ms": r.latency_ms, "pass_count": r.pass_count}
            )
            dag.add_edge("ingest", r.agent_name, payload=json.dumps({"flagged": r.flagged, "score": r.score}))

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
            return route

        # 2) Fallback hard thresholds if no rule matched
        if state.flag_count >= s.ESCALATION_FLAG_THRESHOLD:
            return "escalate"
        if state.risk_score >= s.RISK_SCORE_PAUSE:
            return "pause"
        if state.risk_score >= s.RISK_SCORE_ESCALATE:
            return "escalate"

        # Injection hard-pause fallback
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
                    dag.add_edge(r.agent_name, "council", payload=json.dumps({"reason": r.reason}))

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

    @listen(or_(run_l2_council, log_and_continue, pause_agent_flow))
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

        # Persist analytics feedback loop window (used by reputation and threshold tuning)
        if state.event:
            insert_agent_metric(
                agent_addr=state.event.agent_id,
                org_id=state.event.org_id,
                risk_score=state.risk_score,
                flag_count=state.flag_count,
                avg_gas_gwei=(state.event.gas_price / 1e9) if state.event.gas_price else 0.0,
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

            from utils.crypto import eth_address_to_uint256
            agent_id = eth_address_to_uint256(state.event.agent_id)

            tx = contract.functions.finalizeTrace(agent_id, hash_bytes).build_transaction({
                "from": acct.address,
                "nonce": w3.eth.get_transaction_count(acct.address),
                "gas": 50_000,
            })
            signed = acct.sign_transaction(tx)
            w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
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
                "gas_average": float(network.metadata.get("avg_gas_gwei", 1.0)) if network else 1.0,
                "metadata": {
                    "gas_price_gwei": float(network.metadata.get("gas_gwei", 0.0)) if network else 0.0,
                    "balance_delta_pct": -float(operations.metadata.get("recent_eth_out_5min", 0.0)) if operations else 0.0,
                    "actions_per_min": int(operations.metadata.get("actions_per_min", 0)) if operations else 0,
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

            ordered = sorted(rules.values(), key=lambda x: int(x.get("priority", 0)), reverse=True)
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
        except Exception:
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
            return SimpleNamespace(**{k: PramanaFlow._to_namespace(v) for k, v in obj.items()})
        if isinstance(obj, list):
            return [PramanaFlow._to_namespace(v) for v in obj]
        return obj
