from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import RawEvent
from pmfi.normalization import NormalizationError
from pmfi.pipeline.runner import process_event


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_process_event_writes_structured_dead_letter_for_bad_price():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="bad-price-1",
        venue_market_id="market-1",
        payload={"price": "bad", "size": "10", "side": "buy", "outcome": "yes"},
    )
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(321, False))),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=NormalizationError("invalid decimal for price: 'bad'")),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=AsyncMock()) as insert_dead_letter,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    insert_dead_letter.assert_awaited_once()
    kwargs = insert_dead_letter.await_args.kwargs
    assert kwargs["venue_code"] == "polymarket"
    assert kwargs["raw_event_id"] == 321
    assert kwargs["source_channel"] == "ws_clob"
    assert kwargs["failure_stage"] == "normalization"
    assert kwargs["error_class"] == "invalid_price_or_size"
    assert "invalid decimal for price" in kwargs["error_message"]
    assert kwargs["payload"] == raw.payload
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric_window.assert_not_awaited()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()


def test_process_event_writes_dead_letter_for_missing_polymarket_asset_mapping():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="asset-miss-1",
        payload={"asset_id": "missing-token", "price": "0.5", "size": "10"},
    )
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(654, False))),
        patch("pmfi.pipeline.runner.normalize_event") as normalize_event,
        patch("pmfi.pipeline.runner.insert_dead_letter", new=AsyncMock()) as insert_dead_letter,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None, asset_id_map={}))

    normalize_event.assert_not_called()
    insert_dead_letter.assert_awaited_once()
    kwargs = insert_dead_letter.await_args.kwargs
    assert kwargs["raw_event_id"] == 654
    assert kwargs["error_class"] == "missing_asset_mapping"
    assert "missing-token" in kwargs["error_message"]
    assert kwargs["payload"] == raw.payload
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric_window.assert_not_awaited()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()


def test_process_event_writes_dead_letter_for_unsupported_venue():
    raw = RawEvent(
        venue_code="predictit",
        source_channel="fixture",
        source_event_type="trade",
        source_event_id="unsupported-venue-1",
        venue_market_id="market-1",
        payload={"price": "0.50", "size": "10"},
    )
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(987, False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=AsyncMock()) as insert_dead_letter,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        outcome = asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    assert outcome.dead_letter_inserted is True
    assert outcome.non_trade_skipped is False
    insert_dead_letter.assert_awaited_once()
    kwargs = insert_dead_letter.await_args.kwargs
    assert kwargs["venue_code"] == "predictit"
    assert kwargs["raw_event_id"] == 987
    assert kwargs["source_channel"] == "fixture"
    assert kwargs["failure_stage"] == "normalization"
    assert kwargs["error_class"] == "unsupported_venue"
    assert kwargs["error_message"] == "unsupported venue: predictit"
    assert kwargs["payload"] == raw.payload
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric_window.assert_not_awaited()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()


def test_process_event_mapped_malformed_payload_preserves_external_raw_payload():
    original_payload = {"asset_id": "tok", "price": "bad", "size": "10"}
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="mapped-bad-1",
        payload=dict(original_payload),
    )
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    insert_raw_event = AsyncMock(return_value=(777, False))

    def fail_after_mapping(mapped_raw):
        assert mapped_raw.payload["asset_id"] == "tok"
        assert mapped_raw.payload["outcome"] == "yes"
        assert mapped_raw.payload["market"] == "condition-1"
        assert mapped_raw.venue_market_id == "condition-1"
        raise NormalizationError("invalid decimal for price: 'bad'")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=insert_raw_event),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=fail_after_mapping),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=AsyncMock()) as insert_dead_letter,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        asyncio.run(
            process_event(
                raw,
                pool,
                engine,
                handler,
                suppression=None,
                asset_id_map={"tok": {"outcome_key": "yes", "venue_market_id": "condition-1"}},
            )
        )

    inserted_raw = insert_raw_event.await_args.args[1]
    assert inserted_raw.payload == original_payload
    assert inserted_raw.venue_market_id is None
    insert_dead_letter.assert_awaited_once()
    assert insert_dead_letter.await_args.kwargs["payload"] == original_payload
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric_window.assert_not_awaited()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()
