r"""Validate DB-free daemon lifecycle and restart contracts.

This gate exercises runner lifecycle behavior with in-memory fakes only. It
does not require Docker/Postgres, does not make live calls, and does not seed or
write artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


try:
    import asyncpg as _asyncpg  # noqa: F401
except ModuleNotFoundError:
    asyncpg_stub = types.ModuleType("asyncpg")

    class _UniqueViolationError(Exception):
        pass

    asyncpg_stub.Connection = object
    asyncpg_stub.Pool = object
    asyncpg_stub.Record = dict
    asyncpg_stub.UniqueViolationError = _UniqueViolationError
    sys.modules["asyncpg"] = asyncpg_stub

from pmfi.domain import AlertDecision, RawEvent  # noqa: E402
from pmfi.markets import kalshi_trade_to_raw_event  # noqa: E402
from pmfi.pipeline import runner  # noqa: E402


class _Acquire:
    def __init__(self, conn: AsyncMock):
        self.conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self.conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: AsyncMock):
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


class _Clock:
    def __init__(self, values: list[datetime]):
        self._values = list(values)

    def now(self, tz: timezone | None = None) -> datetime:
        _require(bool(self._values), "controlled clock exhausted")
        value = self._values.pop(0)
        return value if tz is None else value.astimezone(tz)


@dataclass(frozen=True)
class _Check:
    name: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": "pass", "details": self.details}


def _fail(message: str) -> NoReturn:
    raise RuntimeError(f"lifecycle-smoke failed: {message}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


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


def _decision(rule_id: str = "large_trade_absolute_v1") -> AlertDecision:
    return AlertDecision(
        emit_alert=True,
        rule_id=rule_id,
        rule_version="alert_rules.v1",
        severity="high",
        confidence="high",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"capital_at_risk_usd": "32500.00"},
        data_quality="verified",
    )


def _check_by_name(payload: dict[str, Any], name: str) -> dict[str, Any]:
    checks = payload.get("checks")
    _require(isinstance(checks, list) and checks, "checks must be a non-empty list")
    for check in checks:
        if isinstance(check, dict) and check.get("name") == name:
            return check
    _fail(f"missing lifecycle check {name}")


def _details(payload: dict[str, Any], name: str) -> dict[str, Any]:
    check = _check_by_name(payload, name)
    _require(check.get("status") == "pass", f"{name} status must be pass")
    details = check.get("details")
    _require(isinstance(details, dict), f"{name} details must be an object")
    return details


def validate_lifecycle_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("proof payload must be a JSON object")
    _require(payload.get("ok") is True, "ok must be true")
    _require(payload.get("status") == "pass", "status must be pass")
    _require(payload.get("source") == "db_free_runner_contracts", "source must be db_free_runner_contracts")

    checks = payload.get("checks")
    _require(isinstance(checks, list) and checks, "checks must be a non-empty list")
    names = [check.get("name") for check in checks if isinstance(check, dict)]
    required = {
        "raw_event_replay_dedupe",
        "duplicate_trade_skip",
        "suppression_cache_seed",
        "suppression_window_expiry",
        "non_trade_raw_persistence",
        "kalshi_rest_poll_overlap_dedupe",
    }
    missing = sorted(required.difference(names))
    _require(not missing, "missing lifecycle check(s): " + ", ".join(missing))

    replay = _details(payload, "raw_event_replay_dedupe")
    _require(replay.get("first_run_consumed") == 1, "raw_event_replay_dedupe first_run_consumed must be 1")
    _require(replay.get("second_run_consumed") == 2, "raw_event_replay_dedupe second_run_consumed must be 2")
    _require(replay.get("raw_insert_attempts") == 3, "raw_event_replay_dedupe raw_insert_attempts must be 3")
    _require(
        replay.get("normalized_source_ids") == ["restart-1", "restart-2"],
        "raw_event_replay_dedupe normalized_source_ids must prove duplicate raw replay was skipped",
    )

    duplicate = _details(payload, "duplicate_trade_skip")
    _require(duplicate.get("normalized_events") == 1, "duplicate_trade_skip normalized_events must be 1")
    _require(duplicate.get("insert_trade_calls") == 1, "duplicate_trade_skip insert_trade_calls must be 1")
    _require(duplicate.get("alerts_inserted") == 0, "duplicate_trade_skip alerts_inserted must be 0")
    _require(duplicate.get("alerts_delivered") == 0, "duplicate_trade_skip alerts_delivered must be 0")

    suppression = _details(payload, "suppression_cache_seed")
    _require(suppression.get("raw_events_seen") == 1, "suppression_cache_seed raw_events_seen must be 1")
    _require(suppression.get("process_event_calls") == 1, "suppression_cache_seed process_event_calls must be 1")
    _require(suppression.get("seeded_entries") == 1, "suppression_cache_seed seeded_entries must be 1")

    expiry = _details(payload, "suppression_window_expiry")
    _require(expiry.get("raw_events_seen") == 3, "suppression_window_expiry raw_events_seen must be 3")
    _require(expiry.get("alerts_inserted") == 2, "suppression_window_expiry alerts_inserted must be 2")
    _require(expiry.get("alerts_delivered") == 2, "suppression_window_expiry alerts_delivered must be 2")
    _require(expiry.get("alerts_suppressed") == 1, "suppression_window_expiry alerts_suppressed must be 1")
    _require(
        expiry.get("suppression_window_seconds") == 300,
        "suppression_window_expiry suppression_window_seconds must be 300",
    )

    non_trade = _details(payload, "non_trade_raw_persistence")
    _require(non_trade.get("raw_events_seen") == 1, "non_trade_raw_persistence raw_events_seen must be 1")
    _require(non_trade.get("raw_events_inserted") == 1, "non_trade_raw_persistence raw_events_inserted must be 1")
    _require(non_trade.get("non_trade_skips") == 1, "non_trade_raw_persistence non_trade_skips must be 1")
    _require(non_trade.get("alerts_inserted") == 0, "non_trade_raw_persistence alerts_inserted must be 0")
    _require(non_trade.get("alerts_delivered") == 0, "non_trade_raw_persistence alerts_delivered must be 0")

    kalshi_overlap = _details(payload, "kalshi_rest_poll_overlap_dedupe")
    _require(kalshi_overlap.get("ticker") == "KXOVERLAP-26JUN", "kalshi_rest_poll_overlap_dedupe ticker mismatch")
    _require(kalshi_overlap.get("poll_windows") == [["t1", "t2"], ["t2", "t3"]], "kalshi_rest_poll_overlap_dedupe poll_windows mismatch")
    _require(kalshi_overlap.get("raw_events_seen") == 4, "kalshi_rest_poll_overlap_dedupe raw_events_seen must be 4")
    _require(kalshi_overlap.get("raw_events_inserted") == 3, "kalshi_rest_poll_overlap_dedupe raw_events_inserted must be 3")
    _require(kalshi_overlap.get("raw_event_duplicates") == 1, "kalshi_rest_poll_overlap_dedupe raw_event_duplicates must be 1")
    _require(kalshi_overlap.get("normalized_trades_inserted") == 3, "kalshi_rest_poll_overlap_dedupe normalized_trades_inserted must be 3")
    _require(kalshi_overlap.get("duplicate_trades") == 0, "kalshi_rest_poll_overlap_dedupe duplicate_trades must be 0")
    _require(kalshi_overlap.get("metrics_upserted") == 3, "kalshi_rest_poll_overlap_dedupe metrics_upserted must be 3")
    _require(kalshi_overlap.get("alerts_inserted") == 3, "kalshi_rest_poll_overlap_dedupe alerts_inserted must be 3")
    _require(kalshi_overlap.get("alerts_delivered") == 3, "kalshi_rest_poll_overlap_dedupe alerts_delivered must be 3")
    _require(kalshi_overlap.get("source_event_ids") == ["t1", "t2", "t3"], "kalshi_rest_poll_overlap_dedupe source_event_ids must be t1,t2,t3")
    _require(kalshi_overlap.get("venue_trade_ids") == ["t1", "t2", "t3"], "kalshi_rest_poll_overlap_dedupe venue_trade_ids must be t1,t2,t3")
    _require(kalshi_overlap.get("source_channels") == ["rest_trades"], "kalshi_rest_poll_overlap_dedupe source_channels must be rest_trades")
    return payload


async def _raw_event_replay_dedupe() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [_decision()]
    handler = AsyncMock()
    seen_raw_events: dict[tuple[str, str, str | None], int] = {}

    async def fake_insert_raw_event(_conn: AsyncMock, raw: RawEvent):
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
            new=AsyncMock(side_effect=["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"]),
        ),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=["trade-db-1", "trade-db-2"])),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(side_effect=["alert-1", "alert-2"])),
    ):
        first_run_consumed = await runner.run_adapter_pipeline(
            _events(first, second),
            pool,
            engine,
            handler,
            max_events=1,
            suppression_window_seconds=300,
        )
        second_run_consumed = await runner.run_adapter_pipeline(
            _events(first, second),
            pool,
            engine,
            handler,
            suppression_window_seconds=300,
        )

    return _Check(
        "raw_event_replay_dedupe",
        {
            "first_run_consumed": first_run_consumed,
            "second_run_consumed": second_run_consumed,
            "raw_insert_attempts": insert_raw.await_count,
            "normalized_source_ids": [call.args[0].source_event_id for call in normalize_event.call_args_list],
        },
    )


async def _duplicate_trade_skip() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    raw = _raw_event("restart-duplicate-trade", trade_id="duplicate-trade")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(200, False))),
        patch("pmfi.pipeline.runner.normalize_event", wraps=runner.normalize_event) as normalize_event,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value=None)) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        await runner.process_event(raw, pool, engine, handler, suppression=None)

    return _Check(
        "duplicate_trade_skip",
        {
            "normalized_events": normalize_event.call_count,
            "insert_trade_calls": insert_trade.await_count,
            "alerts_inserted": insert_alert.await_count,
            "alerts_delivered": handler.await_count,
        },
    )


async def _suppression_cache_seed() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    stats = runner.PipelineStats()
    raw = _raw_event("restart-suppression-seed", trade_id="seeded-trade")
    seeded = {
        ("polymarket", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "large_trade_absolute_v1"): datetime(
            2026, 6, 17, 12, 0, tzinfo=timezone.utc
        )
    }

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value=seeded)),
        patch("pmfi.pipeline.runner.process_event", new=AsyncMock()) as process_event,
    ):
        await runner.run_adapter_pipeline(
            _events(raw),
            pool,
            engine,
            handler,
            max_events=1,
            suppression_window_seconds=300,
            stats=stats,
        )

    return _Check(
        "suppression_cache_seed",
        {
            "raw_events_seen": stats.raw_events_seen,
            "process_event_calls": process_event.await_count,
            "seeded_entries": len(seeded),
        },
    )


async def _suppression_window_expiry() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    engine.evaluate.return_value = [_decision()]
    handler = AsyncMock()
    stats = runner.PipelineStats()
    window_seconds = 300
    events = [
        _raw_event("expiry-1", trade_id="expiry-trade-1", market="expiry-market"),
        _raw_event("expiry-2", trade_id="expiry-trade-2", market="expiry-market"),
        _raw_event("expiry-3", trade_id="expiry-trade-3", market="expiry-market"),
    ]
    clock = _Clock(
        [
            datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 17, 12, 1, tzinfo=timezone.utc),
            datetime(2026, 6, 17, 12, 5, 1, tzinfo=timezone.utc),
        ]
    )

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch(
            "pmfi.pipeline.runner.insert_raw_event",
            new=AsyncMock(side_effect=[(300, False), (301, False), (302, False)]),
        ),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
        patch(
            "pmfi.pipeline.runner.insert_trade",
            new=AsyncMock(side_effect=["trade-db-1", "trade-db-2", "trade-db-3"]),
        ),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(side_effect=["alert-1", "alert-2"])),
        patch("pmfi.pipeline.runner.datetime", new=clock),
    ):
        await runner.run_adapter_pipeline(
            _events(*events),
            pool,
            engine,
            handler,
            suppression_window_seconds=window_seconds,
            stats=stats,
        )

    return _Check(
        "suppression_window_expiry",
        {
            "raw_events_seen": stats.raw_events_seen,
            "alerts_inserted": stats.alerts_inserted,
            "alerts_delivered": stats.alerts_delivered,
            "alerts_suppressed": stats.alerts_suppressed,
            "suppression_window_seconds": window_seconds,
        },
    )


async def _non_trade_raw_persistence() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    stats = runner.PipelineStats()
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="new_market",
        source_event_id="poly-new-market-1",
        venue_market_id="poly-market-1",
        payload={"market": "poly-market-1", "event_type": "new_market"},
    )

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=(900, False))),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock()) as insert_alert,
    ):
        await runner.run_adapter_pipeline(
            _events(raw),
            pool,
            engine,
            handler,
            max_events=1,
            suppression_window_seconds=300,
            stats=stats,
        )

    return _Check(
        "non_trade_raw_persistence",
        {
            "raw_events_seen": stats.raw_events_seen,
            "raw_events_inserted": stats.raw_events_inserted,
            "non_trade_skips": stats.non_trade_skips,
            "alerts_inserted": insert_alert.await_count,
            "alerts_delivered": handler.await_count,
        },
    )


def _kalshi_trade(trade_id: str, *, created_time: str, yes_price: int, count: int) -> dict[str, Any]:
    return {
        "trade_id": trade_id,
        "ticker": "KXOVERLAP-26JUN",
        "yes_price": yes_price,
        "no_price": 100 - yes_price,
        "count": count,
        "taker_side": "yes",
        "created_time": created_time,
    }


async def _kalshi_rest_poll_overlap_dedupe() -> _Check:
    conn = AsyncMock()
    pool = _Pool(conn)
    engine = MagicMock()
    handler = AsyncMock()
    stats = runner.PipelineStats()
    ticker = "KXOVERLAP-26JUN"
    poll_one = [
        _kalshi_trade("t1", created_time="2026-06-17T12:00:00Z", yes_price=51, count=10),
        _kalshi_trade("t2", created_time="2026-06-17T12:00:01Z", yes_price=52, count=20),
    ]
    poll_two = [
        dict(poll_one[1]),
        _kalshi_trade("t3", created_time="2026-06-17T12:00:02Z", yes_price=53, count=30),
    ]
    raw_events = [
        kalshi_trade_to_raw_event(trade, ticker)
        for trade in [*poll_one, *poll_two]
    ]
    seen_raw_events: dict[tuple[str, str, str | None], int] = {}

    async def fake_insert_raw_event(_conn: AsyncMock, raw: RawEvent):
        key = (raw.venue_code, raw.source_channel, raw.source_event_id)
        if key in seen_raw_events:
            return seen_raw_events[key], True
        raw_event_id = 500 + len(seen_raw_events)
        seen_raw_events[key] = raw_event_id
        return raw_event_id, False

    def fake_evaluate(trade: Any) -> list[AlertDecision]:
        return [_decision(rule_id=f"kalshi_rest_overlap_{trade.venue_trade_id}")]

    engine.evaluate.side_effect = fake_evaluate

    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(side_effect=fake_insert_raw_event)),
        patch("pmfi.pipeline.runner.normalize_event", wraps=runner.normalize_event) as normalize_event,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="kalshi-overlap-market")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(side_effect=["trade-db-1", "trade-db-2", "trade-db-3"])) as insert_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()) as upsert_metric_window,
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(side_effect=["alert-1", "alert-2", "alert-3"])),
    ):
        await runner.run_adapter_pipeline(
            _events(*raw_events),
            pool,
            engine,
            handler,
            suppression_window_seconds=0,
            stats=stats,
        )

    normalized_events = [call.args[0] for call in normalize_event.call_args_list]
    inserted_trades = [call.args[1] for call in insert_trade.await_args_list]

    return _Check(
        "kalshi_rest_poll_overlap_dedupe",
        {
            "ticker": ticker,
            "poll_windows": [[trade["trade_id"] for trade in poll_one], [trade["trade_id"] for trade in poll_two]],
            "raw_events_seen": stats.raw_events_seen,
            "raw_events_inserted": stats.raw_events_inserted,
            "raw_event_duplicates": stats.raw_event_duplicates,
            "normalized_trades_inserted": stats.normalized_trades_inserted,
            "duplicate_trades": stats.duplicate_trades,
            "metrics_upserted": upsert_metric_window.await_count,
            "alerts_inserted": stats.alerts_inserted,
            "alerts_delivered": stats.alerts_delivered,
            "source_event_ids": [raw.source_event_id for raw in normalized_events],
            "venue_trade_ids": [trade.venue_trade_id for trade in inserted_trades],
            "source_channels": sorted({raw.source_channel for raw in raw_events}),
        },
    )


async def _run_checks() -> list[_Check]:
    return [
        await _raw_event_replay_dedupe(),
        await _duplicate_trade_skip(),
        await _suppression_cache_seed(),
        await _suppression_window_expiry(),
        await _non_trade_raw_persistence(),
        await _kalshi_rest_poll_overlap_dedupe(),
    ]


def run_lifecycle_smoke() -> dict[str, Any]:
    payload = {
        "ok": True,
        "status": "pass",
        "source": "db_free_runner_contracts",
        "checks": [check.as_dict() for check in asyncio.run(_run_checks())],
    }
    return validate_lifecycle_payload(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifecycle_smoke.py")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    try:
        payload = run_lifecycle_smoke()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
    else:
        names = ", ".join(check["name"] for check in payload["checks"])
        print(f"lifecycle-smoke passed: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
