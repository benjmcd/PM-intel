from __future__ import annotations
import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Protocol

import aiohttp

from pmfi.domain import RawEvent, utc_now

logger = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_BASE = "https://clob.polymarket.com"

# Sanity bounds for live-parsed exchange timestamps.
_TS_FUTURE_LIMIT = timedelta(hours=1)
_TS_PAST_LIMIT = timedelta(days=30)
_ERROR_FRAME_TYPES = {"error", "subscription_error", "failed", "failure"}
_ACK_FRAME_TYPES = {"ack", "subscribed", "subscription", "subscriptions", "subscription_success"}


class PolymarketStreamError(OSError):
    """Raised when the live Polymarket stream reports an explicit venue error."""


class ConnectionRecorder(Protocol):
    async def connected(
        self,
        *,
        venue_code: str,
        source_channel: str,
        reconnect_count: int = 0,
        metadata: dict[str, object] | None = None,
    ) -> object | None:
        ...

    async def message(self, connection_id: object) -> None:
        ...

    async def disconnected(
        self,
        connection_id: object,
        *,
        reason: str,
        classification: str,
    ) -> None:
        ...


def _parse_exchange_ts(ev: dict) -> datetime | None:
    """Extract exchange timestamp from a Polymarket event dict.

    Tries 'timestamp', 'ts', and 't' in order. Values may be seconds or
    milliseconds (epoch). Returns a UTC-aware datetime or None.

    A sanity guard rejects timestamps more than 1h in the future or more than
    30 days in the past (logs a warning and returns None so the caller falls
    back to received_at).
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
        parsed = datetime.fromtimestamp(val, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if parsed > now + _TS_FUTURE_LIMIT:
            logger.warning(
                "_parse_exchange_ts: timestamp key=%r value=%r is >1h in the future "
                "(parsed=%s); returning None",
                key, raw, parsed.isoformat(),
            )
            return None
        if parsed < now - _TS_PAST_LIMIT:
            logger.warning(
                "_parse_exchange_ts: timestamp key=%r value=%r is >30d in the past "
                "(parsed=%s); returning None",
                key, raw, parsed.isoformat(),
            )
            return None
        return parsed
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
        subscription_timeout_seconds: float = 30.0,
        receive_timeout_seconds: float = 60.0,
        connection_recorder: ConnectionRecorder | None = None,
    ):
        self._asset_ids = asset_ids or []
        self._ws_url = ws_url
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._reconnect_jitter = reconnect_jitter
        self._subscription_timeout_seconds = subscription_timeout_seconds
        self._receive_timeout_seconds = receive_timeout_seconds
        self._connection_recorder = connection_recorder
        self._session: aiohttp.ClientSession | None = None
        self._running = False

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._running = True

    async def disconnect(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None

    async def _record_connected(self, *, attempt: int) -> object | None:
        if self._connection_recorder is None:
            return None
        try:
            return await self._connection_recorder.connected(
                venue_code=self.venue_code,
                source_channel="ws_clob",
                reconnect_count=max(0, attempt - 1),
                metadata={
                    "asset_count": len(self._asset_ids),
                    "ws_url": self._ws_url,
                },
            )
        except Exception as exc:
            logger.warning("Polymarket WS connection lifecycle start failed (non-fatal): %s", exc)
            return None

    async def _record_message(self, connection_id: object | None) -> None:
        if self._connection_recorder is None or connection_id is None:
            return
        try:
            await self._connection_recorder.message(connection_id)
        except Exception as exc:
            logger.warning("Polymarket WS connection message checkpoint failed (non-fatal): %s", exc)

    async def _record_disconnected(
        self,
        connection_id: object | None,
        *,
        reason: str,
        classification: str,
    ) -> None:
        if self._connection_recorder is None or connection_id is None:
            return
        try:
            await self._connection_recorder.disconnected(
                connection_id,
                reason=reason,
                classification=classification,
            )
        except Exception as exc:
            logger.warning("Polymarket WS connection lifecycle finish failed (non-fatal): %s", exc)

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
                    connection_id = await self._record_connected(attempt=attempt)
                    disconnect_reason = "adapter stopped"
                    disconnect_classification = "operator_stopped"
                    try:
                        if self._asset_ids:
                            await ws.send_str(json.dumps({
                                "assets_ids": self._asset_ids,
                                "type": "market",
                                "custom_feature_enabled": True,
                            }))
                        first_message = True
                        while self._running:
                            try:
                                msg = await self._receive_with_watchdog(ws, first_message=first_message)
                            except asyncio.TimeoutError as exc:
                                disconnect_reason = f"receive timeout: {exc}"
                                disconnect_classification = "best_effort_gap"
                                raise
                            first_message = False
                            if not self._running:
                                return
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._record_message(connection_id)
                                try:
                                    data = json.loads(msg.data)
                                except json.JSONDecodeError:
                                    logger.warning("Polymarket WS ignored non-JSON text frame")
                                    continue
                                for ev in (data if isinstance(data, list) else [data]):
                                    if not isinstance(ev, dict):
                                        logger.warning("Polymarket WS ignored non-object frame: %r", ev)
                                        continue
                                    self._raise_if_error_frame(ev)
                                    if self._is_ack_frame(ev):
                                        logger.info("Polymarket WS subscription acknowledged: %s", self._frame_summary(ev))
                                        continue
                                    if not self._is_event_frame(ev):
                                        logger.warning(
                                            "Polymarket WS ignored non-event frame keys=%s",
                                            sorted(str(k) for k in ev),
                                        )
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
                                disconnect_reason = f"Polymarket WS closed/error: {msg.type}"
                                disconnect_classification = "best_effort_gap"
                                raise OSError(disconnect_reason)
                    except (asyncio.TimeoutError, OSError) as exc:
                        if disconnect_classification == "operator_stopped":
                            disconnect_reason = f"{type(exc).__name__}: {exc}"
                            disconnect_classification = "best_effort_gap"
                        raise
                    except Exception as exc:
                        disconnect_reason = f"{type(exc).__name__}: {exc}"
                        disconnect_classification = "best_effort_gap"
                        raise
                    finally:
                        await self._record_disconnected(
                            connection_id,
                            reason=disconnect_reason,
                            classification=disconnect_classification,
                        )
            except aiohttp.ClientError as exc:
                logger.error("Polymarket WS error: %s", exc)
            except (asyncio.TimeoutError, OSError):
                raise
            except Exception as exc:
                logger.error("Polymarket WS error: %s", exc)
            if not self._running:
                return
            sleep_time = backoff * (0.5 + random.random() / 2) if self._reconnect_jitter else backoff
            logger.info("Polymarket WS reconnecting in %.1fs", sleep_time)
            await asyncio.sleep(sleep_time)
            backoff = min(backoff * 2, self._max_backoff)

    async def _receive_with_watchdog(self, ws, *, first_message: bool):
        timeout = (
            self._subscription_timeout_seconds
            if first_message and self._asset_ids
            else self._receive_timeout_seconds
        )
        try:
            return await asyncio.wait_for(ws.receive(), timeout=timeout)
        except asyncio.TimeoutError:
            if first_message and self._asset_ids:
                logger.warning(
                    "Polymarket WS subscription timed out after %.1fs without a first message",
                    timeout,
                )
            else:
                logger.warning("Polymarket WS receive timed out after %.1fs", timeout)
            raise

    @staticmethod
    def _lower_frame_value(ev: dict, key: str) -> str:
        raw = ev.get(key)
        if raw is None:
            return ""
        return str(raw).strip().lower()

    @classmethod
    def _frame_summary(cls, ev: dict) -> str:
        values = []
        for key in ("type", "event_type", "status", "message", "error"):
            value = ev.get(key)
            if value is not None:
                values.append(f"{key}={value!r}")
        return ", ".join(values) or f"keys={sorted(str(k) for k in ev)}"

    @classmethod
    def _raise_if_error_frame(cls, ev: dict) -> None:
        frame_type = cls._lower_frame_value(ev, "type")
        event_type = cls._lower_frame_value(ev, "event_type")
        status = cls._lower_frame_value(ev, "status")
        has_error_payload = ev.get("error") not in (None, "", False)
        if (
            has_error_payload
            or frame_type in _ERROR_FRAME_TYPES
            or event_type in _ERROR_FRAME_TYPES
            or status in _ERROR_FRAME_TYPES
        ):
            raise PolymarketStreamError(f"Polymarket WS error frame: {cls._frame_summary(ev)}")

    @classmethod
    def _is_ack_frame(cls, ev: dict) -> bool:
        frame_type = cls._lower_frame_value(ev, "type")
        event_type = cls._lower_frame_value(ev, "event_type")
        status = cls._lower_frame_value(ev, "status")
        if frame_type in _ACK_FRAME_TYPES or event_type in _ACK_FRAME_TYPES:
            return True
        return bool(frame_type and status == "success" and not cls._is_event_frame(ev))

    @staticmethod
    def _is_event_frame(ev: dict) -> bool:
        event_type = ev.get("event_type")
        if event_type and str(event_type).strip().lower() not in _ACK_FRAME_TYPES | _ERROR_FRAME_TYPES:
            return True
        return any(key in ev for key in ("id", "trade_id", "market", "asset_id", "price", "size", "side"))

    async def __aenter__(self) -> "PolymarketAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
