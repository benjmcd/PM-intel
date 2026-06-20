from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def test_data_coverage_parser_accepts_read_only_window_flags():
    from pmfi.cli import _build_parser

    parser = _build_parser()
    ns = parser.parse_args(
        [
            "data-coverage",
            "--since",
            "24h",
            "--until",
            "2026-06-20T12:00:00+00:00",
            "--venue",
            "polymarket",
            "--format",
            "json",
        ]
    )

    assert ns.command == "data-coverage"
    assert ns.since == "24h"
    assert ns.until == "2026-06-20T12:00:00+00:00"
    assert ns.venue == "polymarket"
    assert ns.format == "json"


def test_data_coverage_summary_classifies_all_dispositions():
    from pmfi.data_reports import (
        NON_TRADE_RAW_EVENT_TYPES_BY_VENUE,
        summarize_data_coverage_rows,
    )

    assert NON_TRADE_RAW_EVENT_TYPES_BY_VENUE["polymarket"] == frozenset(
        {"price_change", "book", "best_bid_ask", "new_market"}
    )

    report = summarize_data_coverage_rows(
        [
            {
                "venue_code": "kalshi",
                "source_event_type": "trade",
                "has_normalized": True,
                "has_dead_letter": False,
                "cnt": 3,
            },
            {
                "venue_code": "polymarket",
                "source_event_type": "last_trade_price",
                "has_normalized": False,
                "has_dead_letter": True,
                "cnt": 2,
            },
            {
                "venue_code": "polymarket",
                "source_event_type": "price_change",
                "has_normalized": False,
                "has_dead_letter": False,
                "cnt": 4,
            },
            {
                "venue_code": "polymarket",
                "source_event_type": "last_trade_price",
                "has_normalized": False,
                "has_dead_letter": False,
                "cnt": 1,
            },
        ]
    )

    assert report["total_raw_events"] == 10
    assert report["counts"] == {
        "normalized": 3,
        "dead_lettered": 2,
        "skipped_non_trade": 4,
        "unaccounted": 1,
    }
    assert report["accounted_raw_events"] == 9
    assert report["coverage_percent"] == 90.0
    assert report["has_unaccounted_warning"] is True
    assert report["unaccounted_event_types"] == [
        {"venue_code": "polymarket", "source_event_type": "last_trade_price", "count": 1}
    ]


def test_cmd_data_coverage_reports_unaccounted_rows_as_nonzero_json(capsys):
    import asyncpg

    from pmfi.commands.data import cmd_data_coverage

    rows = [
        {
            "venue_code": "kalshi",
            "source_event_type": "trade",
            "has_normalized": True,
            "has_dead_letter": False,
            "cnt": 3,
        },
        {
            "venue_code": "polymarket",
            "source_event_type": "last_trade_price",
            "has_normalized": False,
            "has_dead_letter": False,
            "cnt": 1,
        },
    ]
    pool = SimpleNamespace(fetch=AsyncMock(return_value=rows), close=AsyncMock())

    async def _fake_create_pool(*_args, **_kwargs):
        return pool

    args = argparse.Namespace(since=None, until=None, venue=None, format="json")

    with patch.object(asyncpg, "create_pool", side_effect=_fake_create_pool), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = SimpleNamespace(database=SimpleNamespace(url="postgresql://localhost/test"))
        rc = cmd_data_coverage(args)

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["normalized"] == 3
    assert payload["counts"]["unaccounted"] == 1
    assert payload["coverage_percent"] == 75.0
    assert payload["has_unaccounted_warning"] is True

    sql = pool.fetch.await_args.args[0]
    assert "FROM raw_events" in sql
    assert "normalized_trades" in sql
    assert "dead_letters" in sql
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()


def test_backtest_analytics_parser_accepts_read_only_sweep_flags():
    from pmfi.cli import _build_parser

    parser = _build_parser()
    ns = parser.parse_args(
        [
            "backtest-analytics",
            "--from",
            "24h",
            "--to",
            "1h",
            "--limit",
            "0",
            "--venue",
            "kalshi",
            "--market",
            "KXBTCD",
            "--volume-spike-min-trade-usd",
            "850",
            "--volume-spike-min-trade-usd",
            "1000",
            "--cold-start",
            "--format",
            "json",
        ]
    )

    assert ns.command == "backtest-analytics"
    assert ns.backtest_from == "24h"
    assert ns.backtest_to == "1h"
    assert ns.limit == 0
    assert ns.backtest_venue == "kalshi"
    assert ns.backtest_market == "KXBTCD"
    assert ns.volume_spike_min_trade_usd == [850.0, 1000.0]
    assert ns.cold_start is True
    assert ns.format == "json"


def _trade(venue_market_id: str = "market-1"):
    from pmfi.domain import NormalizedTrade

    return NormalizedTrade(
        venue_code="kalshi",
        venue_market_id=venue_market_id,
        outcome_key="yes",
        price=Decimal("0.55"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("55"),
        payout_notional_usd=Decimal("100"),
        received_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
    )


def _decision(rule_id: str):
    from pmfi.domain import AlertDecision

    return AlertDecision(
        emit_alert=True,
        rule_id=rule_id,
        rule_version="alert_rules.v1",
        severity="low",
        confidence="medium",
        score=Decimal("0.75"),
        reason_codes=("test",),
        evidence={"rule": rule_id},
        data_quality="live",
    )


def test_backtest_analytics_summarizes_replay_fire_counts_with_review_governance():
    from pmfi.data_reports import summarize_backtest_analytics
    from pmfi.replay import ReplayResult

    results = [
        ReplayResult("db:1", _trade("m1"), [_decision("volume_spike_v1")], raw_event_id=101),
        ReplayResult("db:2", _trade("m1"), [_decision("volume_spike_v1")], raw_event_id=102),
        ReplayResult("db:3", _trade("m2"), [_decision("large_trade_absolute_v1")], raw_event_id=201),
    ]

    report = summarize_backtest_analytics(
        results,
        review_index={
            (101, "volume_spike_v1"): "noise",
            (102, "volume_spike_v1"): "tp",
            (201, "large_trade_absolute_v1"): "fp",
        },
        fp_rate_targets={
            "volume_spike_v1": 30.0,
            "large_trade_absolute_v1": 15.0,
        },
        min_reviewed_by_rule={
            "volume_spike_v1": 5,
            "large_trade_absolute_v1": 1,
        },
    )

    by_rule = {row["rule_key"]: row for row in report["per_rule"]}
    assert report["normalized_trades_replayed"] == 3
    assert report["total_alerts"] == 3
    assert by_rule["volume_spike_v1"]["fire_count"] == 2
    assert by_rule["volume_spike_v1"]["reviewed"] == 2
    assert by_rule["volume_spike_v1"]["not_actionable_rate"] == 50.0
    assert by_rule["volume_spike_v1"]["status"] == "INSUFFICIENT"
    assert by_rule["large_trade_absolute_v1"]["status"] == "BREACH"


def test_volume_spike_sensitivity_rows_show_fire_count_and_review_deltas():
    from pmfi.data_reports import build_volume_spike_sensitivity_rows

    rows = build_volume_spike_sensitivity_rows(
        [
            {
                "min_trade_usd": 850.0,
                "summary": {
                    "per_rule": [
                        {
                            "rule_key": "volume_spike_v1",
                            "fire_count": 2,
                            "reviewed": 2,
                            "not_actionable_rate": 50.0,
                            "status": "INSUFFICIENT",
                        }
                    ]
                },
            },
            {
                "min_trade_usd": 1000.0,
                "summary": {
                    "per_rule": [
                        {
                            "rule_key": "volume_spike_v1",
                            "fire_count": 1,
                            "reviewed": 1,
                            "not_actionable_rate": 0.0,
                            "status": "INSUFFICIENT",
                        }
                    ]
                },
            },
        ],
        baseline_min_trade_usd=850.0,
    )

    assert rows[0]["fire_count_delta"] == 0
    assert rows[0]["not_actionable_rate_delta"] == 0.0
    assert rows[1]["fire_count_delta"] == -1
    assert rows[1]["not_actionable_rate_delta"] == -50.0


def test_cmd_backtest_analytics_runs_read_only_replays_and_sweep(capsys):
    import asyncpg

    from pmfi.commands.data import cmd_backtest_analytics
    from pmfi.replay import ReplayResult

    current_results = [
        ReplayResult("db:1", _trade("m1"), [_decision("volume_spike_v1")], raw_event_id=101)
    ]
    candidate_results = [
        ReplayResult("db:1", _trade("m1"), [], raw_event_id=101)
    ]
    review_rows = [{"raw_event_id": 101, "rule_key": "volume_spike_v1", "label": "noise"}]
    pool = SimpleNamespace(
        fetch=AsyncMock(return_value=review_rows),
        execute=AsyncMock(),
        close=AsyncMock(),
    )

    async def _fake_create_pool(*_args, **_kwargs):
        return pool

    args = argparse.Namespace(
        backtest_from=None,
        backtest_to=None,
        limit=0,
        backtest_venue=None,
        backtest_market=None,
        volume_spike_min_trade_usd=[850.0, 1000.0],
        cold_start=False,
        format="json",
    )

    replay = AsyncMock(side_effect=[current_results, candidate_results])
    with patch.object(asyncpg, "create_pool", side_effect=_fake_create_pool), \
            patch("pmfi.replay.replay_from_db", replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = SimpleNamespace(database=SimpleNamespace(url="postgresql://localhost/test"))
        rc = cmd_backtest_analytics(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    by_rule = {row["rule_key"]: row for row in payload["current"]["per_rule"]}
    assert by_rule["volume_spike_v1"]["fire_count"] == 1
    assert payload["volume_spike_sensitivity"][1]["min_trade_usd"] == 1000.0
    assert payload["volume_spike_sensitivity"][1]["fire_count_delta"] == -1
    assert all(call.kwargs["persist"] is False for call in replay.await_args_list)
    assert all(call.kwargs["normalized_only"] is True for call in replay.await_args_list)
    pool.execute.assert_not_awaited()
