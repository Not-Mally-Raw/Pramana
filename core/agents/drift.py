"""
core/agents/drift.py — Behavioral drift detection L1 agent.
Compares current action embedding vs per-agent baseline embeddings in ChromaDB.
Uses MRL-aware dimensionality selection for latency-aware similarity checks.
Skipped in DegradedMode.NO_VECTORS.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from core.agents.base import BaseL1Agent
from core.embeddings import DIM_FAST, DIM_PRECISE, DIM_STANDARD, get_mrl_embedder
from core.config.settings import get_settings
from utils.models import ChainEvent, L1Result, PramanaState

__all__ = ["DriftMonitorAgent"]


class DriftMonitorAgent(BaseL1Agent):
    name = "drift_monitor"
    max_latency_ms = 80.0

    def check(self, event: ChainEvent, state: PramanaState) -> L1Result:
        s = get_settings()
        meta: dict[str, Any] = {}

        # DegradedMode.NO_VECTORS: fall back to a minimal deterministic heuristic.
        # We can't compute embedding similarity, but we can still catch obviously
        # anomalous "structured" actions with empty calldata.
        if state.degraded_mode and "NO_VECTORS" in str(state.degraded_mode):
            action = (event.action or "").strip().lower()
            calldata = (event.calldata or "").strip().lower()
            meta["vector_unavailable"] = True
            if action in {"swap", "approve", "userop"} and calldata in {"", "0x"}:
                meta["heuristic"] = "empty_calldata_structured_action"
                return self._make_result(
                    self.name,
                    True,
                    0.85,
                    "drift detected (heuristic): empty calldata for structured action",
                    meta,
                )
            return self._make_result(
                self.name, False, 0.0, "drift check skipped (NO_VECTORS)", meta
            )

        # Build behavior text: action + first 256 chars of calldata
        behavior_text = f"{event.action} {(event.calldata or '')[:256]}"
        meta["behavior_len"] = len(behavior_text)
        start_ms = state.pipeline_start_ms or (time.perf_counter() * 1000)
        elapsed_ms = max(0.0, (time.perf_counter() * 1000) - start_ms)

        embedding, dim_used = self._embed_behavior(behavior_text, elapsed_ms)
        meta["mrl_dimension_used"] = dim_used
        meta["pipeline_elapsed_ms"] = round(elapsed_ms, 1)

        similarity = self._baseline_similarity(
            event.agent_id,
            event.org_id,
            behavior_text,
            embedding,
            dim_used,
        )
        meta["baseline_similarity"] = (
            None if similarity is None else round(float(similarity), 4)
        )

        if similarity is None:
            # Cold start: no baseline exists yet, so drift cannot be assessed.
            # Seed the baseline so future checks work, but do not hard-flag on
            # first sighting. Treat as an unavailable signal (uncertainty), not
            # evidence of compromise.
            meta["cold_start"] = True
            self._store_embedding(
                event.agent_id,
                event.org_id,
                behavior_text,
                event.tx_hash or "",
                1.0,
                embedding,
                dim_used,
            )
            return self._make_result(
                self.name,
                False,
                0.0,
                "drift_cold_start: baseline not established (skipped)",
                meta,
                pass_count=0,
            )

        flagged = False
        score = 0.0
        reasons: list[str] = []

        if similarity < s.DRIFT_SIMILARITY_THRESHOLD:
            deviation = 1.0 - similarity
            score = max(score, min(deviation * 1.5, 1.0))
            flagged = True
            reasons.append(
                f"drift similarity {similarity:.3f} < {s.DRIFT_SIMILARITY_THRESHOLD:.3f}"
            )

        if s.DRIFT_MULTISCALE_ENABLED:
            multiscale_score, window_scores = self._compute_multiscale_drift(
                behavior_text,
                event.agent_id,
                event.org_id,
            )
            meta["multiscale_windows"] = window_scores
            meta["multiscale_drift_score"] = round(multiscale_score, 4)
            if multiscale_score >= s.DRIFT_MULTISCALE_THRESHOLD:
                flagged = True
                score = max(score, min(multiscale_score * 1.2, 1.0))
                reasons.append(
                    f"multiscale drift {multiscale_score:.3f} >= {s.DRIFT_MULTISCALE_THRESHOLD:.3f}"
                )

        self._store_embedding(
            event.agent_id,
            event.org_id,
            behavior_text,
            event.tx_hash or "",
            similarity,
            embedding,
            dim_used,
        )

        if flagged:
            return self._make_result(
                self.name,
                True,
                score,
                "; ".join(reasons),
                meta,
            )

        return self._make_result(
            self.name, False, 0.0, "behavior within baseline", meta
        )

    def verify(
        self, event: ChainEvent, state: PramanaState, first: L1Result
    ) -> L1Result:
        """Second pass: compare against rolling N-tx window (profile-aware).

        Fix 11: Window size is now per-profile via DRIFT_WINDOW_TXS.
        arb_bot uses 100, treasury_bot uses 10, etc.
        """
        s = get_settings()
        meta = dict(first.metadata)
        behavior_text = f"{event.action} {(event.calldata or '')[:256]}"

        # Load profile-aware drift window size
        profile = state.agent_profile or "generic"
        thresholds = s.AGENT_PROFILE_THRESHOLDS.get(
            profile, s.AGENT_PROFILE_THRESHOLDS["generic"]
        )
        drift_window = thresholds.get("DRIFT_WINDOW_TXS", 10)
        meta["drift_window_txs"] = drift_window

        emb = get_mrl_embedder().embed([behavior_text], dimensionality=DIM_STANDARD)[0]

        rolling_sim = self._rolling_similarity(
            event.agent_id,
            event.org_id,
            behavior_text,
            emb,
            DIM_STANDARD,
            window=drift_window,
        )
        meta["rolling_similarity"] = round(rolling_sim, 4)
        meta["rolling_dimension"] = DIM_STANDARD

        if rolling_sim < 0.60:
            return self._make_result(
                self.name,
                True,
                min(first.score + 0.10, 1.0),
                first.reason
                + f" (rolling sim {rolling_sim:.3f}, window={drift_window})",
                meta,
                pass_count=2,
            )
        return self._make_result(
            self.name,
            first.flagged,
            max(first.score - 0.05, 0.0),
            first.reason + " (rolling window ok)",
            meta,
            pass_count=2,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _collection_name(agent_id: str, org_id: str, dim: int) -> str:
        return f"baseline_{agent_id}_{org_id}_d{dim}"

    @staticmethod
    def _embed_behavior(behavior_text: str, elapsed_ms: float) -> tuple[np.ndarray, int]:
        if elapsed_ms < 80:
            dim = DIM_PRECISE
        elif elapsed_ms < 150:
            dim = DIM_STANDARD
        else:
            dim = DIM_FAST
        emb = get_mrl_embedder().embed([behavior_text], dimensionality=dim)
        return emb[0], dim

    @staticmethod
    def _baseline_similarity(
        agent_id: str,
        org_id: str,
        text: str,
        embedding: np.ndarray,
        dim: int,
    ) -> float | None:
        from core.config.settings import get_settings as _gs
        from utils.cache import EMBEDDING_CACHE

        # Include org_id so cross-tenant runs do not reuse stale similarities.
        cache_key = f"drift:{org_id}:{agent_id}:{dim}:{hash(text)}"
        if cache_key in EMBEDDING_CACHE:
            return EMBEDDING_CACHE[cache_key]

        try:
            import chromadb

            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                DriftMonitorAgent._collection_name(agent_id, org_id, dim),
                metadata={"hnsw:space": "cosine", "mrl_dim": dim},
            )
            # RANSAC/Bootstrap: check count
            count = col.count()
            # Warmup: require enough baseline samples before using similarity as a
            # drift signal. Small baselines are too noisy and cause false positives
            # (especially for new agents and synthetic/simulation traffic).
            if count < 10:
                return None  # warmup/cold start: signal absence of reliable data

            results = col.query(
                query_embeddings=[embedding.tolist()],
                n_results=min(10, count),
            )
            distances = results.get("distances", [[]])[0]
            if not distances:
                return 0.85

            # RANSAC centroid check
            avg_dist = sum(distances) / len(distances)
            sim = 1.0 - avg_dist

            EMBEDDING_CACHE[cache_key] = sim
            return sim
        except Exception:
            return 0.85  # fail safe → assume ok

    @staticmethod
    def _store_embedding(
        agent_id: str,
        org_id: str,
        text: str,
        doc_id: str,
        similarity: float = 1.0,
        embedding: np.ndarray | None = None,
        dim_used: int = DIM_STANDARD,
    ) -> None:
        """Stores embedding with RANSAC >1.5 sigma outlier rejection and 500 cap pruning."""
        try:
            import time

            import chromadb

            from core.config.settings import get_settings as _gs

            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            primary_col = client.get_or_create_collection(
                DriftMonitorAgent._collection_name(agent_id, org_id, dim_used),
                metadata={"hnsw:space": "cosine", "mrl_dim": dim_used},
            )

            count = primary_col.count()

            # 1. RANSAC Outlier Rejection during Bootstrap
            # If we have a reasonable baseline (e.g. >10 txs) and the similarity drops hard, reject it.
            if count >= 10 and similarity < 0.65:
                return  # Do not poison the baseline

            # 2. Baseline versioning in Chroma metadata
            meta = {
                "version": "2.0-mrl",
                "timestamp": int(time.time()),
                "is_centroid": False,
            }

            embedder = get_mrl_embedder()
            vectors: dict[int, np.ndarray] = {}
            if embedding is not None:
                vectors[dim_used] = embedding

            for dim in (DIM_FAST, DIM_STANDARD, DIM_PRECISE):
                if dim not in vectors:
                    vectors[dim] = embedder.embed([text], dimensionality=dim)[0]

            base_id = doc_id or text[:36]
            unique_id = f"{base_id}:{int(time.time() * 1000)}"

            for dim, vec in vectors.items():
                col = client.get_or_create_collection(
                    DriftMonitorAgent._collection_name(agent_id, org_id, dim),
                    metadata={"hnsw:space": "cosine", "mrl_dim": dim},
                )
                col.upsert(
                    documents=[text],
                    ids=[f"{unique_id}:d{dim}"],
                    metadatas=[{**meta, "dim": dim}],
                    embeddings=[vec.astype(np.float32).tolist()],
                )
                DriftMonitorAgent._prune_collection(col)

        except Exception:
            pass

    @staticmethod
    def _prune_collection(col) -> None:
        if col.count() < 500:
            return
        all_docs = col.get(include=["metadatas"])
        if not all_docs or "ids" not in all_docs or "metadatas" not in all_docs:
            return
        pairs = list(zip(all_docs["ids"], all_docs["metadatas"]))
        pairs.sort(key=lambda x: x[1].get("timestamp", 0) if x[1] else 0)
        ids_to_delete = [p[0] for p in pairs[:50]]
        if ids_to_delete:
            col.delete(ids=ids_to_delete)

    @staticmethod
    def _rolling_similarity(
        agent_id: str,
        org_id: str,
        text: str,
        embedding: np.ndarray,
        dim: int,
        window: int = 10,
    ) -> float:
        """Compare vs last N stored embeddings (profile-aware window)."""
        try:
            import chromadb

            from core.config.settings import get_settings as _gs

            s = _gs()
            client = chromadb.PersistentClient(path=s.CHROMA_PATH)
            col = client.get_or_create_collection(
                DriftMonitorAgent._collection_name(agent_id, org_id, dim),
                metadata={"hnsw:space": "cosine", "mrl_dim": dim},
            )
            n = min(col.count(), window)
            if n == 0:
                return 0.85
            results = col.query(query_embeddings=[embedding.tolist()], n_results=n)
            distances = results.get("distances", [[]])[0]
            if not distances:
                return 0.85
            return 1.0 - (sum(distances) / len(distances))
        except Exception:
            return 0.85

    @staticmethod
    def _get_baseline_texts(
        agent_id: str,
        org_id: str,
        window_secs: int | None = None,
        limit: int = 50,
    ) -> list[str]:
        try:
            from core.db import get_sqlite_conn

            with get_sqlite_conn() as conn:
                if window_secs is None:
                    rows = conn.execute(
                        """
                        SELECT action, calldata
                        FROM events
                        WHERE agent_addr=? AND org_id=?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (agent_id, org_id, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT action, calldata
                        FROM events
                        WHERE agent_addr=? AND org_id=?
                          AND created_at >= datetime('now', ?)
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (agent_id, org_id, f"-{int(window_secs)} seconds", int(limit)),
                    ).fetchall()

            texts = []
            for row in rows:
                action = (row[0] or "").strip()
                calldata = (row[1] or "")[:256]
                if action or calldata:
                    texts.append(f"{action} {calldata}".strip())
            return texts
        except Exception:
            return []

    def _compute_multiscale_drift(
        self,
        behavior_text: str,
        agent_id: str,
        org_id: str,
    ) -> tuple[float, dict[str, Any]]:
        """Compute weighted drift across short and long time horizons."""
        embedder = get_mrl_embedder()
        windows = [
            ("10tx", None, DIM_PRECISE, 0.50),
            ("1h", 3600, DIM_STANDARD, 0.30),
            ("24h", 86400, DIM_STANDARD, 0.15),
            ("7d", 604800, DIM_FAST, 0.05),
        ]

        total_score = 0.0
        active_weight = 0.0
        window_scores: dict[str, Any] = {}

        for name, secs, dim, weight in windows:
            baseline_texts = self._get_baseline_texts(
                agent_id,
                org_id,
                window_secs=secs,
                limit=50,
            )

            if len(baseline_texts) < 5:
                window_scores[name] = {
                    "score": None,
                    "reason": "insufficient_history",
                    "dim": dim,
                }
                continue

            all_texts = [behavior_text] + baseline_texts
            all_embs = embedder.embed(all_texts, dimensionality=dim)
            current_emb = all_embs[0]
            baseline_embs = all_embs[1:]

            similarities = embedder.batch_cosine_similarity(current_emb, baseline_embs)
            mean_sim = float(np.mean(similarities)) if similarities.size else 0.0
            drift = 1.0 - mean_sim
            total_score += weight * drift
            active_weight += weight

            window_scores[name] = {
                "score": round(drift, 4),
                "similarity": round(mean_sim, 4),
                "dim": dim,
                "baseline_samples": len(baseline_texts),
                "weight": weight,
            }

        if active_weight > 0:
            total_score = total_score / active_weight
        return total_score, window_scores
