from __future__ import annotations
import asyncio
import json
import logging
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent

logger = logging.getLogger(__name__)

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
    ):
        self._tickers = tickers or []
        self._api_key_id = api_key_id
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
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
        timeout = aiohttp.ClientTimeout(total=None, connect=self._timeout_seconds)
        try:
            async with self._session.ws_connect(self._ws_url, timeout=timeout, heartbeat=30) as ws:
                if self._tickers:
                    sub = {"id": self._seq, "cmd": "subscribe", "params": {"channels": ["trade"], "market_tickers": self._tickers}}
                    self._seq += 1
                    await ws.send_str(json.dumps(sub))
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        msg_type = data.get("type", "")
                        if msg_type == "trade":
                            payload = data.get("msg", data)
                            ticker = payload.get("ticker", payload.get("market_ticker", "unknown"))
                            yield RawEvent(
                                venue_code="kalshi",
                                source_channel="ws_trade",
                                source_event_type="trade",
                                source_event_id=str(payload.get("trade_id")) if payload.get("trade_id") else None,
                                venue_market_id=ticker,
                                exchange_ts=None,
                                payload=payload,
                            )
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning("Kalshi WS closed or error: %s", msg.type)
                        break
        except Exception as exc:
            logger.error("Kalshi WS error: %s", exc)

    async def __aenter__(self) -> "KalshiAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
