from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import AlertDecision, NormalizedTrade, RawEvent
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


def _raw_event() -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="event-1",
        venue_market_id="market-1",
        exchange_ts=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 6, 17, 12, 0, 1, tzinfo=timezone.utc),
        payload={"price": "0.65", "size": "50000", "side": "buy", "outcome": "yes"},
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


def test_process_event_adds_lineage_to_persisted_and_delivered_alert():
    raw = _raw_event()
    trade = _trade()
    decision = _decision()
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [decision]
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(123, False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-db-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="alert-1")) as insert_alert,
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    persisted_decision = insert_alert.call_args.args[1]
    delivered_decision = handler.await_args.args[0]
    lineage = persisted_decision.evidence["lineage"]

    assert persisted_decision.evidence["capital_at_risk_usd"] == "32500.00"
    assert delivered_decision.evidence == persisted_decision.evidence
    assert lineage["raw_event_id"] == "123"
    assert lineage["trade_id"] == "trade-db-1"
    assert lineage["market_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert lineage["venue_trade_id"] == "trade-venue-1"
    assert lineage["source_event_id"] == "event-1"
    assert lineage["source_channel"] == "ws_clob"
    assert lineage["source_event_type"] == "trade"
    assert lineage["normalization_version"] == "trade.v1"
    assert lineage["raw_event_received_at"] == "2026-06-17T12:00:01+00:00"
    assert lineage["trade_received_at"] == "2026-06-17T12:00:01+00:00"
    assert lineage["exchange_ts"] == "2026-06-17T12:00:00+00:00"


def test_process_event_duplicate_raw_event_skips_derived_writes():
    raw = _raw_event()
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(123, True))),
        patch("pmfi.pipeline.runner.normalize_event") as normalize_event,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    normalize_event.assert_not_called()
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric_window.assert_not_awaited()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()
