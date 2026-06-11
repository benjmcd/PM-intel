from __future__ import annotations
import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent

logger = logging.getLogger(__name__)

# Error classification helpers
_PERMANENT_HTTP_STATUS = frozenset({401, 403, 404, 405, 410})


def _is_permanent_ws_error(exc: BaseException) -> bool:
    """Return True for errors that should not be retried (auth/protocol failures)."""
    msg = str(exc).lower()
    return any(kw in msg for kw in ("401", "403", "forbidden", "unauthorized", "invalid api key"))


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
        reconnect_jitter: bool = True,
        subscription_timeout_seconds: float = 30.0,
        receive_timeout_seconds: float = 60.0,
    ):
        self._tickers = tickers or []
        self._api_key_id = api_key_id
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._reconnect_jitter = reconnect_jitter
        self._subscription_timeout_seconds = subscription_timeout_seconds
        self._receive_timeout_seconds = receive_timeout_seconds
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

    async def _receive_with_idle_timeout(self, ws: aiohttp.ClientWebSocketResponse):
        """Receive next WS message, raising asyncio.TimeoutError if idle too long."""
        return await asyncio.wait_for(ws.receive(), timeout=self._receive_timeout_seconds)

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
                    sub_sent = False
                    if self._tickers:
                        sub = {"id": self._seq, "cmd": "subscribe", "params": {"channels": ["trade"], "market_tickers": self._tickers}}
                        self._seq += 1
                        await ws.send_str(json.dumps(sub))
                        sub_sent = True

                    # Silent-dead-subscription detection: warn if no message within
                    # subscription_timeout_seconds after sending a subscription.
                    sub_warned = False
                    first_msg_received = not sub_sent  # skip guard if no sub needed
                    sub_deadline = self._subscription_timeout_seconds

                    while self._running:
                        try:
                            msg = await asyncio.wait_for(
                                ws.receive(),
                                timeout=sub_deadline if not first_msg_received else self._receive_timeout_seconds,
                            )
                        except asyncio.TimeoutError:
                            if not first_msg_received and not sub_warned:
                                logger.warning(
                                    "Kalshi WS: no message received within %.0fs after subscription — "
                                    "subscription may have been silently rejected or ignored",
                                    self._subscription_timeout_seconds,
                                )
                                sub_warned = True
                                # Switch to regular idle timeout for remaining receive calls
                                first_msg_received = True
                                continue
                            # Idle timeout — silently-hung connection
                            logger.warning(
                                "Kalshi WS idle timeout (%.0fs without a message) — reconnecting",
                                self._receive_timeout_seconds,
                            )
                            break

                        first_msg_received = True
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except json.JSONDecodeError:
                                continue
                            # Explicit subscription error/nack from server
                            msg_type = data.get("type", "")
                            if msg_type in ("error", "subscribed_error", "subscription_error"):
                                logger.warning(
                                    "Kalshi WS subscription error/nack: type=%r msg=%r",
                                    msg_type, data,
                                )
                                continue
                            if msg_type == "trade":
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
                if _is_permanent_ws_error(exc):
                    logger.error(
                        "Kalshi WS permanent error (will not retry): %s", exc
                    )
                    self._running = False
                    return
                logger.error("Kalshi WS transient error: %s", exc)
            if not self._running:
                return
            sleep_time = backoff * (0.5 + random.random() / 2) if self._reconnect_jitter else backoff
            logger.info("Kalshi WS reconnecting in %.1fs", sleep_time)
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, self._max_backoff)

    async def __aenter__(self) -> "KalshiAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
