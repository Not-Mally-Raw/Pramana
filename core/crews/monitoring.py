"""
core/crews/monitoring.py — Level-1 Monitoring Crew.
Runs 7 L1 agents sequentially (Network → Ops → Halluc → Inject → Validator → Drift).
Results aggregated into PramanaState.
"""
from __future__ import annotations

from utils.models import ChainEvent, L1Result, PramanaState

from core.agents import (
    DriftMonitorAgent,
    EconomicSanityAgent,
    HallucinationAgent,
    InjectionAgent,
    NetworkAgent,
    OperationsAgent,
    ValidatorAgent,
    SanctionsAgent,
)

__all__ = ["MonitoringCrew"]

# Execution order (NetworkX critical path order)
_AGENT_ORDER = [
    SanctionsAgent(),      # 1. Hard-stop compliance
    NetworkAgent(),        # 2. Infra context
    OperationsAgent(),     # 3. Account balance context
    HallucinationAgent(),  # 4. LLM output anomalies
    InjectionAgent(),      # 5. Security payloads (hard-stop)
    ValidatorAgent(),      # 6. Schema/ABI validity
    EconomicSanityAgent(), # 7. MEV/Profitability
    DriftMonitorAgent(),   # 8. Behavioral drift
]


class MonitoringCrew:
    """
    Level-1 crew: all 7 L1 agents. Sequential execution preserving order.
    Stops early only if injection is detected (hard-stop security rule).
    Returns list[L1Result] directly (no CrewAI overhead needed at this layer).
    """

    def kickoff(self, event: ChainEvent, state: PramanaState) -> list[L1Result]:
        results: list[L1Result] = []

        # Check disabled agents from recovery state
        disabled = getattr(state, "disabled_agents", set())

        for agent in _AGENT_ORDER:
            if agent.name in disabled:
                results.append(
                    L1Result(agent_name=agent.name, flagged=False, score=0.0,
                             reason="agent disabled by recovery", latency_ms=0.0, pass_count=0, metadata={})
                )
                continue

            result = agent.run(event, state)
            results.append(result)

            # Hard stop: sanctions hit → skip remaining L1 agents immediately
            if agent.name == "sanctions" and result.flagged:
                remaining = [a for a in _AGENT_ORDER if a not in _AGENT_ORDER[:_AGENT_ORDER.index(agent) + 1]]
                for rem in remaining:
                    results.append(
                        L1Result(agent_name=rem.name, flagged=False, score=0.0,
                                 reason="skipped: OFAC sanctions hard-stop", latency_ms=0.0, pass_count=0, metadata={})
                    )
                break

            # Hard stop: injection flagged at high confidence → skip remaining L1 agents
            if agent.name == "injection" and result.flagged and result.score >= 0.90:
                remaining = [a for a in _AGENT_ORDER if a not in _AGENT_ORDER[:_AGENT_ORDER.index(agent) + 1]]
                for rem in remaining:
                    results.append(
                        L1Result(agent_name=rem.name, flagged=False, score=0.0,
                                 reason="skipped: injection hard-stop", latency_ms=0.0, pass_count=0, metadata={})
                    )
                break

        return results
