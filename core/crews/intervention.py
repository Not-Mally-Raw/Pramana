"""
core/crews/intervention.py — Level-2 Intervention Crew.
Invoked when escalation threshold is met.
Runs: Council deliberation → Recovery (if needed) → Reputation update.
"""
from __future__ import annotations

from utils.models import CouncilResult, FlowAction, PramanaState

from core.agents.council import CouncilAgent
from core.agents.recovery import RecoveryAgent
from core.agents.reputation import ReputationAgent

__all__ = ["InterventionCrew"]


class InterventionCrew:
    """Level-2 crew: Council + Recovery + Reputation. Invoked on escalation."""

    def __init__(self) -> None:
        self._council    = CouncilAgent()
        self._recovery   = RecoveryAgent()
        self._reputation = ReputationAgent()

    def kickoff(self, state: PramanaState) -> CouncilResult:
        # ── 1. Council deliberation ─────────────────────────────────────────
        council_result = self._council.deliberate(state)

        # ── 2. Recovery: if any judge flagged groq_rate_limit pattern ───────
        if all(v.model == "fallback" or v.model == "deterministic" for v in council_result.votes):
            self._recovery.execute("groq_rate_limit", state)

        # ── 3. On-chain action based on council verdict ──────────────────────
        if council_result.action in (FlowAction.PAUSE, FlowAction.EMERGENCY):
            self._on_chain_pause(state)

        # ── 4. Reputation update (async / non-critical) ───────────────────
        if state.event:
            self._reputation.update_org(state.event.org_id)

        return council_result

    @staticmethod
    def _on_chain_pause(state: PramanaState) -> None:
        """Write pause + council decision to Orchestrator contract."""
        try:
            from core.config.settings import get_settings
            from web3 import Web3
            import json

            s = get_settings()
            if not s.ORCHESTRATOR_ADDR or not s.DEPLOYER_KEY or not state.event:
                return

            w3 = Web3(Web3.HTTPProvider(s.active_rpc))
            abi = json.loads("""[
              {"inputs":[{"name":"agentId","type":"uint256"},{"name":"reason","type":"string"}],
               "name":"pauseAgent","outputs":[],"stateMutability":"nonpayable","type":"function"},
              {"inputs":[{"name":"agentId","type":"uint256"},{"name":"approved","type":"bool"},
               {"name":"riskScore","type":"uint8"},{"name":"reason","type":"string"}],
               "name":"recordCouncilDecision","outputs":[],"stateMutability":"nonpayable","type":"function"}
            ]""")
            contract = w3.eth.contract(address=s.ORCHESTRATOR_ADDR, abi=abi)
            acct = w3.eth.account.from_key(s.DEPLOYER_KEY)
            nonce = w3.eth.get_transaction_count(acct.address)

            # recordCouncilDecision auto-pauses if !approved && risk >= 70
            cr = state.council_result
            if cr:
                risk_int = min(int(cr.risk_score * 100), 100)
                tx = contract.functions.recordCouncilDecision(
                    0,  # agentId — resolve via registry in production
                    cr.approved,
                    risk_int,
                    cr.reason[:200],
                ).build_transaction({"from": acct.address, "nonce": nonce, "gas": 80_000})
                signed = acct.sign_transaction(tx)
                w3.eth.send_raw_transaction(signed.raw_transaction)
        except Exception:
            pass  # chain write failures handled by recovery playbook


class MonitoringAndInterventionCrew:
    """
    Convenience wrapper: runs monitoring then conditionally runs intervention.
    Used by PramanaFlow.
    """

    def __init__(self) -> None:
        from core.crews.monitoring import MonitoringCrew
        self._monitoring    = MonitoringCrew()
        self._intervention  = InterventionCrew()

    def run_monitoring(self, state: PramanaState) -> PramanaState:
        if not state.event:
            return state
        results = self._monitoring.kickoff(state.event, state)
        state.l1_results = results
        state.flag_count = sum(1 for r in results if r.flagged)
        state.risk_score = max((r.score for r in results if r.flagged), default=0.0)
        return state

    def run_intervention(self, state: PramanaState) -> PramanaState:
        council_result = self._intervention.kickoff(state)
        state.council_result = council_result
        state.action = council_result.action
        return state
