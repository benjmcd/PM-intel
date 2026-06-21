from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmfi.domain import NormalizedTrade, RawEvent


def _raw(source_event_id: str = "integrity-raw-1") -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=source_event_id,
        venue_market_id="integrity-market",
        exchange_ts=datetime.now(timezone.utc),
        payload={"price": "0.55", "size": "100", "side": "buy", "outcome": "yes"},
    )


def _trade() -> NormalizedTrade:
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="integrity-market",
        outcome_key="yes",
        price=Decimal("0.55"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("55"),
        payout_notional_usd=Decimal("100"),
        directional_side="yes",
        aggressor_side="buy",
        side_confidence="high",
        venue_trade_id="integrity-trade-1",
        exchange_ts=datetime.now(timezone.utc),
        source_payload={},
    )


class _Acquire:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def acquire(self):
        return _Acquire()


async def _events(*raw_events: RawEvent):
    for raw in raw_events:
        yield raw


async def _noop_handler(*args, **kwargs):
    return None


def test_process_event_dead_letters_post_raw_pre_trade_failure_once_on_dedupe_retry():
    from pmfi.pipeline.runner import process_event

    raw = _raw()
    engine = MagicMock()
    engine.evaluate.return_value = []
    dead_letters: list[dict] = []

    async def _capture_dead_letter(conn, **kwargs):
        dead_letters.append(kwargs)

    with (
        patch(
            "pmfi.pipeline.runner.insert_raw_event",
            new=AsyncMock(side_effect=[("raw-integrity-1", False), ("raw-integrity-1", True)]),
        ),
        patch("pmfi.pipeline.runner.normalize_event", return_value=_trade()),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-integrity-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=RuntimeError("trade insert failed"))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        with pytest.raises(RuntimeError, match="trade insert failed"):
            asyncio.run(process_event(raw, _Pool(), engine, _noop_handler))
        asyncio.run(process_event(raw, _Pool(), engine, _noop_handler))

    assert len(dead_letters) == 1
    assert dead_letters[0]["raw_event_id"] == "raw-integrity-1"
    assert dead_letters[0]["failure_stage"] == "pipeline_write"
    assert dead_letters[0]["error_class"] == "pipeline_write_failed"
    assert "RuntimeError" in dead_letters[0]["error_message"]


def test_db_timeout_inside_process_event_counts_as_connection_failure(monkeypatch):
    from pmfi.pipeline import runner
    from pmfi.pipeline.runner import IngestConnectionLost, run_adapter_pipeline

    raw = _raw("integrity-timeout")
    engine = MagicMock()
    engine.evaluate.return_value = []
    dead_letter = AsyncMock()
    monkeypatch.setattr(runner, "_CONNECTION_FAILURE_THRESHOLD", 1)

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-timeout-1", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=_trade()),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-timeout-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=asyncio.TimeoutError("db timed out"))),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=dead_letter),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        with pytest.raises(IngestConnectionLost, match="DB connection lost"):
            asyncio.run(
                run_adapter_pipeline(
                    _events(raw),
                    _Pool(),
                    engine,
                    _noop_handler,
                    raise_on_connection_loss=True,
                )
            )

    dead_letter.assert_awaited_once()
    assert dead_letter.await_args.kwargs["error_class"] == "pipeline_write_failed"


def test_dead_letter_write_db_down_escalates_as_connection_loss(monkeypatch):
    from pmfi.pipeline import runner
    from pmfi.pipeline.runner import IngestConnectionLost, run_adapter_pipeline

    raw = _raw("integrity-dead-letter-db-down")
    engine = MagicMock()
    engine.evaluate.return_value = []
    monkeypatch.setattr(runner, "_CONNECTION_FAILURE_THRESHOLD", 1)

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-dl-down", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=_trade()),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-dl-down")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=RuntimeError("trade insert failed"))),
        patch(
            "pmfi.pipeline.runner.insert_dead_letter",
            new=AsyncMock(side_effect=asyncio.TimeoutError("dead_letter write timed out")),
        ) as dead_letter,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        with pytest.raises(IngestConnectionLost, match="DB connection lost"):
            asyncio.run(
                run_adapter_pipeline(
                    _events(raw),
                    _Pool(),
                    engine,
                    _noop_handler,
                    raise_on_connection_loss=True,
                )
            )

    dead_letter.assert_awaited_once()


def test_parse_timeout_inside_process_event_remains_data_error(monkeypatch):
    from pmfi.pipeline import runner
    from pmfi.pipeline.runner import run_adapter_pipeline

    raw = _raw("integrity-parse-timeout")
    engine = MagicMock()
    engine.evaluate.return_value = []
    dead_letter = AsyncMock()
    monkeypatch.setattr(runner, "_CONNECTION_FAILURE_THRESHOLD", 1)

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-parse-timeout", False))),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=asyncio.TimeoutError("parser timed out")),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=dead_letter),
    ):
        processed = asyncio.run(
            run_adapter_pipeline(
                _events(raw),
                _Pool(),
                engine,
                _noop_handler,
                raise_on_connection_loss=True,
            )
        )

    assert processed == 0
    dead_letter.assert_awaited_once()
    assert dead_letter.await_args.kwargs["failure_stage"] == "normalization"
    assert dead_letter.await_args.kwargs["error_class"] == "normalizer_exception"
    assert "TimeoutError: parser timed out" in dead_letter.await_args.kwargs["error_message"]
