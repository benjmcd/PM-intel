"""Offline tests for cmd_stats, cmd_dead_letters, and cmd_report.

All DB calls are mocked — no real Postgres required.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {"limit": 20, "since": "24h", "format": "table"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _pool_fetchval_side_effect(counts: dict):
    """Return a side_effect list that answers each fetchval call with a canned count."""
    # cmd_stats calls fetchval 11 times; we just return 0 for each.
    return [0] * 20


# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    def _make_pool(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])
        pool.close = AsyncMock()
        return pool

    def test_returns_zero_on_success(self, capsys):
        from pmfi.commands.reporting import cmd_stats
        pool = self._make_pool()
        with patch("pmfi.db.create_pool", new=AsyncMock(return_value=pool)):
            with patch("pmfi.db.close_pool", new=AsyncMock()):
                rc = cmd_stats(_make_args())
        assert rc == 0

    def test_returns_one_on_db_error(self, capsys):
        from pmfi.commands.reporting import cmd_stats
        with patch("pmfi.db.create_pool", new=AsyncMock(side_effect=Exception("conn refused"))):
            rc = cmd_stats(_make_args())
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_dead_letters
# ---------------------------------------------------------------------------

class TestCmdDeadLetters:
    def test_no_dead_letters_returns_zero(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 0
        out = capsys.readouterr().out
        assert "No dead letters" in out

    def test_no_dead_letters_json_returns_empty_rows(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20, format="json"))
        assert rc == 0
        assert json.loads(capsys.readouterr().out) == []

    def test_db_connect_failure_returns_one(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        with patch("asyncpg.create_pool", new=AsyncMock(side_effect=Exception("unreachable"))):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 1

    def test_dead_letters_present_returns_zero(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "dead_letter_id": "12345678-0000-0000-0000-000000000001",
            "created_at": "2026-01-01 12:00:00",
            "venue_code": "polymarket",
            "failure_stage": "normalization",
            "error_class": "invalid_price_or_size",
            "error_message": "price parse failed",
            "source_channel": "ws",
            "resolved": True,
            "resolved_at": datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc),
            "payload_preview": '{"asset_id": "abc"}',
        }])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 0
        out = capsys.readouterr().out
        assert "12345678" in out

    def test_dead_letters_json_includes_safe_machine_readable_rows(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        created_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "dead_letter_id": "12345678-0000-0000-0000-000000000001",
            "created_at": created_at,
            "venue_code": "polymarket",
            "failure_stage": "normalization",
            "error_class": "invalid_price_or_size",
            "error_message": "price parse failed",
            "source_channel": "ws",
            "resolved": True,
            "resolved_at": datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc),
            "payload_preview": '{"asset_id": "abc"}',
        }])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20, format="json"))

        assert rc == 0
        rows = json.loads(capsys.readouterr().out)
        assert rows == [{
            "dead_letter_id": "12345678-0000-0000-0000-000000000001",
            "short_id": "12345678",
            "created_at": "2026-01-01T12:00:00+00:00",
            "venue_code": "polymarket",
            "failure_stage": "normalization",
            "error_class": "invalid_price_or_size",
            "error_message": "price parse failed",
            "source_channel": "ws",
            "resolved": True,
            "resolved_at": "2026-01-01T12:05:00+00:00",
            "payload_preview": '{"asset_id": "abc"}',
        }]
        assert "payload" not in rows[0]
        sql = pool.fetch.await_args.args[0]
        assert "dl.resolved" in sql
        assert "LEFT(dl.payload::text, 120) AS payload_preview" in sql

    def test_resolve_success_updates_one_unresolved_row(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "dead_letter_id": "abcdef12-0000-0000-0000-000000000001",
            "created_at": "2026-01-01 12:00:00",
            "venue_code": "polymarket",
            "failure_stage": "normalization",
            "error_class": "invalid_price_or_size",
            "error_message": "price parse failed",
        }])
        pool.fetchrow = AsyncMock(return_value={
            "dead_letter_id": "abcdef12-0000-0000-0000-000000000001",
            "resolved_at": "2026-01-01 12:05:00",
        })
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(
                dead_letters_cmd="resolve",
                dead_letter_id_or_prefix="abcdef12",
                dry_run=False,
            ))
        assert rc == 0
        assert "resolved" in capsys.readouterr().out.lower()
        pool.fetchrow.assert_awaited_once()
        sql = pool.fetchrow.await_args.args[0]
        assert "resolved = false" in sql
        assert "resolved_at = now()" in sql

    def test_resolve_not_found_fails_closed_without_update(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock()
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(
                dead_letters_cmd="resolve",
                dead_letter_id_or_prefix="missing0",
                dry_run=False,
            ))
        assert rc == 1
        assert "No unresolved dead letter" in capsys.readouterr().out
        pool.fetchrow.assert_not_awaited()

    def test_resolve_short_prefix_fails_before_db(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters

        with patch("asyncpg.create_pool", new=AsyncMock()) as create_pool, \
                patch("pmfi.config.load_config") as load_config:
            rc = cmd_dead_letters(_make_args(
                dead_letters_cmd="resolve",
                dead_letter_id_or_prefix="abc",
                dry_run=False,
            ))

        assert rc == 1
        assert "at least 8 characters" in capsys.readouterr().out
        load_config.assert_not_called()
        create_pool.assert_not_called()

    def test_resolve_ambiguous_prefix_fails_closed_without_update(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[
            {"dead_letter_id": "abc00000-0000-0000-0000-000000000001"},
            {"dead_letter_id": "abc00000-1111-0000-0000-000000000002"},
        ])
        pool.fetchrow = AsyncMock()
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(
                dead_letters_cmd="resolve",
                dead_letter_id_or_prefix="abc00000",
                dry_run=False,
            ))
        assert rc == 1
        assert "Ambiguous" in capsys.readouterr().out
        pool.fetchrow.assert_not_awaited()

    def test_resolve_dry_run_previews_without_update(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "dead_letter_id": "fedcba98-0000-0000-0000-000000000001",
            "created_at": "2026-01-01 12:00:00",
            "venue_code": "kalshi",
            "failure_stage": "normalization",
            "error_class": "missing_market",
            "error_message": "market missing",
        }])
        pool.fetchrow = AsyncMock()
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(
                dead_letters_cmd="resolve",
                dead_letter_id_or_prefix="fedcba98",
                dry_run=True,
            ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out
        assert "fedcba98" in out
        pool.fetchrow.assert_not_awaited()


# ---------------------------------------------------------------------------
# cmd_report
# ---------------------------------------------------------------------------

class TestCmdReport:
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
            self.close = AsyncMock()

        def acquire(self):
            return TestCmdReport._Acquire(self.conn)

    def _summary(self):
        now = datetime(2026, 6, 17, 12, 30, tzinfo=timezone.utc)
        return {
            "total": 3,
            "by_severity": [{"severity": "high", "cnt": 2}],
            "by_rule": [{"rule_key": "large_trade_v1", "cnt": 3}],
            "by_venue": [{"venue_code": "polymarket", "cnt": 3}],
            "top_markets": [{"title": "Election winner", "cnt": 3, "max_severity": "high"}],
            "recent_high": [],
            "review_queue": {
                "total": 1,
                "alerts": [{
                    "alert_id": "abc12345-0000-0000-0000-000000000000",
                    "created_at": now,
                    "rule_key": "large_trade_v1",
                    "severity": "high",
                    "venue_code": "polymarket",
                    "title": "Election winner",
                    "triage_flags": ["low_notional", "near_threshold"],
                }],
                "triage_flags": {
                    "total_flagged": 1,
                    "by_flag": [
                        {"flag": "low_notional", "cnt": 1},
                        {"flag": "near_threshold", "cnt": 1},
                    ],
                },
            },
            "review_outcomes": {
                "reviewed_total": 2,
                "by_label": [{"label": "fp", "cnt": 1}, {"label": "tp", "cnt": 1}],
                "false_positive_categories": [{"category": "stale_metadata", "cnt": 1}],
            },
            "data_gaps": {
                "unresolved_dead_letters": {
                    "total": 2,
                    "by_stage": [{
                        "failure_stage": "normalization",
                        "error_class": "invalid_price_or_size",
                        "cnt": 2,
                    }],
                    "recent": [],
                },
                "open_data_quality_incidents": {
                    "total": 1,
                    "by_type": [{
                        "severity": "medium",
                        "incident_type": "feed_gap",
                        "cnt": 1,
                    }],
                    "recent": [],
                },
            },
            "since": "2026-06-16T12:30:00+00:00",
        }

    def _run_with_summary(self, args, summary):
        from pmfi.commands.reporting import cmd_report

        conn = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[10, 8, 2])
        pool = self._Pool(conn)
        with patch("pmfi.db.create_pool", new=AsyncMock(return_value=pool)):
            with patch("pmfi.db.repos.alerts.get_alert_summary", new=AsyncMock(return_value=summary)):
                return cmd_report(args)

    def test_db_unavailable_returns_one(self, capsys):
        from pmfi.commands.reporting import cmd_report
        with patch("pmfi.db.create_pool", new=AsyncMock(side_effect=Exception("no db"))):
            rc = cmd_report(_make_args())
        assert rc == 1
        out = capsys.readouterr().out
        assert "unavailable" in out.lower() or "db" in out.lower()

    def test_invalid_since_returns_one_before_db_access(self, capsys):
        from pmfi.commands.reporting import cmd_report

        with patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool:
            rc = cmd_report(_make_args(since="not-a-window"))

        assert rc == 1
        assert "Invalid --since value" in capsys.readouterr().out
        create_pool.assert_not_called()

    def test_table_includes_triage_sections(self, capsys):
        rc = self._run_with_summary(_make_args(format="table"), self._summary())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Review queue:" in out
        assert "abc12345" in out
        assert "Triage flags: low_notional=1  near_threshold=1" in out
        assert "Review outcomes:" in out
        assert "Reviewed alerts: 2" in out
        assert "FP categories: stale_metadata=1" in out
        assert "Data gaps:" in out
        assert "normalization / invalid_price_or_size: 2" in out
        assert "[medium] feed_gap: 1" in out

    def test_json_includes_triage_keys(self, capsys):
        rc = self._run_with_summary(_make_args(format="json"), self._summary())
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["review_queue"]["total"] == 1
        assert payload["review_queue"]["triage_flags"] == {
            "total_flagged": 1,
            "by_flag": [
                {"flag": "low_notional", "cnt": 1},
                {"flag": "near_threshold", "cnt": 1},
            ],
        }
        assert payload["review_queue"]["alerts"][0]["triage_flags"] == [
            "low_notional",
            "near_threshold",
        ]
        assert "evidence" not in payload["review_queue"]["alerts"][0]
        assert "raw_event_id" not in payload["review_queue"]["alerts"][0]
        assert "trade_id" not in payload["review_queue"]["alerts"][0]
        assert payload["review_outcomes"]["reviewed_total"] == 2
        assert payload["data_gaps"]["unresolved_dead_letters"]["total"] == 2
        assert payload["data_gaps"]["open_data_quality_incidents"]["total"] == 1


class TestAlertSummaryQueries:
    class _Conn:
        def __init__(self):
            self.fetch_sqls: list[str] = []
            self.fetchval_sqls: list[str] = []
            self.execute_sqls: list[str] = []

        async def fetchrow(self, sql, *args):
            return {"total": 0}

        async def fetchval(self, sql, *args):
            self.fetchval_sqls.append(sql)
            return 0

        async def fetch(self, sql, *args):
            self.fetch_sqls.append(sql)
            return []

        async def execute(self, sql, *args):
            self.execute_sqls.append(sql)
            raise AssertionError("get_alert_summary must not write rows")

    def test_summary_adds_latest_review_and_data_gap_queries(self):
        import asyncio
        from pmfi.db.repos.alerts import get_alert_summary

        conn = self._Conn()
        since = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
        summary = asyncio.run(get_alert_summary(conn, since=since))

        assert summary["review_queue"] == {
            "total": 0,
            "alerts": [],
            "triage_flags": {"total_flagged": 0, "by_flag": []},
        }
        assert summary["review_outcomes"]["reviewed_total"] == 0
        assert summary["data_gaps"]["unresolved_dead_letters"]["total"] == 0
        assert summary["data_gaps"]["open_data_quality_incidents"]["total"] == 0
        assert any("NOT EXISTS" in sql and "alert_reviews" in sql for sql in conn.fetch_sqls + conn.fetchval_sqls)
        assert any("DISTINCT ON (ar.alert_id)" in sql for sql in conn.fetch_sqls)
        assert any("data_quality_incidents" in sql for sql in conn.fetch_sqls + conn.fetchval_sqls)

    def test_summary_adds_review_queue_triage_flag_counts_without_writes(self):
        import asyncio
        from pmfi.db.repos.alerts import get_alert_summary

        class Conn(TestAlertSummaryQueries._Conn):
            async def fetchval(self, sql, *args):
                self.fetchval_sqls.append(sql)
                if "NOT EXISTS" in sql and "alert_reviews" in sql:
                    return 2
                return 0

            async def fetch(self, sql, *args):
                self.fetch_sqls.append(sql)
                if "SELECT a.alert_id::text AS alert_id" in sql:
                    return [
                        {
                            "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "created_at": datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
                            "rule_key": "volume_spike_v1",
                            "severity": "medium",
                            "venue_code": "kalshi",
                            "title": "Bitcoin price",
                            "data_quality": "live",
                            "raw_event_id": 1,
                            "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "evidence": (
                                '{"this_trade_usd": 760, "spike_multiplier": 5.07, '
                                '"min_spike_multiplier": 5.0, "baseline_trades": 20}'
                            ),
                        },
                        {
                            "alert_id": "cccccccc-dddd-eeee-ffff-000000000001",
                            "created_at": datetime(2026, 6, 18, 12, 1, tzinfo=timezone.utc),
                            "rule_key": "market_relative_large_trade_v1",
                            "severity": "low",
                            "venue_code": "polymarket",
                            "title": "Election winner",
                            "data_quality": "baseline_pending",
                            "raw_event_id": None,
                            "trade_id": None,
                            "evidence": {
                                "capital_at_risk_usd": "5200",
                                "min_capital_threshold_usd": "5000",
                                "degraded_reasons": ["missing_directional_side"],
                            },
                        },
                    ]
                if "SELECT a.data_quality" in sql and "a.evidence" in sql:
                    return [
                        {
                            "data_quality": "live",
                            "raw_event_id": 1,
                            "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "evidence": (
                                '{"this_trade_usd": 760, "spike_multiplier": 5.07, '
                                '"min_spike_multiplier": 5.0, "baseline_trades": 20}'
                            ),
                        },
                        {
                            "data_quality": "baseline_pending",
                            "raw_event_id": None,
                            "trade_id": None,
                            "evidence": {
                                "capital_at_risk_usd": "5200",
                                "min_capital_threshold_usd": "5000",
                                "degraded_reasons": ["missing_directional_side"],
                            },
                        },
                    ]
                return []

        conn = Conn()
        since = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
        summary = asyncio.run(get_alert_summary(conn, since=since))

        assert summary["review_queue"]["total"] == 2
        assert summary["review_queue"]["alerts"][0]["triage_flags"] == [
            "low_notional",
            "thin_baseline",
            "near_threshold",
        ]
        assert summary["review_queue"]["alerts"][1]["triage_flags"] == [
            "near_threshold",
            "degraded_data_quality",
            "missing_lineage",
        ]
        assert summary["review_queue"]["triage_flags"] == {
            "total_flagged": 2,
            "by_flag": [
                {"flag": "near_threshold", "cnt": 2},
                {"flag": "degraded_data_quality", "cnt": 1},
                {"flag": "low_notional", "cnt": 1},
                {"flag": "missing_lineage", "cnt": 1},
                {"flag": "thin_baseline", "cnt": 1},
            ],
        }
        review_queue_sql = next(
            sql for sql in conn.fetch_sqls if "SELECT a.alert_id::text AS alert_id" in sql
        )
        assert "a.evidence" in review_queue_sql
        assert "a.raw_event_id" in review_queue_sql
        assert "a.trade_id::text AS trade_id" in review_queue_sql
        assert conn.execute_sqls == []

    def test_summary_triage_counts_cover_full_unreviewed_queue_not_preview_only(self):
        import asyncio
        from pmfi.db.repos.alerts import get_alert_summary

        class Conn(TestAlertSummaryQueries._Conn):
            async def fetchval(self, sql, *args):
                self.fetchval_sqls.append(sql)
                if "NOT EXISTS" in sql and "alert_reviews" in sql:
                    return 3
                return 0

            async def fetch(self, sql, *args):
                self.fetch_sqls.append(sql)
                if "SELECT a.alert_id::text AS alert_id" in sql:
                    return [
                        {
                            "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "created_at": datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
                            "rule_key": "volume_spike_v1",
                            "severity": "medium",
                            "venue_code": "kalshi",
                            "title": "Preview row",
                            "data_quality": "live",
                            "raw_event_id": 1,
                            "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "evidence": '{"this_trade_usd": 760}',
                        },
                    ]
                if "SELECT a.data_quality" in sql and "a.evidence" in sql:
                    return [
                        {
                            "data_quality": "live",
                            "raw_event_id": 1,
                            "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                            "evidence": '{"this_trade_usd": 760}',
                        },
                        {
                            "data_quality": "baseline_pending",
                            "raw_event_id": None,
                            "trade_id": None,
                            "evidence": '{"capital_at_risk_usd": 5200, "min_capital_threshold_usd": 5000}',
                        },
                        {
                            "data_quality": "live",
                            "raw_event_id": 2,
                            "trade_id": "dddddddd-cccc-dddd-eeee-ffffffffffff",
                            "evidence": '{"baseline_trades": 3}',
                        },
                    ]
                return []

        conn = Conn()
        since = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
        summary = asyncio.run(get_alert_summary(conn, since=since))

        assert summary["review_queue"]["total"] == 3
        assert len(summary["review_queue"]["alerts"]) == 1
        assert summary["review_queue"]["alerts"][0]["triage_flags"] == ["low_notional"]
        assert summary["review_queue"]["triage_flags"] == {
            "total_flagged": 3,
            "by_flag": [
                {"flag": "degraded_data_quality", "cnt": 1},
                {"flag": "low_notional", "cnt": 1},
                {"flag": "missing_lineage", "cnt": 1},
                {"flag": "near_threshold", "cnt": 1},
                {"flag": "thin_baseline", "cnt": 1},
            ],
        }
        assert any("SELECT a.data_quality" in sql and "a.evidence" in sql for sql in conn.fetch_sqls)
