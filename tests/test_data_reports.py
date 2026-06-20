from __future__ import annotations

import argparse
import json
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
