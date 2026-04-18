"""
core/agents/base.py — BaseL1Agent abstract class.
Two-pass template method: check() (first pass) → verify() (borderline second pass).
All L1 agents extend this.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["BaseL1Agent", "_human_reason"]

# Borderline band: re-check if score is in [low, high]
BORDERLINE_LOW = 0.60
BORDERLINE_HIGH = 0.80

HUMAN_REASONS = {
    "sanctions_check_pass": "No OFAC sanctions matches found for this address.",
    "sanctions_match": "Warning: address matches OFAC sanctions list; transaction blocked.",
    "injection_detected": "Warning: adversarial text pattern detected in transaction data; possible prompt injection attack.",
    "injection_clean": "No injection patterns found in transaction calldata.",
    "hallucination_similar": "Agent behavior matches established baseline. Acting as expected.",
    "hallucination_drift": "Warning: agent behavior deviates significantly from baseline; possible drift or compromise.",
    "drift_cold_start": "No behavioral baseline yet. Cannot assess drift on a new agent.",
    "drift_normal": "Behavioral pattern is within the normal range for this agent type.",
    "economic_clean": "Gas usage, value transfer, and timing are within expected parameters.",
    "economic_anomaly": "Warning: unusual economic pattern detected; abnormal gas or value transfer.",
    "operations_normal": "Transaction frequency and action patterns are normal.",
    "operations_loop": "Warning: rapid repeated actions detected; possible loop or exploit.",
    "network_clean": "Network conditions and RPC state appear normal.",
    "aa_valid": "UserOperation structure is valid for ERC-4337 EntryPoint.",
    "aa_invalid": "Warning: UserOperation structure violates ERC-4337 specification.",
    "validator_pass": "Transaction passes structural validation checks.",
    "validator_fail": "Warning: transaction fails structural validation.",
}


def _human_reason(technical_reason: str) -> str:
    """Convert a technical reason string into a human-readable explanation."""
    lowered = (technical_reason or "").lower()
    for key, human_text in HUMAN_REASONS.items():
        if key in lowered:
            return human_text
    if not technical_reason:
        return "Analysis complete."
    return f"Analysis complete: {technical_reason[:100]}"


class BaseL1Agent(ABC):
    """
    Two-pass deterministic verification (Symphony-derived).
    Pass 1: fast check (always runs, O(ms)).
    Pass 2: deeper verify (only if borderline, still deterministic, 0 Groq calls).
    """

    name: str = "base_l1_agent"
    max_latency_ms: float = 100.0  # override per agent
    borderline_low: float = BORDERLINE_LOW
    borderline_high: float = BORDERLINE_HIGH
    # Some agents need deterministic second-pass verification even when the
    # first-pass score is not in the borderline band (e.g., RPC-backed checks).
    always_verify: bool = False

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        """First-pass check. Must run in < max_latency_ms."""

    def verify(
        self, event: ChainEvent, state: PramanaState, first: L1Result
    ) -> L1Result:
        """
        Optional second-pass verification when score is borderline.
        Default: return first result unchanged.
        Override to add deeper deterministic checks.
        """
        return first

    # ── Template method ───────────────────────────────────────────────────────

    def run(self, event: ChainEvent, state: PramanaState) -> L1Result:
        """Run the two-pass verification and return final L1Result.

        Fix 8: Added structured exception handling. Instead of silently
        returning a clean score on infrastructure failure, a 'degraded'
        result is returned with flagged=True, score=0.5. This routes
        degraded results to escalation, not pass.
        """
        import logging

        logger = logging.getLogger(f"pramana.agent.{self.name}")

        t0 = time.perf_counter()
        try:
            first = self.check(event, state)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error(f"Agent {self.name} check() failed: {e}", exc_info=True)
            return L1Result(
                agent_name=self.name,
                flagged=True,
                score=0.5,
                reason=f"agent_degraded: {type(e).__name__}: {str(e)[:100]}",
                latency_ms=round(elapsed_ms, 2),
                pass_count=0,
                metadata={"degraded": True, "error": str(e)[:200]},
            )

        # Preserve explicit skip/degraded markers from check() implementations.
        # Convention: pass_count == 0 means the agent produced no reliable signal
        # (skipped/degraded), and should contribute to uncertainty in fusion.
        pass_count = 0 if getattr(first, "pass_count", 1) == 0 else 1

        # Enter second pass if score is in borderline band (default), or if the
        # agent explicitly requests always-on verification.
        # If the first pass was explicitly skipped/degraded (pass_count==0),
        # do not attempt a second pass.
        should_verify = pass_count != 0 and (
            self.borderline_low <= first.score <= self.borderline_high or self.always_verify
        )
        if should_verify:
            try:
                second = self.verify(event, state, first)
                pass_count = 2
                result = second
            except Exception as e:
                logger.error(f"Agent {self.name} verify() failed: {e}", exc_info=True)
                # On verify failure, use first-pass result but flag as degraded
                first_meta = dict(first.metadata)
                first_meta["degraded"] = True
                first_meta["verify_error"] = str(e)[:200]
                result = L1Result(
                    agent_name=self.name,
                    flagged=True,
                    score=max(first.score, 0.5),
                    reason=f"{first.reason} + verify_degraded: {type(e).__name__}",
                    latency_ms=0.0,
                    pass_count=1,
                    metadata=first_meta,
                )
        else:
            result = first

        elapsed_ms = (time.perf_counter() - t0) * 1000
        return L1Result(
            agent_name=self.name,
            flagged=result.flagged,
            score=result.score,
            reason=result.reason,
            latency_ms=round(elapsed_ms, 2),
            pass_count=pass_count,
            metadata=result.metadata,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_result(
        agent_name: str,
        flagged: bool,
        score: float,
        reason: str,
        metadata: dict[str, Any] | None = None,
        pass_count: int = 1,
        latency_ms: float = 0.0,
    ) -> L1Result:
        return L1Result(
            agent_name=agent_name,
            flagged=flagged,
            score=score,
            reason=reason,
            latency_ms=latency_ms,
            pass_count=pass_count,
            metadata=metadata or {},
        )
