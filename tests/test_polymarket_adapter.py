"""Offline tests for PolymarketAdapter.

All tests stub aiohttp so NO real network is used.
Mock strategy: patch aiohttp.ClientSession.ws_connect to return an
async-context-manager that yields a fake WS object which async-iterates
a scripted list of fake messages.

All test functions are sync (use asyncio.run) so asyncio_mode=auto does not
interfere. Only the proof test in test_asyncio_mode_proof.py uses @pytest.mark.asyncio.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from pmfi.adapters.polymarket import PolymarketAdapter
from pmfi.domain import RawEvent

# ---------------------------------------------------------------------------
# Helpers: fake WS messages and WS object
# ---------------------------------------------------------------------------

def _text_msg(data: dict) -> MagicMock:
    """Create a fake aiohttp WS TEXT message."""
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.TEXT
    msg.data = json.dumps(data)
    return msg


def _closed_msg() -> MagicMock:
    msg = MagicMock()
    msg.type = aiohttp.WSMsgType.CLOSED
    msg.data = None
    return msg


class _FakeWS:
    """Fake WebSocket exposing the aiohttp receive() contract used by events().

    events() now consumes messages via ``await asyncio.wait_for(ws.receive(), ...)``
    (so an idle connection can time out). receive() returns scripted messages in
    order; once exhausted (or on a CLOSED message) it sets adapter._running = False
    so the outer reconnect loop terminates deterministically. __aiter__ is retained
    for any iteration-based consumer.
    """

    def __init__(self, messages: list, adapter_ref: list):
        """adapter_ref is a one-element list so we can late-bind the adapter."""
        self._messages = list(messages)
        self._adapter_ref = adapter_ref
        self.sent: list[str] = []
        self._idx = 0

    async def send_str(self, text: str) -> None:
        self.sent.append(text)

    def _stop_adapter(self) -> None:
        if self._adapter_ref:
            self._adapter_ref[0]._running = False

    async def receive(self):
        if self._idx >= len(self._messages):
            # No more scripted messages: stop the adapter and signal CLOSED.
            self._stop_adapter()
            return _closed_msg()
        msg = self._messages[self._idx]
        self._idx += 1
        if msg.type == aiohttp.WSMsgType.CLOSED:
            self._stop_adapter()
        return msg

    def __aiter__(self) -> AsyncIterator:
        return self._iter()

    async def _iter(self):
        for msg in self._messages:
            if msg.type == aiohttp.WSMsgType.CLOSED:
                self._stop_adapter()
            yield msg


def _make_ws_connect(fake_ws: _FakeWS):
    """Return a patched ws_connect that acts as an async context manager."""
    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield fake_ws

    return _ctx


def _setup(
    messages: list,
    *,
    asset_ids: list[str] | None = None,
    initial_backoff: float = 0.0,
    max_backoff: float = 0.0,
    reconnect_jitter: bool = False,
) -> tuple[PolymarketAdapter, _FakeWS]:
    """Create an adapter and a FakeWS that auto-stops the adapter on CLOSED."""
    adapter_ref: list = []
    fake_ws = _FakeWS(messages, adapter_ref)
    adapter = PolymarketAdapter(
        ws_url="wss://fake",
        asset_ids=asset_ids or [],
        initial_backoff=initial_backoff,
        max_backoff=max_backoff,
        reconnect_jitter=reconnect_jitter,
    )
    adapter_ref.append(adapter)
    return adapter, fake_ws


async def _run_adapter(adapter: PolymarketAdapter, fake_ws: _FakeWS) -> list[RawEvent]:
    """Connect adapter, run events() until _running stops, return results."""
    await adapter.connect()
    results: list[RawEvent] = []
    with patch.object(aiohttp.ClientSession, "ws_connect", new=_make_ws_connect(fake_ws)):
        async for ev in adapter.events():
            results.append(ev)
    return results


# ---------------------------------------------------------------------------
# Part A-1: source_event_id from 'id' field
# ---------------------------------------------------------------------------

def test_source_event_id_from_id_field():
    """TEXT message with 'id' -> RawEvent.source_event_id == str(id)."""
    payload = {"id": "evt-001", "market": "condition_abc", "event_type": "trade",
               "price": "0.5", "size": "10", "side": "BUY"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].source_event_id == "evt-001"


# ---------------------------------------------------------------------------
# Part A-2: source_event_id from 'trade_id' fallback
# ---------------------------------------------------------------------------

def test_source_event_id_from_trade_id_fallback():
    """TEXT message with only 'trade_id' (no 'id') -> uses trade_id."""
    payload = {"trade_id": "tid-999", "market": "condition_abc", "event_type": "trade",
               "price": "0.5", "size": "10", "side": "BUY"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].source_event_id == "tid-999"


# ---------------------------------------------------------------------------
# Part A-3: source_event_id is None when neither id nor trade_id present
# ---------------------------------------------------------------------------

def test_source_event_id_none_when_neither():
    """TEXT message with neither 'id' nor 'trade_id' -> source_event_id is None."""
    payload = {"market": "condition_abc", "event_type": "trade",
               "price": "0.5", "size": "10", "side": "BUY"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].source_event_id is None


# ---------------------------------------------------------------------------
# Part A-4: venue_market_id from 'market' field
# ---------------------------------------------------------------------------

def test_venue_market_id_from_market_field():
    """venue_market_id is taken from the 'market' field in the payload."""
    payload = {"id": "e1", "market": "condition_xyz", "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].venue_market_id == "condition_xyz"


def test_venue_market_id_none_when_no_market_field():
    """venue_market_id is None when the payload has no 'market' field."""
    payload = {"id": "e2", "event_type": "trade", "asset_id": "token_abc"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].venue_market_id is None


# ---------------------------------------------------------------------------
# Part A-5: exchange_ts parsed from timestamp/ts/t
# ---------------------------------------------------------------------------

def test_exchange_ts_from_timestamp_key():
    """exchange_ts is parsed from 'timestamp' (seconds epoch)."""
    from datetime import timedelta
    recent_s = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    payload = {"id": "e3", "timestamp": recent_s, "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    ts = results[0].exchange_ts
    assert ts is not None
    assert ts.tzinfo == timezone.utc


def test_exchange_ts_from_ts_key():
    """exchange_ts is parsed from 'ts' (milliseconds epoch)."""
    from datetime import timedelta
    recent_ms = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
    payload = {"id": "e4", "ts": recent_ms, "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert results[0].exchange_ts is not None


def test_exchange_ts_from_t_key():
    """exchange_ts is parsed from 't' key."""
    from datetime import timedelta
    recent_s = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    payload = {"id": "e5", "t": recent_s, "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert results[0].exchange_ts is not None


def test_exchange_ts_none_when_no_ts_field():
    """exchange_ts is None when no timestamp field is present."""
    payload = {"id": "e6", "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert results[0].exchange_ts is None


# ---------------------------------------------------------------------------
# Part A-6: CLOSED message breaks the inner loop; adapter stops
# ---------------------------------------------------------------------------

def test_closed_message_stops_adapter():
    """After a CLOSED message, _FakeWS sets _running=False so events() exits cleanly."""
    payload = {"id": "e7", "market": "cond_1", "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 1
    assert results[0].source_event_id == "e7"
    assert adapter._running is False


# ---------------------------------------------------------------------------
# Part A-7: list payload — multiple events in one TEXT message
# ---------------------------------------------------------------------------

def test_list_payload_yields_multiple_events():
    """A TEXT message containing a JSON array yields one RawEvent per element."""
    msgs_data = [
        {"id": "a1", "market": "cond_a", "event_type": "trade"},
        {"id": "a2", "market": "cond_a", "event_type": "trade"},
    ]
    list_msg = MagicMock()
    list_msg.type = aiohttp.WSMsgType.TEXT
    list_msg.data = json.dumps(msgs_data)

    adapter, fake_ws = _setup([list_msg, _closed_msg()])

    results = asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(results) == 2
    assert results[0].source_event_id == "a1"
    assert results[1].source_event_id == "a2"


# ---------------------------------------------------------------------------
# Part A-8: backoff grows on ws_connect exception; reconnect retries
# ---------------------------------------------------------------------------

def test_reconnect_retries_on_ws_error():
    """On a ws_connect exception, the adapter retries and eventually succeeds.

    ws_connect raises on first call, succeeds on second.
    """
    payload = {"id": "retry-ok", "market": "cond_retry", "event_type": "trade"}
    adapter_ref: list = []
    adapter = PolymarketAdapter(
        ws_url="wss://fake",
        initial_backoff=0.001,
        max_backoff=1.0,
        reconnect_jitter=False,
    )
    adapter_ref.append(adapter)
    success_ws = _FakeWS([_text_msg(payload), _closed_msg()], adapter_ref)

    call_count = 0

    @asynccontextmanager
    async def _flaky_ctx(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise aiohttp.ClientConnectionError("simulated ws error")
        yield success_ws

    async def _run():
        await adapter.connect()
        results: list[RawEvent] = []
        with patch.object(aiohttp.ClientSession, "ws_connect", new=_flaky_ctx):
            with patch("pmfi.adapters.polymarket.asyncio.sleep", new=AsyncMock()):
                async for ev in adapter.events():
                    results.append(ev)
        return results

    results = asyncio.run(_run())
    assert call_count == 2, f"ws_connect should have been called twice, got {call_count}"
    assert len(results) == 1
    assert results[0].source_event_id == "retry-ok"


def test_backoff_doubles_on_repeated_errors():
    """Backoff doubles each retry cycle (no jitter): initial -> *2 -> *2..."""
    sleep_calls: list[float] = []

    @asynccontextmanager
    async def _always_fail(*args, **kwargs):
        raise aiohttp.ClientConnectionError("always fails")
        yield  # make this a valid async generator

    async def _run():
        adapter = PolymarketAdapter(
            ws_url="wss://fake",
            initial_backoff=1.0,
            max_backoff=8.0,
            reconnect_jitter=False,
        )
        await adapter.connect()

        async def _stopping_sleep(t: float) -> None:
            sleep_calls.append(t)
            if len(sleep_calls) >= 2:
                adapter._running = False

        with patch.object(aiohttp.ClientSession, "ws_connect", new=_always_fail):
            with patch("pmfi.adapters.polymarket.asyncio.sleep", side_effect=_stopping_sleep):
                async for _ in adapter.events():
                    pass
        return sleep_calls

    sleeps = asyncio.run(_run())
    assert len(sleeps) >= 2, f"Expected at least 2 sleep calls, got {sleeps}"
    assert sleeps[0] == 1.0, f"first sleep should be 1.0 (initial_backoff), got {sleeps[0]}"
    assert sleeps[1] == 2.0, f"second sleep should be 2.0 (doubled), got {sleeps[1]}"


# ---------------------------------------------------------------------------
# Part A-9: disconnect() closes session and is safe when not connected
# ---------------------------------------------------------------------------

def test_disconnect_closes_session():
    """disconnect() must close the session and set _session to None."""
    async def _run():
        adapter = PolymarketAdapter(ws_url="wss://fake")
        await adapter.connect()
        assert adapter._session is not None
        assert adapter._running is True
        await adapter.disconnect()
        assert adapter._session is None
        assert adapter._running is False

    asyncio.run(_run())


def test_disconnect_safe_when_not_connected():
    """disconnect() must be safe to call when adapter was never connected."""
    async def _run():
        adapter = PolymarketAdapter(ws_url="wss://fake")
        await adapter.disconnect()  # must not raise
        assert adapter._session is None

    asyncio.run(_run())


def test_disconnect_idempotent():
    """Calling disconnect() twice must not raise."""
    async def _run():
        adapter = PolymarketAdapter(ws_url="wss://fake")
        await adapter.connect()
        await adapter.disconnect()
        await adapter.disconnect()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Part A-10: context manager (__aenter__ / __aexit__)
# ---------------------------------------------------------------------------

def test_context_manager_connects_and_disconnects():
    """Using PolymarketAdapter as an async context manager connects/disconnects."""
    async def _run():
        adapter_ref: list = []
        payload = {"id": "ctx-1", "market": "cond_ctx", "event_type": "trade"}
        # We need adapter_ref before creating adapter for _FakeWS; use late binding
        fake_ws_holder: list = []
        adapter = PolymarketAdapter(ws_url="wss://fake", reconnect_jitter=False,
                                    initial_backoff=0.0, max_backoff=0.0)
        adapter_ref.append(adapter)
        fake_ws = _FakeWS([_text_msg(payload), _closed_msg()], adapter_ref)
        fake_ws_holder.append(fake_ws)

        async with adapter:
            assert adapter._running is True
            assert adapter._session is not None
            with patch.object(aiohttp.ClientSession, "ws_connect",
                               new=_make_ws_connect(fake_ws)):
                results = []
                async for ev in adapter.events():
                    results.append(ev)
        assert adapter._session is None
        return results

    results = asyncio.run(_run())
    assert len(results) == 1
    assert results[0].source_event_id == "ctx-1"


# ---------------------------------------------------------------------------
# Part A-11: events() returns immediately when no session
# ---------------------------------------------------------------------------

def test_events_returns_immediately_without_connect():
    """events() is a no-op (yields nothing) when connect() was never called."""
    async def _run():
        adapter = PolymarketAdapter(ws_url="wss://fake")
        results = []
        async for ev in adapter.events():
            results.append(ev)
        return results

    results = asyncio.run(_run())
    assert results == []


# ---------------------------------------------------------------------------
# Part A-12: asset_ids subscription message is sent on connect
# ---------------------------------------------------------------------------

def test_subscription_message_sent_when_asset_ids_provided():
    """When asset_ids are given, a subscription JSON is sent after WS connect."""
    payload = {"id": "sub-1", "market": "cond_sub", "event_type": "trade"}
    adapter, fake_ws = _setup(
        [_text_msg(payload), _closed_msg()],
        asset_ids=["token_abc", "token_def"],
    )

    asyncio.run(_run_adapter(adapter, fake_ws))

    assert len(fake_ws.sent) == 1
    sent = json.loads(fake_ws.sent[0])
    assert sent["type"] == "market"
    assert "token_abc" in sent["assets_ids"]
    assert "token_def" in sent["assets_ids"]


def test_no_subscription_message_when_no_asset_ids():
    """When no asset_ids given, no subscription message is sent."""
    payload = {"id": "nosub-1", "market": "cond_nosub", "event_type": "trade"}
    adapter, fake_ws = _setup([_text_msg(payload), _closed_msg()])

    asyncio.run(_run_adapter(adapter, fake_ws))

    assert fake_ws.sent == []
