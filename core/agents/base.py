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

__all__ = ["BaseL1Agent"]

# Borderline band: re-check if score is in [low, high]
BORDERLINE_LOW  = 0.60
BORDERLINE_HIGH = 0.80


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

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        """First-pass check. Must run in < max_latency_ms."""

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
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

        pass_count = 1

        # Enter second pass only if score is in borderline band
        if self.borderline_low <= first.score <= self.borderline_high:
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
