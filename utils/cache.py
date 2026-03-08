"""
utils/cache.py — LRU + TTL caches for Pramãna.

All caches are in-process (no Redis). Uses:
  - functools.lru_cache  for pure function memoization
  - cachetools.TTLCache  for time-bounded caches (e.g. routing table, LLM results)
  - Thread-safe via cachetools.LRUCache with threading.Lock

Key caches:
  1. RoutingTableCache   — escalation rules dict (TTL=30s, auto-reload from SQLite)
  2. LLMResultCache      — last N Groq results per (agent_id, action) key (TTL=5min)
  3. PolicyCache         — guard policies per agent (TTL=60s)
  4. OracleDataCache     — gas price / chain data (TTL=15s)
  5. EmbeddingCache      — Chroma embedding results (TTL=5min, LRU 512)
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from cachetools import LRUCache, TTLCache

F = TypeVar("F", bound=Callable[..., Any])

# ─── Routing Table ────────────────────────────────────────────────────────────
# O(1) dict lookup, TTL=30s so dashboard changes apply within 30s.
_routing_lock = threading.Lock()
_routing_cache: TTLCache = TTLCache(maxsize=1, ttl=30)


def get_routing_table(loader: Callable[[], dict]) -> dict:
    """
    Return cached routing table or reload from SQLite.
    loader should be: lambda: load_routing_rules_from_db()
    Thread-safe via lock.
    """
    with _routing_lock:
        if "rules" not in _routing_cache:
            _routing_cache["rules"] = loader()
        return _routing_cache["rules"]


def invalidate_routing_cache() -> None:
    """Call this after POST /config/routing to force immediate reload."""
    with _routing_lock:
        _routing_cache.clear()


# ─── LLM Result Cache ─────────────────────────────────────────────────────────
# Avoid redundant Groq calls for identical agent+action patterns.
# Key: (agent_id, action_hash) → L1Result / CouncilResult
# TTL=300s (5 min), LRU max 256 entries, ~1KB each = ~256KB max.
_llm_cache: TTLCache = TTLCache(maxsize=256, ttl=300)
_llm_lock  = threading.Lock()


def get_llm_cache(key: tuple) -> Any | None:
    with _llm_lock:
        return _llm_cache.get(key)


def set_llm_cache(key: tuple, value: Any) -> None:
    with _llm_lock:
        _llm_cache[key] = value


def llm_cache_stats() -> dict:
    with _llm_lock:
        return {
            "size":    len(_llm_cache),
            "maxsize": _llm_cache.maxsize,
            "ttl":     _llm_cache.ttl,
            "hits":    getattr(_llm_cache, "_hits", 0),
        }


# ─── Policy Cache ─────────────────────────────────────────────────────────────
# Guard policies per (agent_id, org_id). TTL=60s.
_policy_cache: TTLCache = TTLCache(maxsize=128, ttl=60)
_policy_lock  = threading.Lock()


def get_policy_cache(agent_id: int, org_id: str) -> Any | None:
    with _policy_lock:
        return _policy_cache.get((agent_id, org_id))


def set_policy_cache(agent_id: int, org_id: str, policy: Any) -> None:
    with _policy_lock:
        _policy_cache[(agent_id, org_id)] = policy


def invalidate_policy_cache(agent_id: int, org_id: str) -> None:
    with _policy_lock:
        _policy_cache.pop((agent_id, org_id), None)


# ─── Oracle / Chain Data Cache ────────────────────────────────────────────────
# Gas price, block number, chain latency. Short TTL=15s.
_oracle_cache: TTLCache = TTLCache(maxsize=32, ttl=15)
_oracle_lock  = threading.Lock()


def get_oracle_cache(key: str) -> Any | None:
    with _oracle_lock:
        return _oracle_cache.get(key)


def set_oracle_cache(key: str, value: Any) -> None:
    with _oracle_lock:
        _oracle_cache[key] = value


# ─── Embedding Cache ──────────────────────────────────────────────────────────
# Chroma similarity results per (agent_id, text_hash). TTL=5min, LRU 512.
_embed_cache: TTLCache = TTLCache(maxsize=512, ttl=300)
_embed_lock  = threading.Lock()


def get_embed_cache(agent_id: int, text_hash: str) -> float | None:
    with _embed_lock:
        return _embed_cache.get((agent_id, text_hash))


def set_embed_cache(agent_id: int, text_hash: str, score: float) -> None:
    with _embed_lock:
        _embed_cache[(agent_id, text_hash)] = score


# ─── Rate Limit Tracker ───────────────────────────────────────────────────────
# Count Groq calls per minute to stay under 25 RPM (buffer below 30 RPM free limit).
_groq_calls: list[float] = []
_groq_lock  = threading.Lock()
GROQ_RPM_LIMIT = 25


def can_call_groq() -> bool:
    """Returns True if we haven't hit 25 RPM. O(n) where n ≤ 25 entries."""
    now = time.time()
    with _groq_lock:
        # Prune calls older than 60s
        _groq_calls[:] = [t for t in _groq_calls if now - t < 60]
        return len(_groq_calls) < GROQ_RPM_LIMIT


def record_groq_call() -> None:
    with _groq_lock:
        _groq_calls.append(time.time())


def groq_calls_last_minute() -> int:
    now = time.time()
    with _groq_lock:
        return sum(1 for t in _groq_calls if now - t < 60)


# ─── Generic TTL Decorator ────────────────────────────────────────────────────

def ttl_cached(ttl_seconds: int = 60, maxsize: int = 128):
    """
    Decorator for caching function results with TTL.
    Usage: @ttl_cached(ttl_seconds=30) def my_func(x): ...
    """
    cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
    lock = threading.Lock()

    def decorator(func: F) -> F:
        def wrapper(*args, **kwargs):
            # Build a hashable key from args — kwargs must be hashable
            key = (args, tuple(sorted(kwargs.items())))
            with lock:
                if key in cache:
                    return cache[key]
            result = func(*args, **kwargs)
            with lock:
                cache[key] = result
            return result
        wrapper.__wrapped__ = func  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
