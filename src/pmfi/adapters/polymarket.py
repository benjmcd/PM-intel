from __future__ import annotations
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent, utc_now

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_BASE = "https://clob.polymarket.com"


def _parse_exchange_ts(ev: dict) -> datetime | None:
    """Extract exchange timestamp from a Polymarket event dict.

    Tries 'timestamp', 'ts', and 't' in order. Values may be seconds or
    milliseconds (epoch). Returns a UTC-aware datetime or None.
    """
    for key in ("timestamp", "ts", "t"):
        raw = ev.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        # Heuristic: values > 1e12 are milliseconds, otherwise seconds.
        if val > 1e12:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)
    return None


class PolymarketAdapter:
    """Opt-in live WebSocket adapter for Polymarket CLOB trades.

    Only used when features.enable_polymarket_live=True.
    All events are yielded as raw RawEvent objects before any normalization.
    """
    venue_code = "polymarket"

    def __init__(
        self,
        *,
        asset_ids: list[str] | None = None,
        base_url: str = REST_BASE,
        ws_url: str = WS_URL,
        timeout_seconds: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        reconnect_jitter: bool = True,
    ):
        self._asset_ids = asset_ids or []
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._reconnect_jitter = reconnect_jitter
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
                    if self._asset_ids:
                        await ws.send_str(json.dumps({
                            "assets_ids": self._asset_ids,
                            "type": "market",
                            "custom_feature_enabled": True,
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
                                source_event_id = None
                                _id = ev.get("id") or ev.get("trade_id")
                                if _id is not None:
                                    source_event_id = str(_id)
                                yield RawEvent(
                                    venue_code="polymarket",
                                    source_channel="ws_clob",
                                    source_event_type=str(ev.get("event_type", "")),
                                    source_event_id=source_event_id,
                                    venue_market_id=str(ev.get("market")) if ev.get("market") else None,
                                    exchange_ts=_parse_exchange_ts(ev),
                                    payload=ev,
                                )
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("Polymarket WS closed/error: %s", msg.type)
                            break
            except Exception as exc:
                logger.error("Polymarket WS error: %s", exc)
            if not self._running:
                return
            sleep_time = backoff * (0.5 + random.random() / 2) if self._reconnect_jitter else backoff
            logger.info("Polymarket WS reconnecting in %.1fs", sleep_time)
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, self._max_backoff)

    async def __aenter__(self) -> "PolymarketAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
