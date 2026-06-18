from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import AlertDecision, RawEvent
from pmfi.pipeline import runner


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


def _raw_event(source_event_id: str, *, trade_id: str, market: str = "restart-market") -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=source_event_id,
        venue_market_id=market,
        exchange_ts=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 6, 17, 12, 0, 1, tzinfo=timezone.utc),
        payload={
            "market": market,
            "trade_id": trade_id,
            "price": "0.65",
            "size": "50000",
            "side": "buy",
            "outcome": "yes",
        },
    )


async def _events(*events: RawEvent):
    for event in events:
        yield event


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


def test_adapter_pipeline_resume_skips_duplicate_raw_event_and_processes_new_event():
    """A restarted adapter may replay the last feed event; raw dedupe must stop it."""
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [_decision()]
    handler = AsyncMock()
    seen_raw_events: dict[tuple[str, str, str | None], int] = {}

    async def fake_insert_raw_event(_conn, raw: RawEvent):
        key = (raw.venue_code, raw.source_channel, raw.source_event_id)
        if key in seen_raw_events:
            return seen_raw_events[key], True
        raw_event_id = 100 + len(seen_raw_events)
        seen_raw_events[key] = raw_event_id
        return raw_event_id, False

    first = _raw_event("restart-1", trade_id="trade-1")
    second = _raw_event("restart-2", trade_id="trade-2", market="restart-market-2")

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(side_effect=fake_insert_raw_event)) as insert_raw,
        patch("pmfi.pipeline.runner.normalize_event", wraps=runner.normalize_event) as normalize_event,
        patch(
            "pmfi.pipeline.runner.upsert_market",
            new=AsyncMock(
                side_effect=[
                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                ]
            ),
        ),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=["trade-db-1", "trade-db-2"])) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(side_effect=["alert-1", "alert-2"])) as insert_alert,
    ):
        first_run_consumed = asyncio.run(
            runner.run_adapter_pipeline(
                _events(first, second),
                pool,
                engine,
                handler,
                max_events=1,
                suppression_window_seconds=300,
            )
        )
        second_run_consumed = asyncio.run(
            runner.run_adapter_pipeline(
                _events(first, second),
                pool,
                engine,
                handler,
                suppression_window_seconds=300,
            )
        )

    normalized_source_ids = [
        call.args[0].source_event_id
        for call in normalize_event.call_args_list
    ]

    assert first_run_consumed == 1
    assert second_run_consumed == 2
    assert insert_raw.await_count == 3
    assert normalized_source_ids == ["restart-1", "restart-2"]
    assert engine.evaluate.call_count == 2
    assert insert_trade.await_count == 2
    assert upsert_metric.await_count == 2
    assert insert_alert.await_count == 2
    assert handler.await_count == 2


def test_process_event_duplicate_trade_skips_metrics_alerts_and_delivery():
    """A reconnect may resend the same venue_trade_id in a new raw event shape."""
    raw = _raw_event("restart-duplicate-trade", trade_id="duplicate-trade")
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(200, False))),
        patch("pmfi.pipeline.runner.normalize_event", wraps=runner.normalize_event) as normalize_event,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value=None)) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        asyncio.run(runner.process_event(raw, pool, engine, handler, suppression=None))

    assert normalize_event.call_count == 1
    upsert_market.assert_awaited_once()
    insert_trade.assert_awaited_once()
    upsert_metric.assert_not_awaited()
    engine.evaluate.assert_not_called()
    insert_alert.assert_not_awaited()
    handler.assert_not_awaited()


def test_adapter_pipeline_passes_seeded_suppression_cache_to_process_event():
    """Daemon restarts should seed in-memory suppression from persisted alerts."""
    raw = _raw_event("restart-suppression-seed", trade_id="seeded-trade")
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    seeded = {
        (
            "polymarket",
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "large_trade_absolute_v1",
        ): datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    }

    stats = runner.PipelineStats()

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value=seeded)) as load_cache,
        patch("pmfi.pipeline.runner.process_event", new=AsyncMock()) as process_event,
    ):
        consumed = asyncio.run(
            runner.run_adapter_pipeline(
                _events(raw),
                pool,
                engine,
                handler,
                max_events=1,
                suppression_window_seconds=300,
                stats=stats,
            )
        )

    assert consumed == 1
    assert stats.raw_events_seen == 1
    load_cache.assert_awaited_once_with(conn, window_seconds=300)
    process_event.assert_awaited_once()
    kwargs = process_event.await_args.kwargs
    assert kwargs["suppression"] is seeded
    assert kwargs["suppression_window_seconds"] == 300


def test_adapter_pipeline_reports_raw_persisted_non_trade_skip_without_trade_or_alert():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="new_market",
        source_event_id="poly-new-market-1",
        venue_market_id="poly-market-1",
        payload={"market": "poly-market-1", "event_type": "new_market"},
    )
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()

    stats = runner.PipelineStats()

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(900, False))) as insert_raw,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()) as upsert_market,
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        consumed = asyncio.run(
            runner.run_adapter_pipeline(
                _events(raw),
                pool,
                engine,
                handler,
                max_events=1,
                suppression_window_seconds=300,
                stats=stats,
            )
        )

    assert consumed == 1
    assert stats.raw_events_seen == 1
    assert stats.raw_events_inserted == 1
    assert stats.non_trade_skips == 1
    assert stats.normalized_trades_inserted == 0
    assert stats.duplicate_trades == 0
    assert stats.dead_letters_inserted == 0
    assert stats.alerts_inserted == 0
    assert stats.alerts_delivered == 0
    insert_raw.assert_awaited_once()
    upsert_market.assert_not_awaited()
    insert_trade.assert_not_awaited()
    upsert_metric.assert_not_awaited()
    insert_alert.assert_not_awaited()
    engine.evaluate.assert_not_called()
    handler.assert_not_awaited()
