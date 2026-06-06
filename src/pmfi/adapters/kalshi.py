from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent

logger = logging.getLogger(__name__)


def _parse_exchange_ts(payload: dict) -> datetime | None:
    """Extract exchange timestamp from a Kalshi trade payload.

    Tries 'created_time' (ISO string), 'ts', and 'timestamp' in order.
    """
    for key in ("created_time", "ts", "timestamp"):
        val = payload.get(key)
        if val is None:
            continue
        try:
            if isinstance(val, str):
                text = val.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            if isinstance(val, (int, float)):
                seconds = val / 1000 if val > 1e12 else val
                return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except Exception:
            continue
    return None

WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
REST_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class KalshiAdapter:
    """Opt-in live WebSocket adapter for Kalshi trades.

    Only used when features.enable_kalshi_live=True.
    API key auth is optional (public trade data may be available unauthed).
    """
    venue_code = "kalshi"

    def __init__(
        self,
        *,
        tickers: list[str] | None = None,
        api_key_id: str | None = None,
        base_url: str = REST_BASE,
        ws_url: str = WS_URL,
        timeout_seconds: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ):
        self._tickers = tickers or []
        self._api_key_id = api_key_id
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._seq = 1

    async def connect(self) -> None:
        headers = {}
        if self._api_key_id:
            headers["KALSHI-ACCESS-KEY"] = self._api_key_id
        self._session = aiohttp.ClientSession(headers=headers)
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
                    backoff = self._initial_backoff
                    logger.info("Kalshi WS connected (attempt %d)", attempt)
                    if self._tickers:
                        sub = {"id": self._seq, "cmd": "subscribe", "params": {"channels": ["trade"], "market_tickers": self._tickers}}
                        self._seq += 1
                        await ws.send_str(json.dumps(sub))
                    async for msg in ws:
                        if not self._running:
                            return
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            if data.get("type") == "trade":
                                payload = data.get("msg", data)
                                ticker = payload.get("ticker", payload.get("market_ticker", "unknown"))
                                yield RawEvent(
                                    venue_code="kalshi",
                                    source_channel="ws_trade",
                                    source_event_type="trade",
                                    source_event_id=str(payload.get("trade_id")) if payload.get("trade_id") else None,
                                    venue_market_id=ticker,
                                    exchange_ts=_parse_exchange_ts(payload),
                                    payload=payload,
                                )
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning("Kalshi WS closed/error: %s", msg.type)
                            break
            except Exception as exc:
                logger.error("Kalshi WS error: %s", exc)
            if not self._running:
                return
            logger.info("Kalshi WS reconnecting in %.1fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def __aenter__(self) -> "KalshiAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
