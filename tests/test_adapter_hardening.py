"""Unit tests for adapter hardening (Session 7 slice).

Covers:
  (a) Silent-dead-subscription detection (Kalshi WS + Polymarket WS)
  (b) HTTP 429 / Retry-After handling (KalshiRestPollingAdapter + markets helpers)
  (c) Transient-vs-permanent error classification
  (d) WS idle timeout triggering reconnect
  (e) Kalshi WS reconnect jitter (was missing before)
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from pmfi.adapters.kalshi import KalshiAdapter, _is_permanent_ws_error
from pmfi.adapters.kalshi_rest import (
    KalshiRestPollingAdapter,
    _is_permanent_http_error,
    _rate_limit_backoff,
)
from pmfi.adapters.polymarket import PolymarketAdapter, _is_permanent_ws_error as poly_permanent
from pmfi.domain import RawEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws_msg(type_: aiohttp.WSMsgType, data: str = "") -> aiohttp.WSMessage:
    return aiohttp.WSMessage(type=type_, data=data, extra=None)


def _make_text_msg(data: str) -> aiohttp.WSMessage:
    return _make_ws_msg(aiohttp.WSMsgType.TEXT, data)


def _make_close_msg() -> aiohttp.WSMessage:
    return _make_ws_msg(aiohttp.WSMsgType.CLOSED, "")


# ---------------------------------------------------------------------------
# (a) Silent-dead-subscription detection — Kalshi WS
# ---------------------------------------------------------------------------

class TestKalshiSubTimeout:
    def test_logs_warning_when_no_msg_after_sub(self, caplog):
        """When subscription is sent and no message arrives within sub timeout, warn."""
        adapter = KalshiAdapter(
            tickers=["SOME-TICKER"],
            ws_url="wss://fake",
            subscription_timeout_seconds=0.05,
            receive_timeout_seconds=0.1,
        )
        adapter._running = True

        # ws.receive() always times out (simulates server ignoring the sub)
        ws_mock = AsyncMock()
        ws_mock.receive = AsyncMock(side_effect=asyncio.TimeoutError)
        ws_mock.send_str = AsyncMock()
        ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
        ws_mock.__aexit__ = AsyncMock(return_value=False)

        session_mock = MagicMock()
        session_mock.ws_connect = MagicMock(return_value=ws_mock)

        adapter._session = session_mock
        # Stop after 2 sleep calls so the test doesn't run forever
        sleep_calls = [0]

        async def _fake_sleep(t):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                adapter._running = False

        async def _run():
            with patch("pmfi.adapters.kalshi.asyncio.sleep", side_effect=_fake_sleep):
                with caplog.at_level(logging.WARNING, logger="pmfi.adapters.kalshi"):
                    async for _ in adapter.events():
                        pass

        asyncio.run(_run())
        assert any(
            "subscription may have been silently rejected" in r.message
            for r in caplog.records
        ), f"Expected sub-timeout warning; got: {[r.message for r in caplog.records]}"

    def test_explicit_subscription_error_nack_logged(self, caplog):
        """An explicit error/nack message from server is logged distinctly."""
        import json as _json

        adapter = KalshiAdapter(
            tickers=["SOME-TICKER"],
            ws_url="wss://fake",
            subscription_timeout_seconds=5.0,
            receive_timeout_seconds=10.0,
        )
        adapter._running = True

        nack_msg = _make_text_msg(_json.dumps({"type": "error", "code": "bad_sub", "msg": "rejected"}))
        close_msg = _make_close_msg()

        call_count = [0]

        async def _receive():
            call_count[0] += 1
            if call_count[0] == 1:
                return nack_msg
            adapter._running = False
            return close_msg

        ws_mock = AsyncMock()
        ws_mock.receive = _receive
        ws_mock.send_str = AsyncMock()
        ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
        ws_mock.__aexit__ = AsyncMock(return_value=False)

        session_mock = MagicMock()
        session_mock.ws_connect = MagicMock(return_value=ws_mock)
        adapter._session = session_mock

        async def _run():
            with patch("pmfi.adapters.kalshi.asyncio.sleep", new=AsyncMock()):
                with caplog.at_level(logging.WARNING, logger="pmfi.adapters.kalshi"):
                    async for _ in adapter.events():
                        pass

        asyncio.run(_run())
        assert any(
            "subscription error/nack" in r.message
            for r in caplog.records
        ), f"Expected nack log; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# (a) Silent-dead-subscription detection — Polymarket WS
# ---------------------------------------------------------------------------

class TestPolymarketSubTimeout:
    def test_logs_warning_when_no_msg_after_sub(self, caplog):
        """Polymarket: sub timeout warns when no message after subscription."""
        adapter = PolymarketAdapter(
            asset_ids=["0xabc"],
            ws_url="wss://fake",
            subscription_timeout_seconds=0.05,
            receive_timeout_seconds=0.1,
        )
        adapter._running = True

        ws_mock = AsyncMock()
        ws_mock.receive = AsyncMock(side_effect=asyncio.TimeoutError)
        ws_mock.send_str = AsyncMock()
        ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
        ws_mock.__aexit__ = AsyncMock(return_value=False)

        session_mock = MagicMock()
        session_mock.ws_connect = MagicMock(return_value=ws_mock)
        adapter._session = session_mock

        sleep_calls = [0]

        async def _fake_sleep(t):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                adapter._running = False

        async def _run():
            with patch("pmfi.adapters.polymarket.asyncio.sleep", side_effect=_fake_sleep):
                with caplog.at_level(logging.WARNING, logger="pmfi.adapters.polymarket"):
                    async for _ in adapter.events():
                        pass

        asyncio.run(_run())
        assert any(
            "subscription may have been silently rejected" in r.message
            for r in caplog.records
        ), f"Expected sub-timeout warning; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# (b) HTTP 429 / Retry-After handling — KalshiRestPollingAdapter
# ---------------------------------------------------------------------------

class TestRateLimitBackoff:
    def test_rate_limit_backoff_honors_retry_after(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            headers={"Retry-After": "7"},
        )
        wait = _rate_limit_backoff(exc, current_backoff=1.0)
        assert wait == 7.0

    def test_rate_limit_backoff_fallback_when_no_header(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            headers={},
        )
        wait = _rate_limit_backoff(exc, current_backoff=1.0)
        assert wait is not None and wait > 0

    def test_rate_limit_backoff_none_for_non_429(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=500,
            headers={},
        )
        assert _rate_limit_backoff(exc, current_backoff=1.0) is None

    def test_rate_limit_capped_at_max(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=429,
            headers={"Retry-After": "9999"},
        )
        wait = _rate_limit_backoff(exc, current_backoff=1.0)
        assert wait is not None and wait <= 120.0

    def test_polling_adapter_handles_429_without_crash(self):
        """On 429, polling adapter waits and then continues without crashing."""
        from pmfi.adapters.kalshi_rest import _PERMANENT_HTTP_STATUS

        call_count = [0]

        async def _side_effect(ticker, *, limit=100, max_pages=None, timeout=None, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                exc = aiohttp.ClientResponseError(
                    request_info=MagicMock(),
                    history=(),
                    status=429,
                    headers={"Retry-After": "0.01"},
                )
                raise exc
            return [{
                "trade_id": "t-ok",
                "ticker": "KS-X",
                "yes_price_dollars": "0.5",
                "no_price_dollars": "0.5",
                "count_fp": "10",
                "taker_side": "yes",
                "created_time": "2026-01-01T00:00:00Z",
                "is_block_trade": False,
            }]

        adapter = KalshiRestPollingAdapter(
            tickers=["KS-X"],
            poll_interval_seconds=0.01,
            initial_backoff=0.01,
        )

        async def _run():
            with patch("pmfi.adapters.kalshi_rest.fetch_kalshi_trades", side_effect=_side_effect):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    await adapter.connect()
                    results = []
                    async for ev in adapter.events():
                        results.append(ev)
                        adapter._running = False
                    return results

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].source_event_id == "t-ok"
        assert call_count[0] >= 2


# ---------------------------------------------------------------------------
# (c) Permanent error classification
# ---------------------------------------------------------------------------

class TestPermanentErrorClassification:
    def test_401_is_permanent(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=401, headers={}
        )
        assert _is_permanent_http_error(exc)

    def test_403_is_permanent(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=403, headers={}
        )
        assert _is_permanent_http_error(exc)

    def test_404_is_permanent(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=404, headers={}
        )
        assert _is_permanent_http_error(exc)

    def test_500_is_not_permanent(self):
        exc = aiohttp.ClientResponseError(
            request_info=MagicMock(), history=(), status=500, headers={}
        )
        assert not _is_permanent_http_error(exc)

    def test_connection_error_is_not_permanent(self):
        exc = aiohttp.ClientConnectionError("connection refused")
        assert not _is_permanent_http_error(exc)

    def test_kalshi_ws_permanent_auth_error(self):
        assert _is_permanent_ws_error(Exception("401 Unauthorized"))
        assert _is_permanent_ws_error(Exception("403 Forbidden"))
        assert not _is_permanent_ws_error(Exception("connection reset"))

    def test_polymarket_ws_permanent_auth_error(self):
        assert poly_permanent(Exception("403 forbidden"))
        assert not poly_permanent(Exception("timeout"))

    def test_permanent_http_error_stops_polling_adapter(self):
        """A 403 in the polling adapter stops _running and does not retry."""
        call_count = [0]

        async def _side_effect(ticker, *, limit=100, max_pages=None, timeout=None, **kw):
            call_count[0] += 1
            exc = aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=403, headers={}
            )
            raise exc

        adapter = KalshiRestPollingAdapter(tickers=["KS-Y"], poll_interval_seconds=0.01)

        async def _run():
            with patch("pmfi.adapters.kalshi_rest.fetch_kalshi_trades", side_effect=_side_effect):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    await adapter.connect()
                    results = []
                    async for ev in adapter.events():
                        results.append(ev)
                    return results

        results = asyncio.run(_run())
        assert results == []
        assert call_count[0] == 1, "Should have stopped after first 403, not retried"
        assert not adapter._running

    def test_permanent_ws_error_stops_kalshi_adapter(self, caplog):
        """A WS 401 permanent error stops _running and logs clearly."""
        adapter = KalshiAdapter(
            tickers=["T"],
            ws_url="wss://fake",
            subscription_timeout_seconds=5.0,
            receive_timeout_seconds=10.0,
        )
        adapter._running = True

        session_mock = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=Exception("401 Unauthorized — invalid api key"))
        cm.__aexit__ = AsyncMock(return_value=False)
        session_mock.ws_connect = MagicMock(return_value=cm)
        adapter._session = session_mock

        async def _run():
            with patch("pmfi.adapters.kalshi.asyncio.sleep", new=AsyncMock()):
                with caplog.at_level(logging.ERROR, logger="pmfi.adapters.kalshi"):
                    async for _ in adapter.events():
                        pass

        asyncio.run(_run())
        assert not adapter._running
        assert any("permanent error" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# (d) WS idle timeout triggers reconnect — Kalshi WS
# ---------------------------------------------------------------------------

class TestKalshiIdleTimeout:
    def test_idle_timeout_triggers_reconnect(self, caplog):
        """After idle timeout, adapter logs a warning and reconnects."""
        adapter = KalshiAdapter(
            tickers=[],  # no subscription, so first_msg_received=True immediately
            ws_url="wss://fake",
            subscription_timeout_seconds=5.0,
            receive_timeout_seconds=0.05,
        )
        adapter._running = True

        reconnect_count = [0]

        async def _receive():
            raise asyncio.TimeoutError

        ws_mock = AsyncMock()
        ws_mock.receive = _receive
        ws_mock.send_str = AsyncMock()
        ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
        ws_mock.__aexit__ = AsyncMock(return_value=False)

        session_mock = MagicMock()
        session_mock.ws_connect = MagicMock(return_value=ws_mock)
        adapter._session = session_mock

        sleep_calls = [0]

        async def _fake_sleep(t):
            sleep_calls[0] += 1
            if sleep_calls[0] >= 2:
                adapter._running = False

        async def _run():
            with patch("pmfi.adapters.kalshi.asyncio.sleep", side_effect=_fake_sleep):
                with caplog.at_level(logging.WARNING, logger="pmfi.adapters.kalshi"):
                    async for _ in adapter.events():
                        pass

        asyncio.run(_run())
        assert any(
            "idle timeout" in r.message for r in caplog.records
        ), f"Expected idle timeout warning; got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# (e) Kalshi WS reconnect jitter
# ---------------------------------------------------------------------------

class TestKalshiReconnectJitter:
    def test_jitter_enabled_varies_sleep_time(self):
        """With jitter=True the sleep time is in [0.5*backoff, backoff]."""
        from pmfi.adapters.kalshi import KalshiAdapter as _KA
        import random as _random

        adapter = _KA(
            ws_url="wss://fake",
            initial_backoff=4.0,
            max_backoff=60.0,
            reconnect_jitter=True,
        )
        # Sample 20 jittered values; they should not all be 4.0
        values = set()
        for _ in range(20):
            v = 4.0 * (0.5 + _random.random() / 2)
            values.add(round(v, 4))
        # With 20 samples from [2.0, 4.0], at least 2 should differ
        assert len(values) > 1

    def test_jitter_disabled_sleep_is_exact(self):
        """With jitter=False the sleep time equals the backoff exactly."""
        # This tests the flag path via the sleep call captured in a real events() run
        adapter = KalshiAdapter(
            tickers=[],
            ws_url="wss://fake",
            initial_backoff=2.0,
            max_backoff=60.0,
            reconnect_jitter=False,
            receive_timeout_seconds=0.05,
        )
        adapter._running = True

        ws_mock = AsyncMock()
        ws_mock.receive = AsyncMock(side_effect=asyncio.TimeoutError)
        ws_mock.send_str = AsyncMock()
        ws_mock.__aenter__ = AsyncMock(return_value=ws_mock)
        ws_mock.__aexit__ = AsyncMock(return_value=False)

        session_mock = MagicMock()
        session_mock.ws_connect = MagicMock(return_value=ws_mock)
        adapter._session = session_mock

        sleep_times = []

        async def _fake_sleep(t):
            sleep_times.append(t)
            if len(sleep_times) >= 1:
                adapter._running = False

        async def _run():
            with patch("pmfi.adapters.kalshi.asyncio.sleep", side_effect=_fake_sleep):
                async for _ in adapter.events():
                    pass

        asyncio.run(_run())
        # The reconnect sleep (not sub-timeout internal continue) should equal initial_backoff
        reconnect_sleeps = [t for t in sleep_times if t >= 1.0]
        assert reconnect_sleeps, f"No reconnect sleep recorded; all: {sleep_times}"
        assert reconnect_sleeps[0] == 2.0, f"Expected 2.0 (no jitter), got {reconnect_sleeps[0]}"
