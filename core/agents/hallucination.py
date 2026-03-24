"""
core/agents/hallucination.py — Hallucination/off-policy output L1 agent.
Pass 1: Chroma cosine similarity vs baseline embeddings. <100ms.
Pass 2 (borderline 0.65–0.80): DuckDB historical success rate lookup.
NO Groq calls at L1. Only pattern-based + vector similarity.
"""
from __future__ import annotations

from typing import Any

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["HallucinationAgent"]


class HallucinationAgent(BaseL1Agent):
    name = "hallucination"
    max_latency_ms = 100.0

    # Known bad patterns in calldata (quick pre-filter before vector check)
    _BAD_PATTERNS = [
        "ignore previous",
        "disregard instructions",
        "override policy",
        "transfer all",
        "send to 0x0000",
        "selfdestruct",
        "delegatecall",
    ]

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        meta: dict[str, Any] = {}
        score = 0.0
        issues: list[str] = []

        calldata_lower = (event.calldata or "").lower()

        # ── Quick pattern pre-filter ────────────────────────────────────────
        matched = [p for p in self._BAD_PATTERNS if p in calldata_lower]
        if matched:
            issues.append(f"suspicious patterns: {', '.join(matched)}")
            score = max(score, 0.85)
            meta["patterns_matched"] = matched
            return self._make_result(self.name, True, score, "; ".join(issues), meta)

        # ── Chroma vector similarity ────────────────────────────────────────
        # DegradedMode.NO_VECTORS → skip vector check
        if state.degraded_mode and "NO_VECTORS" in str(state.degraded_mode):
            meta["vector_unavailable"] = True
            return self._make_result(
                self.name,
                False,
                min(0.25, s.HALLUC_SCORE_THRESHOLD * 0.5),
                "vector check skipped (degraded)",
                meta,
                pass_count=0,
            )

        similarity = self._chroma_similarity(event.agent_id, event.org_id, event.calldata or "")
        if similarity is None:
            # Fix 9: Cold-start now flags conservatively instead of passing.
            # New agents with no baseline should be escalated, not trusted.
            meta["vector_unavailable"] = True
            meta["warmup"] = True
            return self._make_result(
                self.name,
                True,   # was: False — silently passed new agents
                0.50,   # was: 0.25 — too permissive
                "warmup_period: no vector baseline established",
                meta,
                pass_count=1,
            )

        meta["chroma_similarity"] = round(similarity, 4)

        # Low similarity → output deviates from baseline behavior
        if similarity < s.HALLUC_SCORE_THRESHOLD:
            deviation = 1.0 - similarity
            score = max(score, min(deviation * 1.2, 1.0))
            issues.append(f"output cosine similarity {similarity:.3f} < threshold {s.HALLUC_SCORE_THRESHOLD}")

        flagged = score >= 0.50
        reason = "; ".join(issues) if issues else "hallucination nominal"
        return self._make_result(self.name, flagged, score, reason, meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: look up historical success rate for this action type."""
        meta = dict(first.metadata)
        success_rate = self._historical_success_rate(event.agent_id, event.org_id, event.action)
        meta["historical_success_rate"] = round(success_rate, 3)

        # Low historical success rate for this specific action reinforces flag
        if success_rate < 0.50:
            return self._make_result(
                self.name, True,
                min(first.score + (0.5 - success_rate) * 0.3, 1.0),
                first.reason + f" (hist success {success_rate:.0%})", meta, pass_count=2
            )
        return self._make_result(
            self.name, first.flagged, max(first.score - 0.05, 0.0),
            first.reason + " (hist ok)", meta, pass_count=2
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _chroma_similarity(agent_id: str, org_id: str, text: str) -> float | None:
        """Query ChromaDB for cosine similarity vs baseline corpus. Returns None when unavailable."""
        from utils.cache import EMBEDDING_CACHE
        cache_key = f"{agent_id}:{hash(text[:256])}"
        if cache_key in EMBEDDING_CACHE:
            return EMBEDDING_CACHE[cache_key]
        try:
            import chromadb
            from core.config.settings import get_settings as _gs
            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                s.CHROMA_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            if col.count() == 0:
                return None
            results = col.query(query_texts=[text[:512]], n_results=1)
            distances = results.get("distances", [[]])[0]
            if not distances:
                return None
            similarity = 1.0 - distances[0]
            EMBEDDING_CACHE[cache_key] = similarity
            return similarity
        except Exception:
            return None

    @staticmethod
    def _historical_success_rate(agent_addr: str, org_id: str, action: str) -> float:
        try:
            from core.db import get_duck_conn
            with get_duck_conn() as conn:
                row = conn.execute(
                    """
                    SELECT AVG(success_pct) FROM agent_metrics
                    WHERE agent_addr=? AND org_id=?
                    ORDER BY window_ts DESC LIMIT 10
                    """,
                    [agent_addr, org_id],
                ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.5
        except Exception:
            return 0.5
