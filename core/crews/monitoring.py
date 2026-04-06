"""
core/crews/monitoring.py — Level-1 Monitoring Crew.
Runs 9 L1 agents in optimized parallel DAG:
- Layer 0 (sequential hard-stops): Sanctions → AA Validator
- Layer 1 (parallel): Network, Operations, Hallucination
- Layer 2 (parallel): Injection, Validator
- Layer 3 (sequential): EconomicSanity → Drift

Target: <100ms total vs ~250ms sequential.
Results aggregated into PramanaState.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from core.agents import (
    DriftMonitorAgent,
    EconomicSanityAgent,
    HallucinationAgent,
    InjectionAgent,
    NetworkAgent,
    OperationsAgent,
    SanctionsAgent,
    ValidatorAgent,
)
from core.agents.aa_validator import AAValidatorAgent
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["MonitoringCrew"]

# Execution order (DAG optimization for parallelism)
_SANCTIONS_AGENT = SanctionsAgent()
_AA_VALIDATOR_AGENT = AAValidatorAgent()
_NETWORK_AGENT = NetworkAgent()
_OPERATIONS_AGENT = OperationsAgent()
_HALLUCINATION_AGENT = HallucinationAgent()
_INJECTION_AGENT = InjectionAgent()
_VALIDATOR_AGENT = ValidatorAgent()
_ECONOMIC_SANITY_AGENT = EconomicSanityAgent()
_DRIFT_MONITOR_AGENT = DriftMonitorAgent()


class MonitoringCrew:
    """
    Level-1 crew: 9 agents in parallel DAG execution.

    Execution layers:
    - Layer 0 (hard-stop): SanctionsAgent → AA Validator (sequential)
    - Layer 1 (parallel): Network, Operations, Hallucination (no interdependencies)
    - Layer 2 (parallel): Injection, Validator (run after Layer 1)
    - Layer 3 (sequential): EconomicSanity → Drift (need full context)

    Hard-stops: Sanctions score ≥ 0.95, AA score ≥ 0.85, Injection ≥ 0.90
    """

    def kickoff(self, event: ChainEvent, state: PramanaState) -> list[L1Result]:
        """Execute L1 agents in optimized parallel DAG."""
        results: list[L1Result] = []
        disabled = getattr(state, "disabled_agents", set())

        # ───────────────────────────────────────────────────────────────────────
        # Layer 0: Hard-stop agents (sequential, fail-fast)
        # ───────────────────────────────────────────────────────────────────────

        # Sanctions (hard-stop: score ≥ 0.95)
        if "sanctions" not in disabled:
            result = _SANCTIONS_AGENT.run(event, state)
            results.append(result)
            if result.flagged and result.score >= 0.95:
                # Hard stop for sanctions — skip all remaining agents
                remaining_agents = [
                    _AA_VALIDATOR_AGENT,
                    _NETWORK_AGENT,
                    _OPERATIONS_AGENT,
                    _HALLUCINATION_AGENT,
                    _INJECTION_AGENT,
                    _VALIDATOR_AGENT,
                    _ECONOMIC_SANITY_AGENT,
                    _DRIFT_MONITOR_AGENT,
                ]
                for rem_agent in remaining_agents:
                    results.append(
                        L1Result(
                            agent_name=rem_agent.name,
                            flagged=False,
                            score=0.0,
                            reason="skipped: OFAC sanctions hard-stop",
                            latency_ms=0.0,
                            pass_count=0,
                            metadata={},
                        )
                    )
                return results

        # AA Validator (only in AA mode; hard-stop: score ≥ 0.85)
        if event.user_operation is not None and "aa_validator" not in disabled:
            result = _AA_VALIDATOR_AGENT.run(event, state)
            results.append(result)
            if result.flagged and result.score >= 0.85:
                # Hard stop for AA violations
                remaining_agents = [
                    _NETWORK_AGENT,
                    _OPERATIONS_AGENT,
                    _HALLUCINATION_AGENT,
                    _INJECTION_AGENT,
                    _VALIDATOR_AGENT,
                    _ECONOMIC_SANITY_AGENT,
                    _DRIFT_MONITOR_AGENT,
                ]
                for rem_agent in remaining_agents:
                    results.append(
                        L1Result(
                            agent_name=rem_agent.name,
                            flagged=False,
                            score=0.0,
                            reason="skipped: invalid Account Abstraction structure",
                            latency_ms=0.0,
                            pass_count=0,
                            metadata={},
                        )
                    )
                return results
        else:
            if "aa_validator" not in disabled and event.user_operation is None:
                # AA validator disabled for non-AA events
                results.append(
                    L1Result(
                        agent_name=_AA_VALIDATOR_AGENT.name,
                        flagged=False,
                        score=0.0,
                        reason="skipped: non-AA event",
                        latency_ms=0.0,
                        pass_count=0,
                        metadata={},
                    )
                )

        # ───────────────────────────────────────────────────────────────────────
        # Layer 1: Parallel independent agents (no data dependencies)
        # ───────────────────────────────────────────────────────────────────────

        layer1_results = self._run_layer1_parallel(event, state, disabled)
        results.extend(layer1_results)

        # ───────────────────────────────────────────────────────────────────────
        # Layer 2: Parallel agents (after Layer 1)
        # ───────────────────────────────────────────────────────────────────────

        layer2_results = self._run_layer2_parallel(event, state, disabled)
        results.extend(layer2_results)

        # Injection hard-stop check (score ≥ 0.90)
        injection_result = next(
            (r for r in layer2_results if r.agent_name == "injection"), None
        )
        if (
            injection_result
            and injection_result.flagged
            and injection_result.score >= 0.90
        ):
            injection_idx = next(
                (i for i, r in enumerate(results) if r.agent_name == "injection"), -1
            )
            if injection_idx >= 0:
                for idx in range(injection_idx + 1, len(results)):
                    rem = results[idx]
                    results[idx] = L1Result(
                        agent_name=rem.agent_name,
                        flagged=False,
                        score=0.0,
                        reason="skipped: injection hard-stop",
                        latency_ms=0.0,
                        pass_count=0,
                        metadata={},
                    )

            # Hard stop for injection
            for agent in [_ECONOMIC_SANITY_AGENT, _DRIFT_MONITOR_AGENT]:
                results.append(
                    L1Result(
                        agent_name=agent.name,
                        flagged=False,
                        score=0.0,
                        reason="skipped: injection hard-stop",
                        latency_ms=0.0,
                        pass_count=0,
                        metadata={},
                    )
                )
            return results

        # ───────────────────────────────────────────────────────────────────────
        # Layer 3: Sequential context-dependent agents
        # ───────────────────────────────────────────────────────────────────────

        if "economic_sanity" not in disabled:
            results.append(_ECONOMIC_SANITY_AGENT.run(event, state))
        if "drift" not in disabled:
            results.append(_DRIFT_MONITOR_AGENT.run(event, state))

        return results

    def _run_layer1_parallel(
        self, event: ChainEvent, state: PramanaState, disabled: set
    ) -> list[L1Result]:
        """
        Layer 1: Run Network, Operations, Hallucination in parallel.
        These agents have no data dependencies.
        """

        async def _run_layer1():
            loop = asyncio.get_event_loop()
            executor = ThreadPoolExecutor(max_workers=3)

            tasks = []
            if "network" not in disabled:
                tasks.append(
                    loop.run_in_executor(executor, _NETWORK_AGENT.run, event, state)
                )
            if "operations" not in disabled:
                tasks.append(
                    loop.run_in_executor(executor, _OPERATIONS_AGENT.run, event, state)
                )
            if "hallucination" not in disabled:
                tasks.append(
                    loop.run_in_executor(
                        executor, _HALLUCINATION_AGENT.run, event, state
                    )
                )

            return await asyncio.gather(*tasks, return_exceptions=False)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(_run_layer1())
        except Exception:
            # Fallback to sequential if async fails
            results = []
            if "network" not in disabled:
                results.append(_NETWORK_AGENT.run(event, state))
            if "operations" not in disabled:
                results.append(_OPERATIONS_AGENT.run(event, state))
            if "hallucination" not in disabled:
                results.append(_HALLUCINATION_AGENT.run(event, state))
            return results

    def _run_layer2_parallel(
        self, event: ChainEvent, state: PramanaState, disabled: set
    ) -> list[L1Result]:
        """
        Layer 2: Run Injection and Validator in parallel.
        Both can run after Layer 1 completes.
        """

        async def _run_layer2():
            loop = asyncio.get_event_loop()
            executor = ThreadPoolExecutor(max_workers=2)

            tasks = []
            if "injection" not in disabled:
                tasks.append(
                    loop.run_in_executor(executor, _INJECTION_AGENT.run, event, state)
                )
            if "validator" not in disabled:
                tasks.append(
                    loop.run_in_executor(executor, _VALIDATOR_AGENT.run, event, state)
                )

            return await asyncio.gather(*tasks, return_exceptions=False)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(_run_layer2())
        except Exception:
            # Fallback to sequential if async fails
            results = []
            if "injection" not in disabled:
                results.append(_INJECTION_AGENT.run(event, state))
            if "validator" not in disabled:
                results.append(_VALIDATOR_AGENT.run(event, state))
            return results
