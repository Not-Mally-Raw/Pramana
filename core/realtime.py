"""Realtime stream hub for frontend websocket consumers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from core.agents.base import _human_reason

logger = logging.getLogger("pramana.realtime")


@dataclass
class StreamSubscriber:
    websocket: WebSocket
    org_id: str | None = None
    trace_id: str | None = None


class RealtimeHub:
    def __init__(self, max_events: int = 5000) -> None:
        self._subscribers: list[StreamSubscriber] = []
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def connected_clients(self) -> int:
        return len(self._subscribers)

    async def connect(
        self,
        websocket: WebSocket,
        org_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._subscribers.append(
                StreamSubscriber(websocket=websocket, org_id=org_id, trace_id=trace_id)
            )

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers = [
                sub for sub in self._subscribers if sub.websocket is not websocket
            ]

    def events_since(
        self,
        since: int,
        org_id: str | None = None,
        trace_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for event in list(self._events):
            if since > 0 and int(event.get("timestamp", 0)) <= since:
                continue
            if not self._matches_filters(event, org_id=org_id, trace_id=trace_id):
                continue
            events.append(event)
            if len(events) >= limit:
                break
        return events

    def publish(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("timestamp", int(time.time() * 1000))

        if self._loop is None:
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop and current_loop is self._loop:
            self._loop.create_task(self._broadcast(payload))
            return

        asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        self._events.append(event)

        async with self._lock:
            subscribers = list(self._subscribers)

        disconnected: list[WebSocket] = []
        for sub in subscribers:
            if not self._matches_filters(
                event, org_id=sub.org_id, trace_id=sub.trace_id
            ):
                continue
            try:
                await sub.websocket.send_json(event)
            except Exception:
                disconnected.append(sub.websocket)

        for websocket in disconnected:
            await self.disconnect(websocket)

    @staticmethod
    def _matches_filters(
        event: dict[str, Any],
        org_id: str | None = None,
        trace_id: str | None = None,
    ) -> bool:
        if org_id:
            event_org = str(event.get("org_id", "")).strip()
            if event_org and event_org != org_id:
                return False

        if trace_id:
            event_trace = str(
                event.get("trace_id") or event.get("trace_hash") or ""
            ).strip()
            if event_trace and event_trace != trace_id:
                return False
            if not event_trace:
                return False

        return True


realtime_hub = RealtimeHub()


def _get_plain_english_summary(event: dict[str, Any]) -> str:
    """Generate a one-sentence summary for non-technical viewers."""
    node = str(event.get("node_name") or "This check")
    score = float(event.get("score") or 0.0)
    flagged = bool(event.get("flagged", False))

    if not flagged:
        confidence = max(0.0, min(1.0, 1.0 - score))
        return (
            f"{node} checked this transaction and found nothing concerning. "
            f"(confidence: {confidence * 100:.0f}%)"
        )

    return (
        f"{node} flagged a potential issue with this transaction. "
        f"Risk: {score * 100:.0f}%. Review recommended."
    )


def publish_event(event: dict[str, Any]) -> None:
    try:
        payload = dict(event)
        if payload.get("type") in {"dag_node", "dag_node_complete"}:
            payload["reasoning"] = _human_reason(str(payload.get("reason") or ""))
            payload["plain_english"] = _get_plain_english_summary(payload)
        realtime_hub.publish(payload)
    except Exception as exc:
        logger.debug("realtime publish failed: %s", exc)
