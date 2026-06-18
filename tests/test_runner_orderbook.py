from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import AlertDecision, NormalizedTrade, RawEvent
from pmfi.pipeline.runner import process_event


class _TrackingAcquire:
    def __init__(self, pool: _TrackingPool):
        self.pool = pool

    async def __aenter__(self):
        self.pool.active_connections += 1
        return self.pool.conn

    async def __aexit__(self, exc_type, exc, tb):
        self.pool.active_connections -= 1
        return False


class _TrackingPool:
    def __init__(self, conn):
        self.conn = conn
        self.active_connections = 0
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _TrackingAcquire(self)


def _raw_event() -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="orderbook-event-1",
        venue_market_id="market-1",
        exchange_ts=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 6, 17, 12, 0, 1, tzinfo=timezone.utc),
        payload={
            "asset_id": "token-1",
            "market": "market-1",
            "trade_id": "trade-venue-1",
            "price": "0.65",
            "size": "50000",
            "side": "buy",
            "outcome": "yes",
        },
    )


def _trade() -> NormalizedTrade:
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="market-1",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("50000"),
        capital_at_risk_usd=Decimal("32500.00"),
        payout_notional_usd=Decimal("50000"),
        directional_side="yes",
        aggressor_side="buy",
        side_confidence="high",
        venue_trade_id="trade-venue-1",
        exchange_ts=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 6, 17, 12, 0, 1, tzinfo=timezone.utc),
        source_payload={"price": "0.65", "size": "50000"},
    )


def _decision() -> AlertDecision:
    return AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="alert_rules.v1",
        severity="high",
        confidence="high",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"capital_at_risk_usd": "32500.00"},
        data_quality="verified",
    )


def test_process_event_fetches_orderbook_before_reacquiring_for_snapshot_and_alerts():
    raw = _raw_event()
    conn = AsyncMock()
    pool = _TrackingPool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [_decision()]
    handler = AsyncMock()
    raw_book = {
        "bids": [{"price": "0.64", "size": "1000"}],
        "asks": [{"price": "0.66", "size": "500"}],
    }

    async def fetch_without_connection(_token_id: str):
        assert pool.active_connections == 0
        return raw_book

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(123, False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=_trade()),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="market-db-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-db-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.fetch_polymarket_book", new=AsyncMock(side_effect=fetch_without_connection)),
        patch("pmfi.pipeline.runner.insert_orderbook_snapshot", new=AsyncMock()) as insert_snapshot,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="alert-1")) as insert_alert,
    ):
        outcome = asyncio.run(
            process_event(raw, pool, engine, handler, suppression=None, capture_orderbook=True)
        )

    insert_snapshot.assert_awaited_once()
    snapshot_kwargs = insert_snapshot.await_args.kwargs
    assert snapshot_kwargs["raw_event_id"] == 123
    assert snapshot_kwargs["market_id"] == "market-db-1"
    assert snapshot_kwargs["bids"] == [{"price": Decimal("0.64"), "size": Decimal("1000")}]
    assert snapshot_kwargs["asks"] == [{"price": Decimal("0.66"), "size": Decimal("500")}]
    assert snapshot_kwargs["payload"] == raw_book
    assert snapshot_kwargs["best_bid"] == Decimal("0.64")
    assert snapshot_kwargs["best_ask"] == Decimal("0.66")
    assert snapshot_kwargs["spread"] == Decimal("0.02")
    assert snapshot_kwargs["top_depth_usd"] == Decimal("970.00")
    insert_alert.assert_awaited_once()
    handler.assert_awaited_once()
    assert outcome.alerts_inserted == 1
    assert outcome.alerts_delivered == 1
    assert pool.acquire_count == 2


def test_process_event_orderbook_fetch_failure_is_non_fatal_for_alerts():
    raw = _raw_event()
    conn = AsyncMock()
    pool = _TrackingPool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [_decision()]
    handler = AsyncMock()

    async def fail_fetch(_token_id: str):
        raise RuntimeError("book unavailable")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(456, False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=_trade()),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="market-db-2")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-db-2")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.fetch_polymarket_book", new=AsyncMock(side_effect=fail_fetch)),
        patch("pmfi.pipeline.runner.insert_orderbook_snapshot", new=AsyncMock()) as insert_snapshot,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="alert-2")) as insert_alert,
    ):
        outcome = asyncio.run(
            process_event(raw, pool, engine, handler, suppression=None, capture_orderbook=True)
        )

    insert_snapshot.assert_not_awaited()
    insert_alert.assert_awaited_once()
    handler.assert_awaited_once()
    assert outcome.alerts_inserted == 1
    assert outcome.alerts_delivered == 1
