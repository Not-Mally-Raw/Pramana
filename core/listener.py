"""
core/listener.py — Async WebSocket event listener.
Listens to Anvil (ws://127.0.0.1:8545) or Alchemy WebSocket for new transactions.
Emits ChainEvent objects into the flow queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Coroutine

import websockets
from websockets.exceptions import ConnectionClosed

from core.config.settings import get_settings
from utils.models import ChainEvent

__all__ = ["ChainListener"]

logger = logging.getLogger("pramana.listener")

# Subscription payload for eth_subscribe newPendingTransactions
_SUBSCRIBE_PAYLOAD = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": ["newPendingTransactions"],
    }
)

_FETCH_TX_PAYLOAD_TEMPLATE = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "eth_getTransactionByHash",
    "params": [],
}


class ChainListener:
    """
    Async WebSocket listener. Calls `on_event(ChainEvent)` for each detected transaction.
    Falls back to Anvil if Alchemy is unavailable.
    Reconnects with exponential backoff on disconnect.
    """

    def __init__(
        self,
        on_event: Callable[[ChainEvent], Coroutine],
        org_id: str = "default",
    ) -> None:
        self._on_event = on_event
        self._org_id = org_id
        self._running = False
        self._retry_delay = 1.0

    async def start(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._connect_and_listen()
                self._retry_delay = 1.0  # reset on clean disconnect
            except Exception as e:
                logger.warning(
                    "Listener disconnected: %s. Retry in %ss", e, self._retry_delay
                )
                await asyncio.sleep(self._retry_delay)
                self._retry_delay = min(
                    self._retry_delay * 2, 60.0
                )  # exponential backoff

    def stop(self) -> None:
        self._running = False

    async def _connect_and_listen(self) -> None:
        s = get_settings()
        ws_url = self._ws_url(s)
        logger.info("Connecting to chain WebSocket: %s", ws_url)

        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
            await ws.send(_SUBSCRIBE_PAYLOAD)
            ack = json.loads(await ws.recv())
            sub_id = ack.get("result")
            logger.info("Subscribed to newPendingTransactions (sub_id=%s)", sub_id)

            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw_msg)
                    params = msg.get("params", {})
                    if params.get("subscription") == sub_id:
                        tx_hash = params.get("result")
                        if tx_hash:
                            event = await self._fetch_event(ws, tx_hash)
                            if event:
                                await self._on_event(event)
                except Exception as e:
                    logger.debug("Event parse error: %s", e)

    async def _fetch_event(self, ws, tx_hash: str) -> ChainEvent | None:
        s = get_settings()
        try:
            payload = dict(_FETCH_TX_PAYLOAD_TEMPLATE)
            payload["params"] = [tx_hash]
            await ws.send(json.dumps(payload))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
            tx = resp.get("result")
            if not tx:
                return None
            gas_price = int(tx.get("gasPrice", "0x0"), 16)
            value = int(tx.get("value", "0x0"), 16)
            return ChainEvent(
                agent_id=tx.get("from", "0x") or "0x",
                org_id=self._org_id,
                tx_hash=tx_hash,
                action=tx.get("input", "0x")[:10],  # 4-byte selector
                calldata=tx.get("input", "0x"),
                value_wei=value,
                gas_price=gas_price,
                network=s.NETWORK,
            )
        except Exception:
            return None

    @staticmethod
    def _ws_url(s) -> str:
        if s.NETWORK == "anvil" or not s.ALCHEMY_WS_URL:
            return "ws://127.0.0.1:8545"
        return s.ALCHEMY_WS_URL
