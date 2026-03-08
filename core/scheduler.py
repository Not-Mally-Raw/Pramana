"""
core/scheduler.py — APScheduler background jobs.
Runs reputation updates, DB eviction, and health checks on configurable intervals.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger("pramana.scheduler")

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def start_scheduler() -> None:
    s = get_scheduler()
    from core.config.settings import get_settings
    cfg = get_settings()

    # ── Job 1: Reputation update every 5 minutes ──────────────────────────
    s.add_job(
        _update_reputation_all_orgs,
        trigger="interval",
        seconds=300,
        id="reputation_update",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 2: SQLite LRU eviction every 10 minutes ────────────────────────
    s.add_job(
        _evict_old_events,
        trigger="interval",
        seconds=600,
        id="db_eviction",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 3: Routing table cache invalidation every 30 seconds ──────────
    s.add_job(
        _refresh_routing_cache,
        trigger="interval",
        seconds=30,
        id="routing_refresh",
        max_instances=1,
        coalesce=True,
    )

    # ── Job 4: Node health check every 60 seconds ───────────────────────────
    s.add_job(
        _node_health_check,
        trigger="interval",
        seconds=60,
        id="node_health",
        max_instances=1,
        coalesce=True,
    )

    s.start()
    logger.info("APScheduler started with %d jobs", len(s.get_jobs()))


def stop_scheduler() -> None:
    s = get_scheduler()
    if s.running:
        s.shutdown(wait=False)
        logger.info("APScheduler stopped")


# ── Job implementations ───────────────────────────────────────────────────────

async def _update_reputation_all_orgs() -> None:
    try:
        from core.agents.reputation import ReputationAgent
        from core.db import get_sqlite_conn
        with get_sqlite_conn() as conn:
            orgs = conn.execute("SELECT DISTINCT org_id FROM agents").fetchall()
        agent = ReputationAgent()
        for (org_id,) in orgs:
            agent.update_org(org_id)
    except Exception as e:
        logger.warning("Reputation update failed: %s", e)


async def _evict_old_events() -> None:
    try:
        from core.db import evict_old_rows, get_sqlite_conn
        with get_sqlite_conn() as conn:
            orgs = conn.execute("SELECT DISTINCT org_id FROM events").fetchall()
        for (org_id,) in orgs:
            evict_old_rows(org_id, max_rows=10_000)
    except Exception as e:
        logger.warning("DB eviction failed: %s", e)


async def _refresh_routing_cache() -> None:
    try:
        from utils.cache import invalidate_routing_cache
        invalidate_routing_cache()
    except Exception as e:
        logger.debug("Routing cache refresh: %s", e)


async def _node_health_check() -> None:
    try:
        import requests
        from core.config.settings import get_settings
        s = get_settings()
        resp = requests.post(
            s.active_rpc,
            json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 99},
            timeout=3,
        )
        if resp.status_code != 200:
            logger.warning("Node health check failed: HTTP %d", resp.status_code)
    except Exception as e:
        logger.warning("Node health check failed: %s", e)
