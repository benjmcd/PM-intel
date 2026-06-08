"""Offline tests for KalshiRestPollingAdapter.

No network calls — fetch_kalshi_trades is stubbed via AsyncMock throughout.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
from pmfi.domain import RawEvent
from pmfi.pipeline.normalize import normalize_event

# ---------------------------------------------------------------------------
# Shared trade fixture matching the real REST shape
# ---------------------------------------------------------------------------

_TRADE_A = {
    "trade_id": "tid-001",
    "ticker": "KS-TEST",
    "yes_price_dollars": "0.9100",
    "no_price_dollars": "0.0900",
    "count_fp": "49.00",
    "taker_side": "yes",
    "created_time": "2026-06-07T10:02:03.545289Z",
    "is_block_trade": False,
}

_TRADE_B = {
    "trade_id": "tid-002",
    "ticker": "KS-TEST",
    "yes_price_dollars": "0.8000",
    "no_price_dollars": "0.2000",
    "count_fp": "10.00",
    "taker_side": "no",
    "created_time": "2026-06-07T10:02:10.000000Z",
    "is_block_trade": False,
}

TICKER = "KS-TEST"


# ---------------------------------------------------------------------------
# Helper: run an async generator, collecting up to max_events items then stop
# ---------------------------------------------------------------------------

async def _collect(adapter: KalshiRestPollingAdapter, max_events: int) -> list[RawEvent]:
    await adapter.connect()
    results: list[RawEvent] = []
    async for ev in adapter.events():
        results.append(ev)
        if len(results) >= max_events:
            adapter._running = False
            break
    return results


# ---------------------------------------------------------------------------
# Test a: events() yields RawEvents with correct metadata
# ---------------------------------------------------------------------------

class TestEventsYieldsRawEvents:
    def test_yields_raw_events_for_trades(self):
        """events() yields one RawEvent per trade with correct metadata."""
        adapter = KalshiRestPollingAdapter(
            tickers=[TICKER],
            poll_interval_seconds=0.01,
            limit=100,
        )

        async def _run():
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                new=AsyncMock(return_value=[_TRADE_A, _TRADE_B]),
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    return await _collect(adapter, max_events=2)

        results = asyncio.run(_run())
        assert len(results) == 2

        first = results[0]
        assert isinstance(first, RawEvent)
        assert first.venue_code == "kalshi"
        assert first.source_channel == "rest_trades"
        assert first.source_event_id == "tid-001"
        assert first.venue_market_id == TICKER

        second = results[1]
        assert second.source_event_id == "tid-002"
        assert second.venue_market_id == TICKER


# ---------------------------------------------------------------------------
# Test b: intra-cycle dedup — same trade returned twice in same cycle is not yielded twice
# ---------------------------------------------------------------------------

class TestIntraCycleDedup:
    def test_duplicate_trade_id_in_same_cycle_suppressed(self):
        """Same trade_id appearing twice in one page is only yielded once."""
        # Two copies of the same trade in the returned list
        duplicate_trades = [_TRADE_A, dict(_TRADE_A)]  # same trade_id

        adapter = KalshiRestPollingAdapter(
            tickers=[TICKER],
            poll_interval_seconds=0.01,
        )

        async def _run():
            await adapter.connect()
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                new=AsyncMock(return_value=duplicate_trades),
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    results: list[RawEvent] = []
                    async for ev in adapter.events():
                        results.append(ev)
                        # After first trade, stop so we can check dedup
                        adapter._running = False
                    return results

        results = asyncio.run(_run())
        trade_ids = [ev.source_event_id for ev in results]
        assert trade_ids.count("tid-001") == 1, (
            f"Expected tid-001 exactly once but got: {trade_ids}"
        )

    def test_cross_cycle_same_trade_suppressed(self):
        """Trade seen in cycle N is not yielded again in cycle N+1 (prev_seen optimization).

        Cycle 1: trade A is new → yielded.
        Cycle 2: trade A is in prev_seen → suppressed (no yield).
        Cycle 3: new trade B appears → yielded (proves the loop is still running).
        """
        call_count = [0]
        trade_b = dict(_TRADE_B)
        trade_a_copy = dict(_TRADE_A)

        async def _side_effect(ticker, *, limit=100, max_pages=None, timeout=None, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                # Cycles 1 and 2 return same trade A
                return [trade_a_copy]
            # Cycle 3 returns trade B (different id) so we can break
            return [trade_b]

        adapter = KalshiRestPollingAdapter(
            tickers=[TICKER],
            poll_interval_seconds=0.01,
        )

        async def _run():
            await adapter.connect()
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                side_effect=_side_effect,
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    results: list[RawEvent] = []
                    async for ev in adapter.events():
                        results.append(ev)
                        # Stop after we see trade B (proves cycle 3 ran)
                        if ev.source_event_id == "tid-002":
                            adapter._running = False
                    return results

        results = asyncio.run(_run())
        trade_ids = [ev.source_event_id for ev in results]
        # Note: the storage layer deduplicates authoritatively across restarts;
        # the seen-set here is only a network/DB-load optimization within a session.
        assert trade_ids.count("tid-001") == 1, (
            f"Cross-cycle dedup failed — tid-001 should appear once: {trade_ids}"
        )
        assert "tid-002" in trade_ids, f"Expected tid-002 in cycle 3 results: {trade_ids}"
        assert call_count[0] >= 3, f"Expected at least 3 fetch calls, got {call_count[0]}"


# ---------------------------------------------------------------------------
# Test c: backoff/resilience — ClientError then success, no crash
# ---------------------------------------------------------------------------

class TestBackoffResilience:
    def test_client_error_then_success_does_not_crash(self):
        """events() recovers from aiohttp.ClientError and still yields on next cycle."""
        call_count = [0]

        async def _side_effect(ticker, *, limit=100, max_pages=None, timeout=None, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise aiohttp.ClientError("simulated network error")
            return [_TRADE_A]

        adapter = KalshiRestPollingAdapter(
            tickers=[TICKER],
            poll_interval_seconds=0.01,
            initial_backoff=0.01,
            max_backoff=0.1,
        )

        async def _run():
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                side_effect=_side_effect,
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    return await _collect(adapter, max_events=1)

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].source_event_id == "tid-001"
        assert call_count[0] >= 2  # first failed, second succeeded

    def test_exception_does_not_propagate(self):
        """Generic Exception in fetch does not crash the events() generator."""
        call_count = [0]

        async def _side_effect(ticker, *, limit=100, max_pages=None, timeout=None, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("unexpected internal error")
            return [_TRADE_B]

        adapter = KalshiRestPollingAdapter(
            tickers=[TICKER],
            poll_interval_seconds=0.01,
            initial_backoff=0.01,
        )

        async def _run():
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                side_effect=_side_effect,
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    return await _collect(adapter, max_events=1)

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0].source_event_id == "tid-002"


# ---------------------------------------------------------------------------
# Test d: adapter -> raw_event -> normalize e2e using the live fixture shape
# ---------------------------------------------------------------------------

class TestAdapterToNormalizeE2E:
    """Full pipeline: stubbed fetch -> RawEvent via adapter -> normalize_event."""

    _FIXTURE_TRADE = {
        "count_fp": "49.00",
        "created_time": "2026-06-07T10:02:03.545289Z",
        "is_block_trade": False,
        "no_price_dollars": "0.0900",
        "taker_book_side": "bid",
        "taker_outcome_side": "yes",
        "taker_side": "yes",
        "ticker": "KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE",
        "trade_id": "f272e06b-c375-72df-d6d4-36b5e0b0b482",
        "yes_price_dollars": "0.9100",
    }
    _TICKER = "KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE"

    def _get_raw_event(self) -> RawEvent:
        adapter = KalshiRestPollingAdapter(
            tickers=[self._TICKER],
            poll_interval_seconds=0.01,
        )

        async def _run():
            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                new=AsyncMock(return_value=[self._FIXTURE_TRADE]),
            ):
                with patch("pmfi.adapters.kalshi_rest.asyncio.sleep", new=AsyncMock()):
                    return await _collect(adapter, max_events=1)

        results = asyncio.run(_run())
        assert len(results) == 1
        return results[0]

    def test_raw_event_metadata(self):
        raw = self._get_raw_event()
        assert raw.venue_code == "kalshi"
        assert raw.source_channel == "rest_trades"
        assert raw.source_event_id == "f272e06b-c375-72df-d6d4-36b5e0b0b482"
        assert raw.venue_market_id == self._TICKER

    def test_normalize_price_0_91(self):
        raw = self._get_raw_event()
        trade = normalize_event(raw)
        assert trade is not None
        assert trade.price == Decimal("0.9100")

    def test_normalize_contracts_49(self):
        raw = self._get_raw_event()
        trade = normalize_event(raw)
        assert trade is not None
        assert trade.contracts == Decimal("49.00")
        assert trade.contracts == 49

    def test_normalize_outcome_yes(self):
        raw = self._get_raw_event()
        trade = normalize_event(raw)
        assert trade is not None
        assert trade.outcome_key == "yes"
