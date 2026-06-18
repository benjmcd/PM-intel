"""Offline tests for US-14 CLI contract and event-time suppression.

No DB required — all tests run in the default offline verify.py sweep.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. CLI arg-parsing contract for replay --from / --to / --venue / --market
# ---------------------------------------------------------------------------

def test_replay_from_db_accepts_new_filter_flags():
    """replay subparser must accept --from, --to, --venue, --market."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args([
        "replay", "--from-db",
        "--from", "2025-01-01T00:00:00Z",
        "--to", "2025-01-02T00:00:00Z",
        "--venue", "polymarket",
        "--market", "some-market-id",
        "--limit", "0",
    ])
    assert ns.from_db is True
    assert ns.replay_from == "2025-01-01T00:00:00Z"
    assert ns.replay_to == "2025-01-02T00:00:00Z"
    assert ns.replay_venue == "polymarket"
    assert ns.replay_market == "some-market-id"
    assert ns.limit == 0


def test_replay_limit_zero_parses():
    """--limit 0 must parse to integer 0 (unlimited sentinel)."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["replay", "--from-db", "--limit", "0"])
    assert ns.limit == 0


def test_replay_from_relative_parses():
    """--from 24h and --to 1h are accepted strings (parsing deferred to cmd_replay)."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["replay", "--from-db", "--from", "24h", "--to", "1h"])
    assert ns.replay_from == "24h"
    assert ns.replay_to == "1h"


def test_replay_persist_flag_parses():
    """--persist flag accepted alongside --from-db."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["replay", "--from-db", "--persist"])
    assert ns.persist is True


def test_replay_default_limit_unchanged():
    """Default --limit remains 100 for back-compat."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["replay", "--from-db"])
    assert ns.limit == 100


def _replay_args(**overrides) -> argparse.Namespace:
    values = {
        "from_db": True,
        "persist": False,
        "fixture_dir": None,
        "limit": 100,
        "verbose": False,
        "replay_from": None,
        "replay_to": None,
        "replay_venue": None,
        "replay_market": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_replay_from_db_invalid_from_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(replay_from="not-a-window"))

    assert rc == 1
    assert "[replay] Invalid --from value: 'not-a-window'" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_naive_iso_from_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(replay_from="2026-06-18T12:00:00"))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[replay] Invalid --from value: '2026-06-18T12:00:00'" in out
    assert "timezone" in out.lower()
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_future_to_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(replay_to="2099-01-01T00:00:00+00:00"))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[replay] Invalid --to value: '2099-01-01T00:00:00+00:00'" in out
    assert "future" in out.lower()
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_inverted_window_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(
            replay_from="2025-06-18T12:00:00+00:00",
            replay_to="2025-06-18T12:00:00+00:00",
        ))

    assert rc == 1
    assert "[replay] --from must be before --to" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_partial_relative_window_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(replay_from="24hours"))

    assert rc == 1
    assert "[replay] Invalid --from value: '24hours'" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_zero_relative_window_returns_one_before_db_replay(capsys):
    from pmfi.cli import cmd_replay

    with (
        patch("pmfi.config.load_config") as load_config,
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.replay.replay_from_db", new=AsyncMock()) as replay_from_db,
    ):
        rc = cmd_replay(_replay_args(replay_from="0h"))

    assert rc == 1
    out = capsys.readouterr().out
    assert "[replay] Invalid --from value: '0h'" in out
    assert "greater than zero" in out
    load_config.assert_not_called()
    create_pool.assert_not_called()
    replay_from_db.assert_not_called()


def test_replay_from_db_valid_aware_iso_passes_parsed_datetimes(capsys):
    from pmfi.cli import cmd_replay

    captured: dict = {}

    async def _fake_replay_from_db(pool, **kwargs):
        captured.update(kwargs)
        return []

    fake_cfg = MagicMock()
    fake_cfg.database.url = "postgresql://fake/db"
    mock_pool = AsyncMock()

    with (
        patch("pmfi.config.load_config", return_value=fake_cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.replay.replay_from_db", side_effect=_fake_replay_from_db),
    ):
        rc = cmd_replay(_replay_args(
            replay_from="2025-06-18T12:00:00+00:00",
            replay_to="2025-06-18T13:00:00+00:00",
        ))

    assert rc == 0
    assert captured["start_ts"] == datetime(2025, 6, 18, 12, tzinfo=timezone.utc)
    assert captured["end_ts"] == datetime(2025, 6, 18, 13, tzinfo=timezone.utc)
    assert "[from-db] replayed 0 raw_event(s)" in capsys.readouterr().out


def test_replay_from_db_valid_relative_window_passes_aware_datetimes():
    from pmfi.cli import cmd_replay

    captured: dict = {}

    async def _fake_replay_from_db(pool, **kwargs):
        captured.update(kwargs)
        return []

    fake_cfg = MagicMock()
    fake_cfg.database.url = "postgresql://fake/db"
    mock_pool = AsyncMock()

    with (
        patch("pmfi.config.load_config", return_value=fake_cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.replay.replay_from_db", side_effect=_fake_replay_from_db),
    ):
        rc = cmd_replay(_replay_args(replay_from="24h", replay_to="1h"))

    assert rc == 0
    assert captured["start_ts"].tzinfo is not None
    assert captured["end_ts"].tzinfo is not None
    assert captured["start_ts"] < captured["end_ts"]


# ---------------------------------------------------------------------------
# 2. _parse_ts helper logic (inline copy mirrors cmd_replay behaviour)
# ---------------------------------------------------------------------------

def _parse_ts(raw: str | None):
    """Mirror of the _parse_ts closure inside cmd_replay."""
    if not raw:
        return None
    raw = raw.strip()
    m = re.fullmatch(r"(\d+)([hdm])", raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            return None
        delta_s = {"h": 3600, "d": 86400, "m": 60}[unit] * n
        return datetime.now(timezone.utc) - timedelta(seconds=delta_s)
    try:
        from pmfi.commands.soak import parse_soak_timestamp
        return parse_soak_timestamp(raw)
    except ValueError:
        return None


def test_parse_ts_iso():
    dt = _parse_ts("2025-06-01T12:00:00Z")
    assert dt is not None
    assert dt.year == 2025
    assert dt.month == 6
    assert dt.tzinfo is not None


def test_parse_ts_relative_hours():
    before = datetime.now(timezone.utc)
    dt = _parse_ts("24h")
    after = datetime.now(timezone.utc)
    assert dt is not None
    expected = before - timedelta(hours=24)
    # Allow ±2s for test execution time
    assert abs((dt - expected).total_seconds()) < 2


def test_parse_ts_relative_days():
    dt = _parse_ts("7d")
    assert dt is not None
    expected = datetime.now(timezone.utc) - timedelta(days=7)
    assert abs((dt - expected).total_seconds()) < 2


def test_parse_ts_relative_minutes():
    dt = _parse_ts("30m")
    assert dt is not None
    expected = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert abs((dt - expected).total_seconds()) < 2


def test_parse_ts_none_returns_none():
    assert _parse_ts(None) is None
    assert _parse_ts("") is None


def test_parse_ts_invalid_returns_none():
    assert _parse_ts("not-a-date") is None


# ---------------------------------------------------------------------------
# 3. Event-time suppression unit tests
# ---------------------------------------------------------------------------

def test_suppression_uses_event_time_same_window():
    """Two trades within the same 5-min event-time window: second is suppressed."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=100)  # 100s later — within 300s suppression window

    def _make_raw(event_id: str, event_ts: datetime) -> RawEvent:
        return RawEvent(
            venue_code="polymarket",
            source_channel="ws_clob",
            source_event_type="trade",
            source_event_id=event_id,
            venue_market_id="test-suppression-eventtime",
            exchange_ts=event_ts,
            payload={"price": "0.65", "size": "50000", "side": "buy", "outcome": "yes"},
        )

    alert = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="v1",
        severity="high",
        confidence="high",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        data_quality="unverified",
        evidence={},
    )

    def _make_mock_trade(event_ts: datetime):
        mt = MagicMock()
        mt.venue_code = "polymarket"
        mt.venue_market_id = "test-suppression-eventtime"
        mt.outcome_key = "yes"
        mt.capital_at_risk_usd = Decimal("50000")
        mt.exchange_ts = event_ts
        mt.received_at = event_ts
        return mt

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_handler = AsyncMock()

    cache: dict = {}

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-sup1", False))),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=[_make_mock_trade(t0), _make_mock_trade(t1)]),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-sup")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-sup1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-sup1")) as mock_insert,
    ):
        mock_engine.evaluate.return_value = [alert]
        asyncio.run(process_event(_make_raw("ev-sup-1", t0), mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1

        asyncio.run(process_event(_make_raw("ev-sup-2", t1), mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1, (
            "Second alert at event_ts+100s should be suppressed (within 300s event-time window)"
        )


def test_suppression_uses_event_time_outside_window():
    """Two trades 10 minutes apart in event-time: both fire (second is outside 5-min window)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=600)  # 10 min later — outside 300s window

    def _make_raw(event_id: str, event_ts: datetime) -> RawEvent:
        return RawEvent(
            venue_code="polymarket",
            source_channel="ws_clob",
            source_event_type="trade",
            source_event_id=event_id,
            venue_market_id="test-suppression-outside",
            exchange_ts=event_ts,
            payload={"price": "0.65", "size": "50000", "side": "buy", "outcome": "yes"},
        )

    alert = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="v1",
        severity="high",
        confidence="high",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        data_quality="unverified",
        evidence={},
    )

    def _make_mock_trade(event_ts: datetime):
        mt = MagicMock()
        mt.venue_code = "polymarket"
        mt.venue_market_id = "test-suppression-outside"
        mt.outcome_key = "yes"
        mt.capital_at_risk_usd = Decimal("50000")
        mt.exchange_ts = event_ts
        mt.received_at = event_ts
        return mt

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_handler = AsyncMock()

    cache: dict = {}

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-out1", False))),
        patch("pmfi.pipeline.runner.normalize_event", side_effect=[_make_mock_trade(t0), _make_mock_trade(t1)]),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-out")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-out1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-out1")) as mock_insert,
    ):
        mock_engine.evaluate.return_value = [alert]
        asyncio.run(process_event(_make_raw("ev-out-1", t0), mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1

        asyncio.run(process_event(_make_raw("ev-out-2", t1), mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 2, (
            "Second alert at event_ts+600s should NOT be suppressed (outside 300s event-time window)"
        )


# ---------------------------------------------------------------------------
# 4. seed_from_db query logic — fake-conn unit test
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal async connection stub that returns a fixed row list."""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetch(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return self._rows


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        pass


def test_seed_from_db_warms_accumulator():
    """seed_from_db feeds trades into _accumulator, _momentum_acc, and _vs_history."""
    import asyncio
    from pmfi.pipeline.engine import AlertEngine

    t_before = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_trade = t_before - timedelta(seconds=60)

    fake_rows = [
        {
            "venue_code": "polymarket",
            "venue_market_id": "seed-market-1",
            "directional_side": "yes",
            "capital_at_risk_usd": Decimal("10000"),
            "price": Decimal("0.55"),
            "event_ts": t_trade,
        },
        {
            "venue_code": "polymarket",
            "venue_market_id": "seed-market-1",
            "directional_side": "yes",
            "capital_at_risk_usd": Decimal("8000"),
            "price": Decimal("0.57"),
            "event_ts": t_trade + timedelta(seconds=10),
        },
    ]

    conn = _FakeConn(fake_rows)
    pool = _FakePool(conn)
    engine = AlertEngine()

    asyncio.run(engine.seed_from_db(pool, before_ts=t_before))

    # _vs_history should have 2 entries for the seed market
    vskey = "polymarket:seed-market-1"
    assert vskey in engine._vs_history
    assert len(engine._vs_history[vskey]) == 2
    assert engine._vs_history[vskey][0] == Decimal("10000")
    assert engine._vs_history[vskey][1] == Decimal("8000")

    # _accumulator should have the trades buffered
    buf = engine._accumulator._buffers.get(vskey)
    assert buf is not None
    assert len(buf) == 2

    # _momentum_acc should also have them
    mbuf = engine._momentum_acc._buffers.get(vskey)
    assert mbuf is not None
    assert len(mbuf) == 2

    # Query must bound by cutoff and before_ts
    assert conn.last_query is not None
    assert "$1" in conn.last_query
    assert "$2" in conn.last_query


def test_seed_from_db_empty_result_no_crash():
    """seed_from_db with no pre-existing trades must not raise."""
    import asyncio
    from pmfi.pipeline.engine import AlertEngine

    t_before = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    conn = _FakeConn([])
    pool = _FakePool(conn)
    engine = AlertEngine()
    asyncio.run(engine.seed_from_db(pool, before_ts=t_before))
    assert engine._vs_history == {}
