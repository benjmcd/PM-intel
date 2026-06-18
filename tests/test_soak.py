from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _summary(**overrides):
    start = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=90)
    data = {
        "window": {"start_at": start, "end_at": end},
        "raw_events": 12,
        "normalized_trades": 5,
        "alerts": 2,
        "unresolved_dead_letters": 0,
        "open_data_quality_incidents": 0,
        "first_raw_event_at": start,
        "last_raw_event_at": end,
        "first_trade_at": start + timedelta(minutes=5),
        "last_trade_at": end - timedelta(minutes=5),
        "first_alert_at": start + timedelta(minutes=20),
        "last_alert_at": start + timedelta(minutes=30),
        "venues": [{
            "venue_code": "polymarket",
            "raw_events": 12,
            "normalized_trades": 5,
            "first_raw_event_at": start,
            "last_raw_event_at": end,
            "first_trade_at": start + timedelta(minutes=5),
            "last_trade_at": end - timedelta(minutes=5),
        }],
    }
    data.update(overrides)
    return data


def _args(**overrides):
    defaults = {
        "window": "2h",
        "min_duration_minutes": 60,
        "min_raw_events": 1,
        "min_trades": 1,
        "max_dead_letters": 0,
        "max_incidents": 0,
        "required_venue": [],
        "format": "text",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_soak_parser_accepts_thresholds():
    from pmfi.cli import _build_parser

    args = _build_parser().parse_args([
        "soak",
        "--window",
        "90m",
        "--min-duration-minutes",
        "45",
        "--min-raw-events",
        "10",
        "--min-trades",
        "3",
        "--required-venue",
        "polymarket,kalshi",
        "--max-dead-letters",
        "1",
        "--max-incidents",
        "0",
        "--format",
        "json",
    ])

    assert args.command == "soak"
    assert args.window == "90m"
    assert args.required_venue == ["polymarket,kalshi"]
    assert args.format == "json"


def test_evaluate_soak_passes_with_enough_evidence():
    from pmfi.commands.soak import SoakThresholds, evaluate_soak

    result = evaluate_soak(_summary(), SoakThresholds(required_venues=("polymarket",)))

    assert result["ok"] is True
    assert result["counts"]["raw_events"] == 12
    assert result["timestamps"]["raw_evidence_duration_minutes"] == 90
    assert result["venues"][0]["venue_code"] == "polymarket"
    assert result["failures"] == []


def test_render_text_includes_per_venue_rows_without_crashing():
    from pmfi.commands.soak import SoakThresholds, evaluate_soak, render_text

    result = evaluate_soak(_summary(), SoakThresholds(required_venues=("polymarket",)))

    rendered = render_text(result)

    assert "Soak readiness: PASS" in rendered
    assert "polymarket: raw=12 trades=5" in rendered


def test_evaluate_soak_fails_closed_on_missing_and_unresolved_evidence():
    from pmfi.commands.soak import SoakThresholds, evaluate_soak

    start = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    result = evaluate_soak(
        _summary(
            raw_events=0,
            normalized_trades=0,
            unresolved_dead_letters=2,
            open_data_quality_incidents=1,
            first_raw_event_at=start,
            last_raw_event_at=start,
            venues=[],
        ),
        SoakThresholds(required_venues=("kalshi",)),
    )

    assert result["ok"] is False
    assert "no raw events in window" in result["failures"]
    assert any("normalized trades" in item for item in result["failures"])
    assert any("unresolved dead letters" in item for item in result["failures"])
    assert any("open data-quality incidents" in item for item in result["failures"])
    assert result["missing_required_venues"] == [{
        "venue_code": "kalshi",
        "reasons": ["missing raw events", "missing normalized trades"],
    }]


def test_fetch_soak_summary_merges_per_venue_and_uses_select_only():
    from pmfi.commands.soak import fetch_soak_summary

    class Conn:
        def __init__(self):
            self.sqls: list[str] = []
            self.fetchrow = AsyncMock(side_effect=[
                {"raw_events": 3, "first_raw_event_at": "r1", "last_raw_event_at": "r2"},
                {"normalized_trades": 2, "first_trade_at": "t1", "last_trade_at": "t2"},
                {"alerts": 1, "first_alert_at": "a1", "last_alert_at": "a2"},
            ])
            self.fetchval = AsyncMock(side_effect=[0, 0])
            self.fetch = AsyncMock(side_effect=[
                [{"venue_code": "polymarket", "raw_events": 3, "first_raw_event_at": "r1", "last_raw_event_at": "r2"}],
                [{"venue_code": "kalshi", "normalized_trades": 2, "first_trade_at": "t1", "last_trade_at": "t2"}],
            ])

    conn = Conn()
    start = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    result = asyncio.run(fetch_soak_summary(conn, start_at=start, end_at=end))
    sqls = [call.args[0].strip().lower() for mock in (conn.fetchrow, conn.fetchval, conn.fetch) for call in mock.call_args_list]

    assert result["raw_events"] == 3
    assert result["normalized_trades"] == 2
    assert {row["venue_code"] for row in result["venues"]} == {"polymarket", "kalshi"}
    assert all(sql.startswith("select") for sql in sqls)


def test_cmd_soak_json_returns_zero_for_passing_summary(capsys):
    from pmfi.commands.soak import cmd_soak

    class Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    with patch("pmfi.config.load_config", return_value=SimpleNamespace(database=SimpleNamespace(url="postgres://local"))), \
            patch("pmfi.db.create_pool", new=AsyncMock(return_value=Pool())), \
            patch("pmfi.db.close_pool", new=AsyncMock()), \
            patch("pmfi.commands.soak.fetch_soak_summary", new=AsyncMock(return_value=_summary())):
        rc = cmd_soak(_args(format="json", required_venue=["polymarket"]))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["counts"]["normalized_trades"] == 5


def test_cmd_soak_invalid_args_fail_before_db(capsys):
    from pmfi.commands.soak import cmd_soak

    with patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool:
        rc = cmd_soak(_args(window="soon"))

    assert rc == 1
    assert "Invalid --window" in capsys.readouterr().err
    create_pool.assert_not_called()


def test_cmd_soak_db_unavailable_returns_one(capsys):
    from pmfi.commands.soak import cmd_soak

    with patch("pmfi.config.load_config", return_value=SimpleNamespace(database=SimpleNamespace(url="postgres://local"))), \
            patch("pmfi.db.create_pool", new=AsyncMock(side_effect=Exception("refused"))):
        rc = cmd_soak(_args())

    assert rc == 1
    assert "DB unavailable" in capsys.readouterr().err


def test_task_soak_routes_threshold_args(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "soak",
        "--window",
        "90m",
        "--min-duration-minutes",
        "45",
        "--min-raw-events",
        "10",
        "--min-trades",
        "3",
        "--required-venue",
        "polymarket",
        "--max-dead-letters",
        "1",
        "--max-incidents",
        "2",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "soak",
        "--window",
        "90m",
        "--min-duration-minutes",
        "45",
        "--min-raw-events",
        "10",
        "--min-trades",
        "3",
        "--max-dead-letters",
        "1",
        "--max-incidents",
        "2",
        "--format",
        "json",
        "--required-venue",
        "polymarket",
    )]
