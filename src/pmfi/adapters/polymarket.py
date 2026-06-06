from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent, utc_now

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/"
REST_BASE = "https://clob.polymarket.com"


class PolymarketAdapter:
    """Opt-in live WebSocket adapter for Polymarket CLOB trades.

    Only used when features.enable_polymarket_live=True.
    All events are yielded as raw RawEvent objects before any normalization.
    """
    venue_code = "polymarket"

    def __init__(
        self,
        *,
        market_ids: list[str] | None = None,
        base_url: str = REST_BASE,
        ws_url: str = WS_URL,
        timeout_seconds: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ):
        self._market_ids = market_ids or []
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._session: aiohttp.ClientSession | None = None
        self._queue: asyncio.Queue[RawEvent] = asyncio.Queue(maxsize=1000)
        self._running = False

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._running = True

    async def disconnect(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def events(self) -> AsyncIterator[RawEvent]:
        if not self._session:
            return
        backoff = self._initial_backoff
        attempt = 0
        timeout = aiohttp.ClientTimeout(total=None, connect=self._timeout_seconds)
        while self._running:
            attempt += 1
            try:
                async with self._session.ws_connect(self._ws_url, timeout=timeout, heartbeat=30) as ws:
                    backoff = self._initial_backoff  # reset on successful connect
                    logger.info("Polymarket WS connected (attempt %d)", attempt)
                    if self._market_ids:
                        await ws.send_str(json.dumps({
                            "type": "subscribe",
                            "channel": "trade",
                            "markets": self._market_ids,
                        }))
                    async for msg in ws:
                        if not self._running:
                            return
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            for ev in (data if isinstance(data, list) else [data]):
                                if not isinstance(ev, dict):
                                    continue
                                yield RawEvent(
                                    venue_code="polymarket",
                                    source_channel="ws_clob",
                                    source_event_type=str(ev.get("event_type", "trade")),
                                    source_event_id=str(ev.get("id")) if ev.get("id") else None,
                                    venue_market_id=str(ev.get("market")) if ev.get("market") else None,
                                    exchange_ts=None,
                                    payload=ev,
                                )
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("Polymarket WS closed/error: %s", msg.type)
                            break
            except Exception as exc:
                logger.error("Polymarket WS error: %s", exc)
            if not self._running:
                return
            logger.info("Polymarket WS reconnecting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def __aenter__(self) -> "PolymarketAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
