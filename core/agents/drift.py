"""
core/agents/drift.py — Behavioral drift detection L1 agent.
Compares current action embedding vs per-agent baseline embeddings in ChromaDB.
Pass 1: cosine similarity vs baseline cluster. <80ms.
Pass 2 (borderline): compare vs 10-tx rolling window average.
Skipped in DegradedMode.NO_VECTORS.
"""
from __future__ import annotations

from typing import Any

from core.agents.base import BaseL1Agent
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["DriftMonitorAgent"]


class DriftMonitorAgent(BaseL1Agent):
    name = "drift_monitor"
    max_latency_ms = 80.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        meta: dict[str, Any] = {}

        # Skip if vectors unavailable
        if state.degraded_mode and "NO_VECTORS" in str(state.degraded_mode):
            return self._make_result(self.name, False, 0.0, "drift check skipped (NO_VECTORS)", meta)

        # Build behavior text: action + first 256 chars of calldata
        behavior_text = f"{event.action} {(event.calldata or '')[:256]}"
        meta["behavior_len"] = len(behavior_text)

        similarity = self._baseline_similarity(event.agent_id, event.org_id, behavior_text)
        meta["baseline_similarity"] = round(similarity, 4)

        if similarity < s.DRIFT_SIMILARITY_THRESHOLD:
            deviation = 1.0 - similarity
            score = min(deviation * 1.5, 1.0)
            reason = f"drift detected: similarity {similarity:.3f} < {s.DRIFT_SIMILARITY_THRESHOLD}"
            # Store new embedding for next comparison
            self._store_embedding(event.agent_id, event.org_id, behavior_text, event.tx_hash or "")
            return self._make_result(self.name, True, score, reason, meta)

        # Update baseline with new good behavior
        self._store_embedding(event.agent_id, event.org_id, behavior_text, event.tx_hash or "")
        return self._make_result(self.name, False, 0.0, "behavior within baseline", meta)

    def verify(self, event: ChainEvent, state: PramanaState, first: L1Result) -> L1Result:
        """Second pass: compare against rolling N-tx window (profile-aware).

        Fix 11: Window size is now per-profile via DRIFT_WINDOW_TXS.
        arb_bot uses 100, treasury_bot uses 10, etc.
        """
        s = get_settings()
        meta = dict(first.metadata)
        behavior_text = f"{event.action} {(event.calldata or '')[:256]}"

        # Load profile-aware drift window size
        profile = state.agent_profile or "generic"
        thresholds = s.AGENT_PROFILE_THRESHOLDS.get(profile, s.AGENT_PROFILE_THRESHOLDS["generic"])
        drift_window = thresholds.get("DRIFT_WINDOW_TXS", 10)
        meta["drift_window_txs"] = drift_window

        rolling_sim = self._rolling_similarity(
            event.agent_id, event.org_id, behavior_text, window=drift_window
        )
        meta["rolling_similarity"] = round(rolling_sim, 4)

        if rolling_sim < 0.60:
            return self._make_result(
                self.name, True,
                min(first.score + 0.10, 1.0),
                first.reason + f" (rolling sim {rolling_sim:.3f}, window={drift_window})",
                meta, pass_count=2
            )
        return self._make_result(
            self.name, first.flagged,
            max(first.score - 0.05, 0.0),
            first.reason + " (rolling window ok)", meta, pass_count=2
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _baseline_similarity(agent_id: str, org_id: str, text: str) -> float:
        from utils.cache import EMBEDDING_CACHE
        cache_key = f"drift:{agent_id}:{hash(text)}"
        if cache_key in EMBEDDING_CACHE:
            return EMBEDDING_CACHE[cache_key]
        try:
            import chromadb
            from core.config.settings import get_settings as _gs
            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                f"baseline_{agent_id}_{org_id}",
                metadata={"hnsw:space": "cosine"},
            )
            if col.count() == 0:
                return 0.85  # no baseline yet → assume ok
            results = col.query(query_texts=[text], n_results=3)
            distances = results.get("distances", [[]])[0]
            if not distances:
                return 0.85
            avg_dist = sum(distances) / len(distances)
            sim = 1.0 - avg_dist
            EMBEDDING_CACHE[cache_key] = sim
            return sim
        except Exception:
            return 0.85  # fail safe → assume ok

    @staticmethod
    def _store_embedding(agent_id: str, org_id: str, text: str, doc_id: str) -> None:
        try:
            import chromadb
            from core.config.settings import get_settings as _gs
            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                f"baseline_{agent_id}_{org_id}",
                metadata={"hnsw:space": "cosine"},
            )
            col.upsert(documents=[text], ids=[doc_id or text[:36]])
        except Exception:
            pass

    @staticmethod
    def _rolling_similarity(agent_id: str, org_id: str, text: str, window: int = 10) -> float:
        """Compare vs last N stored embeddings (profile-aware window).

        Fix 11: Window size is now parameterized via DRIFT_WINDOW_TXS.
        """
        try:
            import chromadb
            from core.config.settings import get_settings as _gs
            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                f"baseline_{agent_id}_{org_id}",
                metadata={"hnsw:space": "cosine"},
            )
            n = min(col.count(), window)
            if n == 0:
                return 0.85
            results = col.query(query_texts=[text], n_results=n)
            distances = results.get("distances", [[]])[0]
            if not distances:
                return 0.85
            return 1.0 - (sum(distances) / len(distances))
        except Exception:
            return 0.85
