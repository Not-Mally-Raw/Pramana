"""
Matryoshka Representation Learning embedding manager for Pramana.

Uses nomic-embed-text-v1.5 in local mode when available. The model is
Matryoshka-trained, so truncated dimensions remain meaningful.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

from core.config.settings import get_settings

logger = logging.getLogger("pramana.mrl")

DIM_FAST = 64
DIM_STANDARD = 256
DIM_PRECISE = 768


class MRLEmbedder:
    """Thread-safe singleton embedder with MRL-aware dimensionality selection."""

    _instance: Optional["MRLEmbedder"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return

            s = get_settings()
            self._model = s.MRL_MODEL
            self._inference_mode = s.MRL_INFERENCE_MODE
            self._device = s.MRL_DEVICE or None
            self._available = False
            self._embed_fn = None

            try:
                from nomic import embed as nomic_embed

                self._embed_fn = nomic_embed.text
                logger.info(
                    "Loading %s with nomic (%s mode)...",
                    self._model,
                    self._inference_mode,
                )
                start = time.perf_counter()
                self._embed_fn(
                    texts=["warmup"],
                    model=self._model,
                    task_type="search_document",
                    dimensionality=DIM_STANDARD,
                    inference_mode=self._inference_mode,
                    device=self._device,
                )
                warmup_ms = (time.perf_counter() - start) * 1000
                logger.info("MRL embedder ready via nomic. Warmup: %.0fms", warmup_ms)
                self._available = True
            except Exception as e:
                logger.warning("nomic local embedder unavailable (%s), using fallback", e)

            self._initialized = True

    def embed(
        self,
        texts: list[str],
        dimensionality: int = DIM_STANDARD,
        task_type: str = "search_document",
    ) -> np.ndarray:
        """Embed text list at requested dimensionality."""
        self._ensure_initialized()
        dim = max(16, int(dimensionality))

        if self._available:
            return self._embed_nomic(texts, dim, task_type)
        return self._embed_fallback(texts, dim)

    def _embed_nomic(self, texts: list[str], dimensionality: int, task_type: str) -> np.ndarray:
        try:
            output = self._embed_fn(
                texts=texts,
                model=self._model,
                task_type=task_type,
                dimensionality=dimensionality,
                inference_mode=self._inference_mode,
                device=self._device,
            )
            return np.array(output["embeddings"], dtype=np.float32)
        except Exception as e:
            # Degrade once to fallback to avoid repeated remote/token failures flooding logs.
            logger.warning("MRL nomic embed failed; switching to fallback embeddings: %s", str(e)[:180])
            self._available = False
            return self._embed_fallback(texts, dimensionality)

    def _embed_fallback(self, texts: list[str], dimensionality: int) -> np.ndarray:
        """Fallback path when nomic local runtime is unavailable."""
        try:
            from sentence_transformers import SentenceTransformer

            if not hasattr(self, "_st_model"):
                self._st_model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = self._st_model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
            )
            truncated = embeddings[:, : min(dimensionality, embeddings.shape[1])]
            norms = np.linalg.norm(truncated, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            return (truncated / norms).astype(np.float32)
        except Exception as e:
            logger.error("Fallback embedder failed: %s", e)
            arr = np.random.randn(len(texts), dimensionality).astype(np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            return arr / np.where(norms == 0, 1, norms)

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        if len(a.shape) == 1 and len(b.shape) == 1:
            return float(np.dot(a, b))
        return float(np.dot(a.flatten(), b.flatten()))

    @staticmethod
    def batch_cosine_similarity(query: np.ndarray, corpus: np.ndarray) -> np.ndarray:
        if len(query.shape) == 1:
            query = query.reshape(1, -1)
        return (corpus @ query.T).flatten()


_embedder: Optional[MRLEmbedder] = None


def get_mrl_embedder() -> MRLEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = MRLEmbedder()
    return _embedder
