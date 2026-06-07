"""Offline test for run_adapter_pipeline failed-event counter (FIX B).

Verifies that:
- run_adapter_pipeline does NOT crash when some events raise during process_event
- the returned int equals the number of SUCCEEDED events only
- failed events are excluded from the count
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmfi.domain import RawEvent


def _make_raw_event(event_id: str) -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=event_id,
        venue_market_id="test-market",
        exchange_ts=None,
        payload={"price": "0.55", "size": "100", "side": "buy", "outcome": "yes"},
    )


async def _async_iter(events):
    for e in events:
        yield e


def _make_pool() -> MagicMock:
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    # fetch returns empty list so suppression seed returns {}
    mock_conn.fetch = AsyncMock(return_value=[])
    return mock_pool


def test_run_adapter_pipeline_counts_only_succeeded_events():
    """3 events: first and third succeed, second raises — processed==2, no crash."""
    from pmfi.pipeline.runner import run_adapter_pipeline

    events = [
        _make_raw_event("ev-ok-1"),
        _make_raw_event("ev-fail-1"),
        _make_raw_event("ev-ok-2"),
    ]

    call_count = 0

    async def fake_process_event(raw, pool, engine, handler, **kwargs):
        nonlocal call_count
        call_count += 1
        if raw.source_event_id == "ev-fail-1":
            raise RuntimeError("simulated processing failure")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=fake_process_event):
        processed = asyncio.run(
            run_adapter_pipeline(
                _async_iter(events),
                pool,
                engine,
                handler,
            )
        )

    assert processed == 2, f"expected 2 succeeded events, got {processed}"
    assert call_count == 3, f"process_event should have been called for all 3 events, got {call_count}"


def test_run_adapter_pipeline_all_fail_returns_zero():
    """All events fail — processed==0, no crash."""
    from pmfi.pipeline.runner import run_adapter_pipeline

    events = [_make_raw_event("ev-fail-1"), _make_raw_event("ev-fail-2")]

    async def always_fail(raw, pool, engine, handler, **kwargs):
        raise ValueError("always fails")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=always_fail):
        processed = asyncio.run(
            run_adapter_pipeline(
                _async_iter(events),
                pool,
                engine,
                handler,
            )
        )

    assert processed == 0


def test_run_adapter_pipeline_all_succeed_returns_full_count():
    """All events succeed — processed equals total event count."""
    from pmfi.pipeline.runner import run_adapter_pipeline

    events = [_make_raw_event(f"ev-ok-{i}") for i in range(4)]

    async def always_succeed(raw, pool, engine, handler, **kwargs):
        pass

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=always_succeed):
        processed = asyncio.run(
            run_adapter_pipeline(
                _async_iter(events),
                pool,
                engine,
                handler,
            )
        )

    assert processed == 4
