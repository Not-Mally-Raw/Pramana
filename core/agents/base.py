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
        """
        Run the two-pass verification and return final L1Result.
        Tracks total latency across both passes.
        """
        t0 = time.perf_counter()
        first = self.check(event, state)
        pass_count = 1

        # Enter second pass only if score is in borderline band
        if BORDERLINE_LOW <= first.score <= BORDERLINE_HIGH:
            second = self.verify(event, state, first)
            pass_count = 2
            # Use second-pass result; if score changed, trust it
            result = second
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
