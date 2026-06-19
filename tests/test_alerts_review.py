from __future__ import annotations

"""Tests for cmd_alerts_review and cmd_alerts_fp_rate commands."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool_mock(fetch_return=None, execute_return=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock(return_value=execute_return or "INSERT 0 1")
    pool.close = AsyncMock()
    return pool


async def _async_create_pool(pool):
    """Coroutine that returns the given pool mock (simulates asyncpg.create_pool)."""
    return pool


# ---------------------------------------------------------------------------
# cmd_alerts_review — success path
# ---------------------------------------------------------------------------

def test_cmd_alerts_review_success(capsys):
    """cmd_alerts_review writes through the shared append-only review helper."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review

    _alert_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    args = argparse.Namespace(
        alert_id=_alert_id,
        label="fp",
        category="stale_baseline",
        notes="price was stale",
        reviewed_by="analyst1",
    )

    pool = _make_pool_mock()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_insert_alert_review(_conn, alert_id, *, label, category, notes, reviewed_by):
        assert _conn is conn
        assert alert_id == _alert_id
        assert label == "fp"
        assert category == "stale_baseline"
        assert notes == "price was stale"
        assert reviewed_by == "analyst1"
        return {
            "review_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "alert_id": _alert_id,
            "label": label,
            "category": category,
            "notes": notes,
            "reviewed_by": reviewed_by,
            "reviewed_at": "2026-06-18T12:00:00+00:00",
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.db.repos.alerts.insert_alert_review", side_effect=_fake_insert_alert_review) as insert_helper, \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 0
    pool.execute.assert_not_awaited()
    assert insert_helper.call_count == 1
    out = capsys.readouterr().out
    assert "[review]" in out
    assert _alert_id in out
    assert "label=fp" in out


# ---------------------------------------------------------------------------
# cmd_alerts_review — FK violation path
# ---------------------------------------------------------------------------

def test_cmd_alerts_review_fk_violation(capsys):
    """cmd_alerts_review prints 'not found' message and returns 1 on FK violation."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review

    _alert_id = "00000000-0000-0000-0000-000000000099"
    args = argparse.Namespace(
        alert_id=_alert_id,
        label="tp",
        category=None,
        notes=None,
        reviewed_by=None,
    )

    pool = _make_pool_mock()
    pool.execute = AsyncMock(side_effect=asyncpg.ForeignKeyViolationError())
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.db.repos.alerts.insert_alert_review", AsyncMock(return_value=None)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 1
    out = capsys.readouterr().out
    assert "not found" in out.lower() or _alert_id in out


# ---------------------------------------------------------------------------
# cmd_alerts_fp_rate — no reviews path
# ---------------------------------------------------------------------------

def test_cmd_alerts_review_dry_run_does_not_insert(capsys):
    """--dry-run validates the alert and prints the planned review without writing."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review

    _alert_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    args = argparse.Namespace(
        alert_id=_alert_id,
        label="noise",
        category="low_notional",
        notes="small trade on thin baseline",
        reviewed_by="analyst1",
        dry_run=True,
    )

    pool = _make_pool_mock()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_get_alert_by_id(_conn, alert_id):
        assert _conn is conn
        assert alert_id == _alert_id
        return {
            "alert_id": _alert_id,
            "rule_key": "volume_spike_v1",
            "severity": "medium",
            "market_title": "Bitcoin price on Jun 18, 2026?",
            "venue_market_id": "KXBTCD-26JUN1817-T63249.99",
            "outcome_key": "no",
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.db.repos.alerts.get_alert_by_id", side_effect=_fake_get_alert_by_id), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 0
    pool.execute.assert_not_awaited()
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()
    assert _alert_id in out
    assert "label=noise" in out
    assert "category=low_notional" in out
    assert "Bitcoin price" in out


def test_cmd_alerts_fp_rate_no_reviews(capsys):
    """cmd_alerts_fp_rate prints 'No reviews' message and returns 0 when table is empty."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_fp_rate

    args = argparse.Namespace(since=None, rule=None)

    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_fp_rate(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "no reviews" in out.lower() or "review" in out.lower()


def test_cmd_alerts_list_market_filter_matches_title_and_identifiers(capsys):
    """--market must bind one substring pattern across useful market identity fields."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=7,
        evidence=False,
        rule="large_trade_absolute_v1",
        venue="polymarket",
        severity="high",
        market="condition-alpha",
        since=None,
        format="json",
    )
    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    pool.fetch.assert_awaited_once()
    sql = pool.fetch.call_args[0][0]
    bound = pool.fetch.call_args[0][1:]
    assert "m.title ILIKE $4" in sql
    assert "m.venue_market_id ILIKE $4" in sql
    assert "a.market_id::text ILIKE $4" in sql
    assert "condition-alpha" not in sql
    assert bound == (
        "large_trade_absolute_v1",
        "polymarket",
        "high",
        "%condition-alpha%",
        7,
    )


def test_cmd_alerts_list_unreviewed_since_empty_reports_filter_miss(capsys):
    """--unreviewed --since empty results should not be reported as an empty DB."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=10,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since="24h",
        format="json",
        unreviewed=True,
        reviewed=False,
        review_label=None,
    )
    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    pool.fetch.assert_awaited_once()
    sql = pool.fetch.call_args[0][0]
    bound = pool.fetch.call_args[0][1:]
    assert "latest_reviews AS" in sql
    assert "LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id" in sql
    assert "a.fired_at >= $1" in sql
    assert "lr.alert_id IS NULL" in sql
    assert "LIMIT $2" in sql
    assert len(bound) == 2
    assert bound[0].tzinfo is not None
    assert bound[1] == 10
    out = capsys.readouterr().out
    assert "No alerts match the selected filters." in out
    assert "No alerts in DB" not in out


def test_cmd_alerts_list_unfiltered_empty_reports_empty_db(capsys):
    """An unfiltered empty alert list should keep the population hint."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=10,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
    )
    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "No alerts in DB. Run 'pmfi replay --persist' to populate." in out


def test_cmd_alerts_list_review_label_filters_latest_review_parameterized(capsys):
    """--review-label must filter by the latest review label with a bind parameter."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=5,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=True,
        review_label="tp",
    )
    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    pool.fetch.assert_awaited_once()
    sql = pool.fetch.call_args[0][0]
    bound = pool.fetch.call_args[0][1:]
    assert "ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC" in sql
    assert "lr.alert_id IS NOT NULL" in sql
    assert "lr.review_label = $1" in sql
    assert "tp" not in sql
    assert bound == ("tp", 5)


def test_cmd_alerts_list_rejects_unreviewed_with_review_label(capsys):
    """--unreviewed and --review-label are conflicting queue states."""
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=5,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=True,
        reviewed=False,
        review_label="fp",
    )

    rc = cmd_alerts_list(args)

    assert rc == 1
    out = capsys.readouterr().out.lower()
    assert "--unreviewed" in out
    assert "--review-label" in out


# ---------------------------------------------------------------------------
# cmd_alerts_fp_rate — with review rows
# ---------------------------------------------------------------------------

def test_cmd_alerts_list_json_adds_evidence_summary_and_triage_flags(capsys):
    """JSON alert lists should expose review-ready evidence metadata without DB writes."""
    import asyncpg
    import json
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=3,
        evidence=True,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
    )
    row = {
        "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "fired_at": "2026-06-18T10:00:00+00:00",
        "rule_key": "volume_spike_v1",
        "rule_version": "alert_rules.v1",
        "severity": "medium",
        "confidence": "medium",
        "score": 0.75,
        "venue_code": "kalshi",
        "outcome_key": "yes",
        "data_quality": "live",
        "market_title": "Bitcoin price on Jun 18, 2026?",
        "outcome_label": "Yes",
        "review_label": None,
        "raw_event_id": 123,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence": (
            '{"this_trade_usd": 760.0, "baseline_median_usd": 150.0, '
            '"spike_multiplier": 5.07, "min_spike_multiplier": 5.0, '
            '"baseline_trades": 20, "degraded_reasons": []}'
        ),
    }
    pool = _make_pool_mock(fetch_return=[row])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    sql = pool.fetch.call_args[0][0]
    assert "a.raw_event_id" in sql
    assert "a.trade_id::text AS trade_id" in sql
    pool.execute.assert_not_awaited()
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["evidence"] == row["evidence"]
    assert payload[0]["evidence_parsed"]["baseline_trades"] == 20
    assert "this_trade_usd=$760" in payload[0]["evidence_summary"]
    assert payload[0]["triage_flags"] == ["low_notional", "thin_baseline", "near_threshold"]


def test_cmd_alerts_list_json_flags_degraded_quality_and_missing_lineage(capsys):
    """Flags should be deterministic metadata, not review labels."""
    import asyncpg
    import json
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=2,
        evidence=True,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
    )
    row = {
        "alert_id": "cccccccc-dddd-eeee-ffff-000000000001",
        "fired_at": "2026-06-18T10:05:00+00:00",
        "rule_key": "market_relative_large_trade_v1",
        "rule_version": "alert_rules.v1",
        "severity": "low",
        "confidence": "low",
        "score": 0.5,
        "venue_code": "polymarket",
        "outcome_key": "no",
        "data_quality": "baseline_pending",
        "market_title": "Some market",
        "outcome_label": "No",
        "review_label": None,
        "raw_event_id": None,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence": {
            "capital_at_risk_usd": "5200",
            "min_capital_threshold_usd": "5000",
            "baseline_status": "baseline_missing",
            "baseline_state": "baseline_missing",
            "degraded_reasons": ["missing_directional_side"],
        },
    }
    pool = _make_pool_mock(fetch_return=[row])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    row_out = payload[0]
    assert row_out["review_label"] is None
    assert "tp" not in row_out["triage_flags"]
    assert "fp" not in row_out["triage_flags"]
    assert "noise" not in row_out["triage_flags"]
    assert row_out["triage_flags"] == [
        "near_threshold",
        "degraded_data_quality",
        "missing_lineage",
    ]


def test_alerts_list_cli_accepts_repeatable_triage_flags():
    """'alerts list --triage-flag ...' parses repeatable deterministic cohorts."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([
        "alerts",
        "list",
        "--triage-flag",
        "low_notional",
        "--triage-flag",
        "thin_baseline",
    ])

    assert args.alerts_cmd == "list"
    assert args.triage_flag == ["low_notional", "thin_baseline"]


def test_alerts_list_cli_rejects_invalid_triage_flag():
    """Argparse choices must reject unknown triage cohorts before DB work."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["alerts", "list", "--triage-flag", "fake_label"])


def test_cmd_alerts_list_json_triage_filter_applies_before_limit_and_omits_internals(capsys):
    """Triage filtering should AND repeated flags before limiting JSON output."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=1,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
        triage_flag=["low_notional", "thin_baseline"],
    )
    rows = [
        {
            "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "fired_at": "2026-06-18T10:00:00+00:00",
            "rule_key": "volume_spike_v1",
            "rule_version": "alert_rules.v1",
            "severity": "medium",
            "confidence": "medium",
            "score": 0.75,
            "venue_code": "kalshi",
            "outcome_key": "yes",
            "data_quality": "live",
            "market_title": "Low notional only",
            "outcome_label": "Yes",
            "review_label": None,
            "raw_event_id": 123,
            "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "evidence": {"this_trade_usd": 100.0, "baseline_trades": 30},
        },
        {
            "alert_id": "cccccccc-dddd-eeee-ffff-000000000001",
            "fired_at": "2026-06-18T10:05:00+00:00",
            "rule_key": "volume_spike_v1",
            "rule_version": "alert_rules.v1",
            "severity": "medium",
            "confidence": "medium",
            "score": 0.7,
            "venue_code": "kalshi",
            "outcome_key": "yes",
            "data_quality": "live",
            "market_title": "Low notional and thin baseline",
            "outcome_label": "Yes",
            "review_label": None,
            "raw_event_id": 456,
            "trade_id": "dddddddd-eeee-ffff-0000-111111111111",
            "evidence": {"this_trade_usd": 250.0, "baseline_trades": 5},
        },
    ]
    pool = _make_pool_mock(fetch_return=rows)

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    pool.execute.assert_not_awaited()
    sql = pool.fetch.call_args[0][0]
    bound = pool.fetch.call_args[0][1:]
    assert "a.evidence" in sql
    assert "a.raw_event_id" in sql
    assert "a.trade_id::text AS trade_id" in sql
    assert " LIMIT $" not in sql
    assert bound == ()
    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    row_out = payload[0]
    assert row_out["alert_id"] == "cccccccc-dddd-eeee-ffff-000000000001"
    assert row_out["triage_flags"] == ["low_notional", "thin_baseline"]
    assert "evidence" not in row_out
    assert "evidence_parsed" not in row_out
    assert "evidence_summary" not in row_out
    assert "raw_event_id" not in row_out
    assert "trade_id" not in row_out


def test_cmd_alerts_list_json_triage_filter_with_evidence_preserves_evidence_fields(capsys):
    """--triage-flag with --evidence should preserve current evidence JSON behavior."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=5,
        evidence=True,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
        triage_flag=["low_notional"],
    )
    row = {
        "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "fired_at": "2026-06-18T10:00:00+00:00",
        "rule_key": "volume_spike_v1",
        "rule_version": "alert_rules.v1",
        "severity": "medium",
        "confidence": "medium",
        "score": 0.75,
        "venue_code": "kalshi",
        "outcome_key": "yes",
        "data_quality": "live",
        "market_title": "Bitcoin price on Jun 18, 2026?",
        "outcome_label": "Yes",
        "review_label": None,
        "raw_event_id": 123,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence": (
            '{"this_trade_usd": 760.0, "baseline_median_usd": 150.0, '
            '"spike_multiplier": 7.0, "min_spike_multiplier": 5.0, '
            '"baseline_trades": 30}'
        ),
    }
    pool = _make_pool_mock(fetch_return=[row])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    row_out = payload[0]
    assert row_out["evidence"] == row["evidence"]
    assert row_out["raw_event_id"] == 123
    assert row_out["trade_id"] == "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    assert row_out["evidence_parsed"]["this_trade_usd"] == 760.0
    assert "this_trade_usd=$760" in row_out["evidence_summary"]
    assert row_out["triage_flags"] == ["low_notional"]


def test_cmd_alerts_list_triage_filter_no_match_exits_zero(capsys):
    """No triage cohort matches should be reported as a filter miss, not an empty DB."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=5,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="json",
        unreviewed=False,
        reviewed=False,
        review_label=None,
        triage_flag=["thin_baseline"],
    )
    pool = _make_pool_mock(fetch_return=[{
        "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "fired_at": "2026-06-18T10:00:00+00:00",
        "rule_key": "volume_spike_v1",
        "rule_version": "alert_rules.v1",
        "severity": "medium",
        "confidence": "medium",
        "score": 0.75,
        "venue_code": "kalshi",
        "outcome_key": "yes",
        "data_quality": "live",
        "market_title": "Not thin",
        "outcome_label": "Yes",
        "review_label": None,
        "raw_event_id": 123,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence": {"this_trade_usd": 6000.0, "baseline_trades": 30},
    }])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "No alerts match" in out
    assert "thin_baseline" in out


def test_cmd_alerts_list_table_triage_filter_shows_flags(capsys):
    """Table output should show the compact matching flags without raw evidence."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_list

    args = argparse.Namespace(
        limit=5,
        evidence=False,
        rule=None,
        venue=None,
        severity=None,
        market=None,
        since=None,
        format="table",
        unreviewed=False,
        reviewed=False,
        review_label=None,
        triage_flag=["low_notional"],
    )
    row = {
        "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "fired_at": "2026-06-18T10:00:00+00:00",
        "rule_key": "volume_spike_v1",
        "rule_version": "alert_rules.v1",
        "severity": "medium",
        "confidence": "medium",
        "score": 0.75,
        "venue_code": "kalshi",
        "outcome_key": "yes",
        "data_quality": "live",
        "market_title": "Low notional",
        "outcome_label": "Yes",
        "review_label": None,
        "raw_event_id": 123,
        "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "evidence": {"this_trade_usd": 100.0, "baseline_trades": 30},
    }
    pool = _make_pool_mock(fetch_return=[row])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_list(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Flags" in out
    assert "low_notional" in out
    assert "this_trade_usd" not in out


def test_cmd_alerts_fp_rate_with_reviews(capsys):
    """cmd_alerts_fp_rate returns 0 and output contains 'FP' and the FP count."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_fp_rate

    args = argparse.Namespace(since=None, rule=None)

    # Simulate rows: 3 FP + 7 TP for large_trade_absolute_v1
    rows = [
        {"label": "fp", "rule_key": "large_trade_absolute_v1", "cnt": 3},
        {"label": "tp", "rule_key": "large_trade_absolute_v1", "cnt": 7},
    ]
    pool = _make_pool_mock(fetch_return=rows)

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    # Force the rich import to fail so the plain print fallback runs and
    # output is captured by capsys.
    rich_backup = sys.modules.pop("rich.console", None)
    rich_table_backup = sys.modules.pop("rich.table", None)
    sys.modules["rich.console"] = None  # type: ignore[assignment]
    sys.modules["rich.table"] = None  # type: ignore[assignment]
    try:
        with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
             patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
             patch("pmfi.config.load_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
            rc = cmd_alerts_fp_rate(args)
    finally:
        # Restore rich modules
        if rich_backup is not None:
            sys.modules["rich.console"] = rich_backup
        else:
            sys.modules.pop("rich.console", None)
        if rich_table_backup is not None:
            sys.modules["rich.table"] = rich_table_backup
        else:
            sys.modules.pop("rich.table", None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "FP" in out or "fp" in out
    # The FP count (3) must appear somewhere in the output.
    assert "3" in out


def test_alerts_review_packet_cli_args_parse(tmp_path):
    """'alerts review-packet' exposes reviewed-cohort export filters."""
    from pmfi.cli import _build_parser

    out_path = tmp_path / "packet.json"
    parser = _build_parser()
    args = parser.parse_args([
        "alerts",
        "review-packet",
        "--since",
        "24h",
        "--rule",
        "volume_spike_v1",
        "--review-label",
        "noise",
        "--category",
        "low_notional",
        "--limit",
        "25",
        "--output",
        str(out_path),
    ])

    assert args.alerts_cmd == "review-packet"
    assert args.since == "24h"
    assert args.rule == "volume_spike_v1"
    assert args.review_label == "noise"
    assert args.category == "low_notional"
    assert args.limit == 25
    assert args.output == str(out_path)


def test_alerts_outcome_audit_cli_args_parse():
    """'alerts outcome-audit' exposes exact-window directional outcome checks."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([
        "alerts",
        "outcome-audit",
        "--since",
        "2026-06-18T16:23:02+00:00",
        "--until",
        "2026-06-18T16:33:04+00:00",
        "--rule",
        "directional_cluster_v1",
        "--limit",
        "20",
        "--format",
        "json",
        "--strict",
    ])

    assert args.alerts_cmd == "outcome-audit"
    assert args.since == "2026-06-18T16:23:02+00:00"
    assert args.until == "2026-06-18T16:33:04+00:00"
    assert args.rule == ["directional_cluster_v1"]
    assert args.limit == 20
    assert args.format == "json"
    assert args.strict is True


def test_cmd_alerts_outcome_audit_rejects_naive_until_before_db(capsys):
    """Exact audit windows must be timezone-aware and fail before DB access."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_outcome_audit

    args = argparse.Namespace(
        since="24h",
        until="2026-06-18T16:33:04",
        rule=None,
        limit=50,
        format="json",
        strict=False,
    )

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_outcome_audit(args)

    assert rc == 1
    assert "timestamp must include timezone" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


def test_cmd_alerts_outcome_audit_json_strict_requires_rows(capsys):
    """Strict audit mode should make no-row samples explicit without DB writes."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_outcome_audit

    args = argparse.Namespace(
        since="24h",
        until=None,
        rule=None,
        limit=50,
        format="json",
        strict=True,
    )
    pool = _make_pool_mock()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_audit(_conn, *, since, until, rules, limit):
        assert _conn is conn
        assert since.tzinfo is not None
        assert until is None
        assert rules == ["directional_cluster_v1", "momentum_v1"]
        assert limit == 50
        return {
            "generated_at": "2026-06-18T16:40:00+00:00",
            "filters": {
                "since": since.isoformat(),
                "until": None,
                "rules": rules,
                "limit": limit,
            },
            "totals": {
                "checked": 0,
                "matched": 0,
                "mismatches": 0,
                "missing_dominant_side": 0,
            },
            "rows": [],
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_directional_outcome_audit", side_effect=_fake_audit), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_outcome_audit(args)

    assert rc == 1
    pool.execute.assert_not_awaited()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["totals"]["checked"] == 0


def test_cmd_alerts_outcome_audit_json_marks_mismatch_not_ok_without_strict(capsys):
    """Non-strict audit should still mark payload ok=false when mismatches exist."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_outcome_audit

    args = argparse.Namespace(
        since="24h",
        until=None,
        rule=["directional_cluster_v1"],
        limit=50,
        format="json",
        strict=False,
    )
    pool = _make_pool_mock()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn

    async def _fake_audit(_conn, *, since, until, rules, limit):
        return {
            "generated_at": "2026-06-18T16:40:00+00:00",
            "filters": {
                "since": since.isoformat(),
                "until": None,
                "rules": rules,
                "limit": limit,
            },
            "totals": {
                "checked": 1,
                "matched": 0,
                "mismatches": 1,
                "missing_dominant_side": 0,
            },
            "rows": [{
                "short_id": "504e373a",
                "status": "mismatch",
                "stored_outcome_key": "yes",
                "dominant_side": "no",
            }],
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_directional_outcome_audit", side_effect=_fake_audit), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_outcome_audit(args)

    assert rc == 0
    pool.execute.assert_not_awaited()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["totals"]["mismatches"] == 1


def test_get_directional_outcome_audit_classifies_rows_without_writes():
    """Repository audit compares stored outcome_key to dominant_side evidence."""
    import asyncio
    from pmfi.db.repos.alerts import get_directional_outcome_audit

    fired = datetime(2026, 6, 18, 16, 30, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.fetch_calls = []

        async def fetch(self, sql, *args):
            self.fetch_calls.append((sql, args))
            return [
                {
                    "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "fired_at": fired,
                    "rule_key": "directional_cluster_v1",
                    "venue_code": "polymarket",
                    "market_id": "market-1",
                    "outcome_key": "no",
                    "title": "Market one",
                    "market_title": "Market one",
                    "evidence": {"dominant_side": "no", "outcome_key": "yes", "directional_side": "yes"},
                },
                {
                    "alert_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                    "fired_at": fired,
                    "rule_key": "momentum_v1",
                    "venue_code": "kalshi",
                    "market_id": "market-2",
                    "outcome_key": "yes",
                    "title": "Market two",
                    "market_title": "Market two",
                    "evidence": '{"dominant_side": "no", "outcome_key": "yes"}',
                },
                {
                    "alert_id": "cccccccc-dddd-eeee-ffff-000000000001",
                    "fired_at": fired,
                    "rule_key": "directional_cluster_v1",
                    "venue_code": "kalshi",
                    "market_id": "market-3",
                    "outcome_key": "yes",
                    "title": "Market three",
                    "market_title": "Market three",
                    "evidence": {},
                },
            ]

        async def execute(self, sql, *args):
            raise AssertionError("get_directional_outcome_audit must be read-only")

    since = datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc)
    conn = Conn()
    audit = asyncio.run(get_directional_outcome_audit(
        conn,
        since=since,
        until=until,
        rules=["directional_cluster_v1", "momentum_v1"],
        limit=25,
    ))

    assert audit["filters"]["since"] == since.isoformat()
    assert audit["filters"]["until"] == until.isoformat()
    assert audit["totals"] == {
        "checked": 3,
        "matched": 1,
        "mismatches": 1,
        "missing_dominant_side": 1,
    }
    assert [row["status"] for row in audit["rows"]] == [
        "match",
        "mismatch",
        "missing_dominant_side",
    ]
    assert audit["rows"][0]["stored_outcome_key"] == "no"
    assert audit["rows"][0]["dominant_side"] == "no"
    sql, args = conn.fetch_calls[0]
    assert "a.rule_key = ANY($1::text[])" in sql
    assert "a.fired_at >= $2" in sql
    assert "a.fired_at <= $3" in sql
    assert args == (["directional_cluster_v1", "momentum_v1"], since, until, 25)


def test_cmd_alerts_review_packet_invalid_since_fails_before_db(capsys):
    """Invalid windows must fail closed without config load or DB access."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    args = argparse.Namespace(
        since="not-a-window",
        rule=None,
        review_label=None,
        category=None,
        limit=50,
        output=None,
        format="json",
    )

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_review_packet(args)

    assert rc == 1
    assert "Invalid --since value" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


def test_default_review_packet_path_uses_packet_root(tmp_path):
    """Default review-packet output is rooted under the ignored packet directory."""
    from pmfi.commands.alerts import _default_review_packet_path

    packet_root = tmp_path / "reports" / "review-packets"
    with patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root):
        path = _default_review_packet_path()

    assert path.parent == packet_root
    assert path.name.startswith("review-packet-")
    assert path.suffix == ".json"


def test_cmd_alerts_review_packet_rejects_unsafe_output_before_db(tmp_path, capsys):
    """Custom packet outputs must remain under reports/review-packets."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    args = argparse.Namespace(
        since="24h",
        rule=None,
        review_label=None,
        category=None,
        limit=50,
        output=str(tmp_path / "unsafe.json"),
        format="json",
    )
    packet_root = tmp_path / "reports" / "review-packets"

    with patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_review_packet(args)

    assert rc == 1
    assert "--output must be inside" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


def test_cmd_alerts_review_packet_rejects_existing_output_before_db(tmp_path, capsys):
    """Review-packet export should not overwrite an existing local artifact."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    packet_root = tmp_path / "reports" / "review-packets"
    packet_root.mkdir(parents=True)
    out_path = packet_root / "existing.json"
    out_path.write_text("already here", encoding="utf-8")
    args = argparse.Namespace(
        since="24h",
        rule=None,
        review_label=None,
        category=None,
        limit=50,
        output=str(out_path),
        format="json",
    )

    with patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_review_packet(args)

    assert rc == 1
    assert "output already exists" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


def test_cmd_alerts_review_packet_writes_json_artifact(tmp_path, capsys):
    """Review-packet export writes a local JSON artifact and performs no writes."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    packet_root = tmp_path / "reports" / "review-packets"
    out_path = packet_root / "review-packet.json"
    args = argparse.Namespace(
        since="24h",
        rule="volume_spike_v1",
        review_label="noise",
        category="low_notional",
        limit=10,
        output=str(out_path),
        format="json",
    )
    pool = _make_pool_mock()
    conn = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    packet = {
        "export_metadata": {
            "schema_version": "review_packet.v1",
            "local_only": True,
            "generated_at": "2026-06-18T12:00:00+00:00",
            "filters": {
                "since": "2026-06-17T12:00:00+00:00",
                "rule": "volume_spike_v1",
                "review_label": "noise",
                "category": "low_notional",
                "limit": 10,
            },
        },
        "reviewed_cohort_totals": {"alerts": 1},
        "alerts": [{"alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}],
    }

    async def _fake_packet(_conn, *, since, rule, review_label, category, limit):
        assert _conn is conn
        assert since.tzinfo is not None
        assert rule == "volume_spike_v1"
        assert review_label == "noise"
        assert category == "low_notional"
        assert limit == 10
        return packet

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch("pmfi.commands.alerts._review_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.db.repos.alerts.get_review_packet", side_effect=_fake_packet), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review_packet(args)

    assert rc == 0
    pool.execute.assert_not_awaited()
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["export_metadata"]["schema_version"] == "review_packet.v1"
    assert saved["reviewed_cohort_totals"]["alerts"] == 1
    out = capsys.readouterr().out
    assert "[review-packet]" in out
    assert str(out_path) in out


def _calibration_trade():
    from decimal import Decimal
    from pmfi.domain import NormalizedTrade

    return NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="KXBTCD-26JUN1817-T63749.99",
        outcome_key="yes",
        price=Decimal("0.50"),
        contracts=Decimal("1500"),
        capital_at_risk_usd=Decimal("750"),
        payout_notional_usd=Decimal("1500"),
        venue_trade_id="trade-1",
        exchange_ts=datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc),
    )


def _calibration_spike_decision():
    from decimal import Decimal
    from pmfi.domain import AlertDecision

    return AlertDecision(
        emit_alert=True,
        rule_id="volume_spike_v1",
        rule_version="alert_rules.v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.75"),
        reason_codes=("volume_spike_detected",),
        data_quality="live",
        evidence={
            "this_trade_usd": 750.0,
            "baseline_median_usd": 100.0,
            "spike_multiplier": 7.5,
            "min_spike_multiplier": 5.0,
            "min_trade_usd": 500.0,
            "baseline_trades": 20,
        },
    )


def test_volume_spike_calibration_summary_counts_removed_low_notional_thin_alert():
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, summarize_volume_spike_calibration
    from pmfi.replay import ReplayResult

    current = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision()],
        )
    ]
    candidate = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[],
        )
    ]

    summary = summarize_volume_spike_calibration(
        current,
        candidate,
        candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
    )

    assert summary["validate_only"] is True
    assert summary["current"]["volume_spike_alerts"] == 1
    assert summary["candidate_replay"]["volume_spike_alerts"] == 0
    assert summary["comparison"]["volume_spike_delta"] == -1
    assert summary["comparison"]["removed_low_notional_thin_baseline"] == 1


def test_cmd_alerts_volume_spike_calibration_rejects_missing_candidate_before_db(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_calibration

    args = argparse.Namespace(
        calibration_from="24h",
        calibration_to=None,
        limit=0,
        calibration_venue=None,
        calibration_market=None,
        min_spike_multiplier=None,
        min_trade_usd=None,
        min_baseline_trades=None,
        history_max=None,
        cold_start=False,
        format="json",
    )

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool:
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 1
    create_pool.assert_not_called()
    assert "provide at least one candidate" in capsys.readouterr().out


def test_cmd_alerts_volume_spike_calibration_runs_read_only_replay(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_calibration
    from pmfi.replay import ReplayResult

    args = argparse.Namespace(
        calibration_from="24h",
        calibration_to="1h",
        limit=0,
        calibration_venue="kalshi",
        calibration_market="KXBTCD-26JUN1817-T63749.99",
        min_spike_multiplier=None,
        min_trade_usd=1000,
        min_baseline_trades=None,
        history_max=None,
        cold_start=True,
        format="json",
    )
    pool = _make_pool_mock()
    current_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision()],
        )
    ]
    candidate_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[],
        )
    ]
    replay_calls = []

    async def _fake_replay(pool_arg, **kwargs):
        assert pool_arg is pool
        replay_calls.append(kwargs)
        assert kwargs["persist"] is False
        assert kwargs["print_summary"] is False
        assert kwargs["venue"] == "kalshi"
        assert kwargs["market"] == "KXBTCD-26JUN1817-T63749.99"
        assert kwargs["seed"] is False
        return candidate_results if kwargs.get("rules_config") else current_results

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.replay.replay_from_db", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 0
    assert len(replay_calls) == 2
    assert replay_calls[0].get("rules_config") is None
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 1000.0
    pool.execute.assert_not_awaited()
    saved = json.loads(capsys.readouterr().out)
    assert saved["schema_version"] == "volume_spike_calibration.v1"
    assert saved["local_only"] is True
    assert saved["validate_only"] is True
    assert saved["comparison"]["removed_volume_spike_alerts"] == 1


def test_get_review_packet_returns_reviewed_cohort_context_without_writes():
    """Repository packet helper should be read-only and include audit context."""
    import asyncio
    from pmfi.db.repos.alerts import get_review_packet

    class Conn:
        def __init__(self):
            self.fetch_sqls: list[str] = []
            self.fetchval_sqls: list[str] = []

        async def fetch(self, sql, *args):
            self.fetch_sqls.append(sql)
            if "WITH latest_reviews AS" in sql and "SELECT a.alert_id::text AS alert_id" in sql:
                return [{
                    "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "fired_at": datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
                    "created_at": datetime(2026, 6, 18, 12, 1, tzinfo=timezone.utc),
                    "rule_key": "volume_spike_v1",
                    "rule_version": "alert_rules.v1",
                    "severity": "medium",
                    "confidence": "medium",
                    "score": 0.8,
                    "venue_code": "kalshi",
                    "outcome_key": "yes",
                    "data_quality": "live",
                    "title": "Bitcoin price",
                    "market_title": "Bitcoin price",
                    "venue_market_id": "KXBTCD-26JUN1817",
                    "outcome_label": "Yes",
                    "raw_event_id": 123,
                    "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                    "evidence": (
                        '{"this_trade_usd": 760, "baseline_median_usd": 150, '
                        '"spike_multiplier": 5.07, "min_spike_multiplier": 5.0, '
                        '"baseline_trades": 20}'
                    ),
                    "review_id": "cccccccc-dddd-eeee-ffff-000000000001",
                    "review_label": "noise",
                    "review_category": "low_notional",
                    "review_notes": "small trade on thin baseline",
                    "reviewed_by": "operator",
                    "reviewed_at": datetime(2026, 6, 18, 12, 5, tzinfo=timezone.utc),
                }]
            if "GROUP BY lr.label" in sql:
                return [{"label": "noise", "cnt": 1}]
            if "GROUP BY COALESCE(lr.false_positive_category" in sql:
                return [{"category": "low_notional", "cnt": 1}]
            if "GROUP BY a.rule_key" in sql:
                return [{"rule_key": "volume_spike_v1", "cnt": 1}]
            if "GROUP BY a.venue_code" in sql:
                return [{"venue_code": "kalshi", "cnt": 1}]
            return []

        async def fetchval(self, sql, *args):
            self.fetchval_sqls.append(sql)
            if "JOIN latest_reviews lr" in sql:
                return 1
            if "COUNT(*) FROM alerts" in sql:
                return 3
            if "COUNT(*) FROM raw_events" in sql:
                return 20
            if "COUNT(*) FROM normalized_trades" in sql:
                return 12
            if "dead_letters" in sql:
                return 0
            if "data_quality_incidents" in sql:
                return 0
            return 0

        async def execute(self, sql, *args):
            raise AssertionError("get_review_packet must be read-only")

    since = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
    conn = Conn()
    packet = asyncio.run(get_review_packet(
        conn,
        since=since,
        rule="volume_spike_v1",
        review_label="noise",
        category="low_notional",
        limit=10,
    ))

    assert packet["export_metadata"]["schema_version"] == "review_packet.v1"
    assert packet["export_metadata"]["local_only"] is True
    assert packet["export_metadata"]["filters"]["review_label"] == "noise"
    assert packet["reviewed_cohort_totals"]["alerts"] == 1
    assert packet["reviewed_cohort_totals"]["by_label"] == [{"label": "noise", "cnt": 1}]
    assert packet["reviewed_cohort_totals"]["triage_flags"] == {
        "total_flagged": 1,
        "by_flag": [
            {"flag": "low_notional", "cnt": 1},
            {"flag": "near_threshold", "cnt": 1},
            {"flag": "thin_baseline", "cnt": 1},
        ],
    }
    alert = packet["alerts"][0]
    assert alert["latest_review"] == {
        "review_id": "cccccccc-dddd-eeee-ffff-000000000001",
        "label": "noise",
        "category": "low_notional",
        "notes": "small trade on thin baseline",
        "reviewed_by": "operator",
        "reviewed_at": "2026-06-18T12:05:00+00:00",
    }
    assert "this_trade_usd=$760" in alert["evidence_summary"]
    assert alert["triage_flags"] == ["low_notional", "thin_baseline", "near_threshold"]
    assert packet["report_context"]["alert_count"] == 3
    assert packet["report_context"]["raw_events"] == 20
    assert any("DISTINCT ON (ar.alert_id)" in sql for sql in conn.fetch_sqls)


# ---------------------------------------------------------------------------
# CLI arg-parse tests (no DB, no asyncio)
# ---------------------------------------------------------------------------

def test_alerts_review_cli_args_parse():
    """'alerts review <uuid> --label fp' parses to alert_id and label='fp'."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    _uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    args = parser.parse_args(["alerts", "review", _uuid, "--label", "fp"])
    assert args.alerts_cmd == "review"
    assert args.alert_id == _uuid
    assert args.label == "fp"


def test_alerts_review_cli_accepts_dry_run():
    """'alerts review ... --dry-run' parses without changing review labels."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([
        "alerts",
        "review",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "--label",
        "noise",
        "--dry-run",
    ])
    assert args.alerts_cmd == "review"
    assert args.label == "noise"
    assert args.dry_run is True


def test_alerts_fp_rate_cli_args_parse():
    """'alerts fp-rate --since 7d --rule large_trade_absolute_v1' parses correctly."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([
        "alerts", "fp-rate",
        "--since", "7d",
        "--rule", "large_trade_absolute_v1",
    ])
    assert args.alerts_cmd == "fp-rate"
    assert args.since == "7d"
    assert args.rule == "large_trade_absolute_v1"
