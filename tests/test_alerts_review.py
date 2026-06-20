"""Tests for cmd_alerts_review and cmd_alerts_fp_rate commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
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


def test_cmd_alerts_review_rejects_ai_agent_reviewer(capsys):
    """Agent-attribution reviewed_by values are rejected before DB access."""
    from pmfi.commands.alerts import cmd_alerts_review

    args = argparse.Namespace(
        alert_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        label="tp",
        category=None,
        notes=None,
        reviewed_by="co" + "dex-tier1",
        dry_run=False,
    )

    rc = cmd_alerts_review(args)

    assert rc == 1
    out = capsys.readouterr().out
    assert "Invalid --reviewed-by" in out
    assert "human/local operator" in out


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


def test_cmd_alerts_fp_rate_uses_latest_review_authority(capsys):
    """fp-rate must not count stale append-only review rows for the same alert."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_fp_rate

    args = argparse.Namespace(since="2026-06-18T12:00:00+00:00", rule="volume_spike_v1")
    rows = [{"label": "noise", "rule_key": "volume_spike_v1", "cnt": 2}]
    pool = _make_pool_mock(fetch_return=rows)

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_fp_rate(args)

    assert rc == 0
    sql = pool.fetch.await_args.args[0]
    params = pool.fetch.await_args.args[1:]
    assert "WITH latest_reviews AS" in sql
    assert "DISTINCT ON (ar.alert_id)" in sql
    assert "ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC" in sql
    assert "FROM latest_reviews lr" in sql
    assert "a.fired_at >= $1" in sql
    assert "a.rule_key = $2" in sql
    assert "GROUP BY lr.label, a.rule_key" in sql
    assert "ar.reviewed_at >= $1" not in sql
    assert "lr.reviewed_at >= $1" not in sql
    assert params[0].isoformat() == "2026-06-18T12:00:00+00:00"
    assert params[1] == "volume_spike_v1"


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
        "--review-state",
        "reviewed",
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
    assert args.review_state == "reviewed"
    assert args.review_label == "noise"
    assert args.category == "low_notional"
    assert args.limit == 25
    assert args.output == str(out_path)


def test_alerts_review_packet_unreviewed_cli_args_parse(tmp_path):
    """'alerts review-packet' can export the unreviewed queue."""
    from pmfi.cli import _build_parser

    out_path = tmp_path / "packet.json"
    parser = _build_parser()
    args = parser.parse_args([
        "alerts",
        "review-packet",
        "--since",
        "7d",
        "--rule",
        "volume_spike_v1",
        "--review-state",
        "unreviewed",
        "--limit",
        "25",
        "--output",
        str(out_path),
    ])

    assert args.alerts_cmd == "review-packet"
    assert args.since == "7d"
    assert args.rule == "volume_spike_v1"
    assert args.review_state == "unreviewed"
    assert args.review_label is None
    assert args.category is None
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
    assert re.fullmatch(r"review-packet-\d{8}-\d{12}Z\.json", path.name)
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


def test_cmd_alerts_review_packet_rejects_unreviewed_with_review_filters(capsys):
    """Unreviewed packet export cannot be combined with latest-review filters."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review_packet

    args = argparse.Namespace(
        since="24h",
        rule="volume_spike_v1",
        review_state="unreviewed",
        review_label="noise",
        category=None,
        limit=10,
        output=None,
        format="json",
    )

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_review_packet(args)

    assert rc == 1
    assert "cannot be combined" in capsys.readouterr().out
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
        review_state="reviewed",
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

    async def _fake_packet(_conn, *, since, rule, review_state, review_label, category, limit):
        assert _conn is conn
        assert since.tzinfo is not None
        assert rule == "volume_spike_v1"
        assert review_state == "reviewed"
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


def test_default_volume_spike_calibration_packet_path_uses_packet_root(tmp_path):
    """Default calibration packet output is rooted under the ignored packet directory."""
    from pmfi.commands.alerts import _default_volume_spike_calibration_packet_path

    packet_root = tmp_path / "reports" / "calibration-packets"
    with patch("pmfi.commands.alerts._calibration_packet_output_root", return_value=packet_root):
        path = _default_volume_spike_calibration_packet_path()

    assert path.parent == packet_root
    assert path.name.startswith("volume-spike-calibration-")
    assert path.suffix == ".json"


def test_cmd_alerts_volume_spike_calibration_rejects_unsafe_packet_output_before_db(
    tmp_path,
    capsys,
):
    """Calibration packet output must remain under reports/calibration-packets."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_calibration

    args = argparse.Namespace(
        calibration_from="24h",
        calibration_to=None,
        limit=0,
        calibration_venue=None,
        calibration_market=None,
        min_spike_multiplier=None,
        min_trade_usd=1000,
        min_baseline_trades=None,
        low_notional_min_baseline_trades=None,
        low_notional_threshold_usd=None,
        history_max=None,
        cold_start=False,
        export_packet=True,
        packet_output=str(tmp_path / "unsafe.json"),
        packet_limit=0,
        format="json",
    )
    packet_root = tmp_path / "reports" / "calibration-packets"

    with patch("pmfi.commands.alerts._calibration_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 1
    assert "--packet-output must be inside" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


def test_cmd_alerts_volume_spike_calibration_rejects_existing_packet_output_before_db(
    tmp_path,
    capsys,
):
    """Calibration packet export should not overwrite an existing local artifact."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_calibration

    packet_root = tmp_path / "reports" / "calibration-packets"
    packet_root.mkdir(parents=True)
    out_path = packet_root / "existing.json"
    out_path.write_text("already here", encoding="utf-8")
    args = argparse.Namespace(
        calibration_from="24h",
        calibration_to=None,
        limit=0,
        calibration_venue=None,
        calibration_market=None,
        min_spike_multiplier=None,
        min_trade_usd=1000,
        min_baseline_trades=None,
        low_notional_min_baseline_trades=None,
        low_notional_threshold_usd=None,
        history_max=None,
        cold_start=False,
        export_packet=True,
        packet_output=str(out_path),
        packet_limit=0,
        format="json",
    )

    with patch("pmfi.commands.alerts._calibration_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.config.load_config") as load_config:
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 1
    assert "packet output already exists" in capsys.readouterr().out
    load_config.assert_not_called()
    create_pool.assert_not_called()


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


def _calibration_spike_decision(
    *,
    this_trade_usd: float | None = 750.0,
    min_trade_usd: float = 500.0,
    spike_multiplier: float = 7.5,
):
    from decimal import Decimal
    from pmfi.domain import AlertDecision

    evidence = {
        "baseline_median_usd": 100.0,
        "spike_multiplier": spike_multiplier,
        "min_spike_multiplier": 5.0,
        "min_trade_usd": min_trade_usd,
        "baseline_trades": 20,
    }
    if this_trade_usd is not None:
        evidence["this_trade_usd"] = this_trade_usd

    return AlertDecision(
        emit_alert=True,
        rule_id="volume_spike_v1",
        rule_version="alert_rules.v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.75"),
        reason_codes=("volume_spike_detected",),
        data_quality="live",
        evidence=evidence,
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
    assert summary["current"]["volume_spike_trade_usd_buckets"] == {
        "unknown": 0,
        "lt_500": 0,
        "500_to_799": 1,
        "800_to_999": 0,
        "gte_1000": 0,
    }
    assert summary["comparison"]["removed_trade_usd_buckets"] == {
        "unknown": 0,
        "lt_500": 0,
        "500_to_799": 1,
        "800_to_999": 0,
        "gte_1000": 0,
    }


def test_volume_spike_calibration_summary_counts_review_matches_by_raw_event_id():
    from dataclasses import replace
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, summarize_volume_spike_calibration
    from pmfi.replay import ReplayResult

    reviewed_removed_trade = replace(_calibration_trade(), venue_trade_id="trade-reviewed-removed")
    unmatched_removed_trade = replace(_calibration_trade(), venue_trade_id="trade-unmatched-removed")
    reviewed_added_trade = replace(_calibration_trade(), venue_trade_id="trade-reviewed-added")
    current = [
        ReplayResult(
            fixture_path="db:reviewed-removed",
            trade=reviewed_removed_trade,
            alerts=[_calibration_spike_decision()],
            raw_event_id=101,
        ),
        ReplayResult(
            fixture_path="db:unmatched-removed",
            trade=unmatched_removed_trade,
            alerts=[_calibration_spike_decision()],
            raw_event_id=102,
        ),
    ]
    candidate = [
        ReplayResult(
            fixture_path="db:reviewed-added",
            trade=reviewed_added_trade,
            alerts=[_calibration_spike_decision(this_trade_usd=1200.0)],
            raw_event_id=201,
        )
    ]
    review_index = {
        101: {"review_label": "noise", "review_category": "low_notional_thin_baseline"},
        "201": {"label": "tp", "false_positive_category": "legit_spike"},
    }

    summary = summarize_volume_spike_calibration(
        current,
        candidate,
        candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
        review_index_by_raw_event_id=review_index,
    )

    comparison = summary["comparison"]
    assert comparison["review_data_provided"] is True
    assert comparison["removed_review_matches"] == 1
    assert comparison["removed_review_unmatched"] == 1
    assert comparison["removed_review_labels"] == {"noise": 1}
    assert comparison["removed_review_categories"] == {"low_notional_thin_baseline": 1}
    assert comparison["added_review_matches"] == 1
    assert comparison["added_review_unmatched"] == 0
    assert comparison["added_review_labels"] == {"tp": 1}
    assert comparison["added_review_categories"] == {"legit_spike": 1}


def test_volume_spike_calibration_summary_includes_delta_shape_profiles():
    from dataclasses import replace
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, summarize_volume_spike_calibration
    from pmfi.replay import ReplayResult

    removed_750 = replace(_calibration_trade(), venue_trade_id="removed-750")
    removed_870 = replace(
        _calibration_trade(),
        venue_trade_id="removed-870",
        capital_at_risk_usd=Decimal("870"),
    )
    removed_1250 = replace(
        _calibration_trade(),
        venue_trade_id="removed-1250",
        capital_at_risk_usd=Decimal("1250"),
    )
    added_400 = replace(
        _calibration_trade(),
        venue_trade_id="added-400",
        capital_at_risk_usd=Decimal("400"),
    )
    current = [
        ReplayResult(
            fixture_path="db:removed-750",
            trade=removed_750,
            alerts=[_calibration_spike_decision(spike_multiplier=5.5)],
            raw_event_id=601,
        ),
        ReplayResult(
            fixture_path="db:removed-870",
            trade=removed_870,
            alerts=[_calibration_spike_decision(this_trade_usd=870.0, spike_multiplier=12.0)],
            raw_event_id=602,
        ),
        ReplayResult(
            fixture_path="db:removed-1250",
            trade=removed_1250,
            alerts=[_calibration_spike_decision(this_trade_usd=1250.0, spike_multiplier=30.0)],
            raw_event_id=603,
        ),
    ]
    candidate = [
        ReplayResult(
            fixture_path="db:added-400",
            trade=added_400,
            alerts=[_calibration_spike_decision(this_trade_usd=400.0, spike_multiplier=4.0)],
            raw_event_id=701,
        ),
    ]

    summary = summarize_volume_spike_calibration(
        current,
        candidate,
        candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
    )

    removed_profile = summary["comparison"]["removed_shape_profile"]
    assert removed_profile["total"] == 3
    assert removed_profile["trade_usd_buckets"] == {
        "unknown": 0,
        "lt_500": 0,
        "500_to_799": 1,
        "800_to_999": 1,
        "gte_1000": 1,
    }
    assert removed_profile["spike_multiplier_buckets"] == {
        "unknown": 0,
        "lt_5x": 0,
        "5_to_9x": 1,
        "10_to_24x": 1,
        "gte_25x": 1,
    }
    assert removed_profile["triage_flag_counts"] == {
        "low_notional": 3,
        "near_threshold": 1,
        "thin_baseline": 3,
    }
    assert removed_profile["near_threshold_count"] == 1
    assert removed_profile["low_notional_thin_baseline_count"] == 3

    added_profile = summary["comparison"]["added_shape_profile"]
    assert added_profile["total"] == 1
    assert added_profile["trade_usd_buckets"]["lt_500"] == 1
    assert added_profile["spike_multiplier_buckets"]["lt_5x"] == 1
    assert added_profile["low_notional_thin_baseline_count"] == 1


def test_volume_spike_calibration_summary_includes_bounded_delta_samples():
    from dataclasses import replace
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, summarize_volume_spike_calibration
    from pmfi.replay import ReplayResult

    removed_trade = replace(_calibration_trade(), venue_trade_id="trade-removed")
    second_removed_trade = replace(_calibration_trade(), venue_trade_id="trade-removed-2")
    added_trade = replace(_calibration_trade(), venue_trade_id="trade-added")
    current = [
        ReplayResult(
            fixture_path="db:removed",
            trade=removed_trade,
            alerts=[_calibration_spike_decision()],
            raw_event_id=401,
        ),
        ReplayResult(
            fixture_path="db:removed-2",
            trade=second_removed_trade,
            alerts=[_calibration_spike_decision(this_trade_usd=620.0)],
            raw_event_id=402,
        ),
    ]
    candidate = [
        ReplayResult(
            fixture_path="db:added",
            trade=added_trade,
            alerts=[_calibration_spike_decision(this_trade_usd=1250.0)],
            raw_event_id=501,
        ),
    ]
    review_index = {
        401: {
            "alert_id": "alert-401",
            "trade_id": "trade-id-401",
            "review_label": "noise",
            "review_category": "low_notional_thin_baseline",
            "reviewed_at": datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc),
        },
        501: {"alert_id": "alert-501"},
    }

    summary = summarize_volume_spike_calibration(
        current,
        candidate,
        candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
        review_index_by_raw_event_id=review_index,
        details_limit=1,
        delta_records_limit=0,
    )

    comparison = summary["comparison"]
    assert comparison["details_limit"] == 1
    assert len(comparison["removed_volume_spike_samples"]) == 1
    assert len(comparison["added_volume_spike_samples"]) == 1
    assert comparison["removed_volume_spike_samples"][0] == {
        "raw_event_id": 401,
        "venue_trade_id": "trade-removed",
        "venue": "kalshi",
        "market": "KXBTCD-26JUN1817-T63749.99",
        "this_trade_usd": 750.0,
        "baseline_median_usd": 100.0,
        "spike_multiplier": 7.5,
        "triage_flags": ["low_notional", "thin_baseline"],
        "review": {
            "matched": True,
            "alert_id": "alert-401",
            "trade_id": "trade-id-401",
            "label": "noise",
            "category": "low_notional_thin_baseline",
            "reviewed_at": "2026-06-18T17:00:00+00:00",
        },
    }
    assert comparison["added_volume_spike_samples"][0]["raw_event_id"] == 501
    assert comparison["added_volume_spike_samples"][0]["this_trade_usd"] == 1250.0
    assert comparison["added_volume_spike_samples"][0]["review"] == {
        "matched": True,
        "alert_id": "alert-501",
        "trade_id": None,
        "label": "unreviewed",
        "category": "uncategorized",
        "reviewed_at": None,
    }
    assert comparison["delta_records_limit"] == 0
    assert comparison["removed_delta_records_truncated"] is False
    assert comparison["added_delta_records_truncated"] is False
    assert [row["raw_event_id"] for row in comparison["removed_volume_spike_records"]] == [
        401,
        402,
    ]
    assert [row["raw_event_id"] for row in comparison["added_volume_spike_records"]] == [
        501,
    ]


def test_volume_spike_calibration_summary_counts_unreviewed_persisted_matches():
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, summarize_volume_spike_calibration
    from pmfi.replay import ReplayResult

    current = [
        ReplayResult(
            fixture_path="db:unreviewed",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision()],
            raw_event_id=301,
        )
    ]
    summary = summarize_volume_spike_calibration(
        current,
        [],
        candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
        review_index_by_raw_event_id={301: {"alert_id": "alert-301"}},
    )

    comparison = summary["comparison"]
    assert comparison["removed_review_matches"] == 1
    assert comparison["removed_review_unmatched"] == 0
    assert comparison["removed_review_labels"] == {"unreviewed": 1}
    assert comparison["removed_review_categories"] == {"uncategorized": 1}


def test_volume_spike_calibration_service_runs_validate_only_replays():
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate
    from pmfi.replay import ReplayResult
    from pmfi.volume_spike_calibration import (
        insufficient_volume_spike_evidence_reason,
        run_volume_spike_calibration_replay,
    )

    pool = _make_pool_mock(
        fetch_return=[
            {
                "raw_event_id": 123,
                "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                "review_label": "noise",
                "review_category": "low_notional_thin_baseline",
                "reviewed_at": datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc),
            }
        ]
    )
    current_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision()],
            raw_event_id=123,
        )
    ]
    candidate_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[],
            raw_event_id=123,
        )
    ]
    replay_calls = []

    async def _fake_replay(pool_arg, **kwargs):
        assert pool_arg is pool
        replay_calls.append(kwargs)
        rule = kwargs["rules_config"]["rules"]["volume_spike_v1"]
        return candidate_results if rule["min_trade_usd"] == 1000.0 else current_results

    since = datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc)
    with patch("pmfi.replay.replay_from_db", side_effect=_fake_replay):
        summary = asyncio.run(
            run_volume_spike_calibration_replay(
                pool,
                base_rules_config={"rules": {"volume_spike_v1": {"min_trade_usd": 800}}},
                since_dt=since,
                until_dt=until,
                limit=0,
                venue="kalshi",
                market="KXBTCD-26JUN1817-T63749.99",
                candidate=VolumeSpikeCandidate(min_trade_usd=Decimal("1000")),
                cold_start=True,
            )
        )

    assert len(replay_calls) == 2
    assert replay_calls[0]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 800
    assert replay_calls[0]["persist"] is False
    assert replay_calls[0]["print_summary"] is False
    assert replay_calls[0]["seed"] is False
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 1000.0
    assert summary["filters"] == {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "limit": 0,
        "venue": "kalshi",
        "market": "KXBTCD-26JUN1817-T63749.99",
        "cold_start": True,
        "details_limit": 10,
    }
    assert summary["comparison"]["removed_review_matches"] == 1
    assert insufficient_volume_spike_evidence_reason(summary) is None
    pool.execute.assert_not_awaited()


def test_volume_spike_candidate_rules_include_low_notional_baseline_knobs():
    from decimal import Decimal
    from pmfi.calibration import VolumeSpikeCandidate, build_volume_spike_candidate_rules

    rules = {
        "version": "alert_rules.v1",
        "rules": {
            "volume_spike_v1": {
                "enabled": True,
                "min_trade_usd": 800,
                "min_baseline_trades": 20,
            }
        },
    }

    candidate = VolumeSpikeCandidate(
        low_notional_min_baseline_trades=30,
        low_notional_min_baseline_median_usd=Decimal("150"),
        low_notional_max_spike_multiplier=Decimal("24"),
        low_notional_threshold_usd=Decimal("5000"),
    )
    updated = build_volume_spike_candidate_rules(rules, candidate)

    spike = updated["rules"]["volume_spike_v1"]
    assert spike["min_trade_usd"] == 800
    assert spike["min_baseline_trades"] == 20
    assert spike["low_notional_min_baseline_trades"] == 30
    assert spike["low_notional_min_baseline_median_usd"] == 150.0
    assert spike["low_notional_max_spike_multiplier"] == 24.0
    assert spike["low_notional_threshold_usd"] == 5000.0
    assert candidate.as_dict()["low_notional_min_baseline_trades"] == 30
    assert candidate.as_dict()["low_notional_min_baseline_median_usd"] == 150.0
    assert candidate.as_dict()["low_notional_max_spike_multiplier"] == 24.0


def test_volume_spike_floor_audit_summary_flags_below_floor_and_unknown_notional():
    from decimal import Decimal
    from pmfi.calibration import summarize_volume_spike_floor_audit
    from pmfi.replay import ReplayResult

    below_floor = ReplayResult(
        fixture_path="db:KXBTCD-26JUN1817-T63749.99",
        trade=_calibration_trade(),
        alerts=[_calibration_spike_decision(this_trade_usd=750.0)],
    )
    unknown_notional = ReplayResult(
        fixture_path="db:KXBTCD-26JUN1817-T63749.99",
        trade=_calibration_trade(),
        alerts=[_calibration_spike_decision(this_trade_usd=None)],
    )

    summary = summarize_volume_spike_floor_audit(
        [below_floor, unknown_notional],
        configured_min_trade_usd=Decimal("800"),
    )

    assert summary["schema_version"] == "volume_spike_floor_audit.v1"
    assert summary["local_only"] is True
    assert summary["validate_only"] is True
    assert summary["configured_rule"] == {
        "rule_id": "volume_spike_v1",
        "min_trade_usd": 800.0,
    }
    assert summary["current"]["volume_spike_alerts"] == 2
    assert summary["floor_check"]["passed"] is False
    assert summary["floor_check"]["below_floor_volume_spike_alerts"] == 1
    assert summary["floor_check"]["unknown_trade_usd_volume_spike_alerts"] == 1
    assert summary["floor_check"]["below_floor_trade_usd_buckets"]["500_to_799"] == 1
    assert summary["floor_check"]["unknown_trade_usd_buckets"]["unknown"] == 1
    assert summary["evidence_status"] == "unknown_trade_usd"


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
        low_notional_min_baseline_trades=None,
        low_notional_min_baseline_median_usd=None,
        low_notional_max_spike_multiplier=None,
        low_notional_threshold_usd=None,
        history_max=None,
        cold_start=False,
        format="json",
    )

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool:
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 1
    create_pool.assert_not_called()
    assert "provide at least one candidate" in capsys.readouterr().out


def test_cmd_alerts_volume_spike_calibration_runs_read_only_replay(tmp_path, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_calibration
    from pmfi.replay import ReplayResult

    packet_root = tmp_path / "reports" / "calibration-packets"
    out_path = packet_root / "calibration.json"
    args = argparse.Namespace(
        calibration_from="24h",
        calibration_to="1h",
        limit=0,
        calibration_venue="kalshi",
        calibration_market="KXBTCD-26JUN1817-T63749.99",
        min_spike_multiplier=None,
        min_trade_usd=1000,
        min_baseline_trades=None,
        low_notional_min_baseline_trades=30,
        low_notional_min_baseline_median_usd=150,
        low_notional_max_spike_multiplier=24,
        low_notional_threshold_usd=5000,
        history_max=None,
        cold_start=True,
        export_packet=True,
        packet_output=str(out_path),
        packet_limit=0,
        format="json",
    )
    pool = _make_pool_mock(
        fetch_return=[
            {
                "raw_event_id": 123,
                "alert_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "trade_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                "review_label": "noise",
                "review_category": "low_notional_thin_baseline",
                "reviewed_at": datetime(2026, 6, 18, 17, 0, tzinfo=timezone.utc),
            }
        ]
    )
    current_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision()],
            raw_event_id=123,
        )
    ]
    candidate_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[],
            raw_event_id=123,
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
        rule = kwargs["rules_config"]["rules"]["volume_spike_v1"]
        return candidate_results if rule["min_trade_usd"] == 1000.0 else current_results

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch("pmfi.commands.alerts._calibration_packet_output_root", return_value=packet_root), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.replay.replay_from_db", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_volume_spike_calibration(args)

    assert rc == 0
    assert len(replay_calls) == 2
    assert replay_calls[0]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 800
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 1000.0
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["low_notional_min_baseline_trades"] == 30
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["low_notional_min_baseline_median_usd"] == 150.0
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["low_notional_max_spike_multiplier"] == 24.0
    assert replay_calls[1]["rules_config"]["rules"]["volume_spike_v1"]["low_notional_threshold_usd"] == 5000.0
    pool.fetch.assert_awaited_once()
    review_sql = pool.fetch.call_args[0][0]
    review_bound = pool.fetch.call_args[0][1:]
    assert "latest_reviews AS" in review_sql
    assert "a.rule_key = $1" in review_sql
    assert "COALESCE(r.exchange_ts, r.received_at) >= $2" in review_sql
    assert "COALESCE(r.exchange_ts, r.received_at) <= $3" in review_sql
    assert "r.venue_code = $4" in review_sql
    assert "r.venue_market_id = $5" in review_sql
    assert review_bound[0] == "volume_spike_v1"
    assert review_bound[3:] == ("kalshi", "KXBTCD-26JUN1817-T63749.99")
    pool.execute.assert_not_awaited()
    saved = json.loads(capsys.readouterr().out)
    assert saved["schema_version"] == "volume_spike_calibration.v1"
    assert saved["local_only"] is True
    assert saved["validate_only"] is True
    assert saved["comparison"]["removed_volume_spike_alerts"] == 1
    assert saved["comparison"]["removed_trade_usd_buckets"]["500_to_799"] == 1
    assert saved["comparison"]["review_data_provided"] is True
    assert saved["comparison"]["removed_review_matches"] == 1
    assert saved["comparison"]["removed_review_unmatched"] == 0
    assert saved["comparison"]["removed_review_labels"] == {"noise": 1}
    assert saved["comparison"]["removed_review_categories"] == {
        "low_notional_thin_baseline": 1
    }
    assert saved["packet_output"] == str(out_path)
    assert saved["comparison"]["delta_records_limit"] == 0
    assert saved["comparison"]["removed_delta_records_truncated"] is False
    assert saved["comparison"]["removed_volume_spike_records"][0]["raw_event_id"] == 123
    packet = json.loads(out_path.read_text(encoding="utf-8"))
    assert packet["export_metadata"]["schema_version"] == "volume_spike_calibration_packet.v1"
    assert packet["export_metadata"]["local_only"] is True
    assert packet["export_metadata"]["validate_only"] is True
    assert packet["export_metadata"]["record_counts"]["removed_volume_spike_records"] == 1
    assert packet["calibration_summary"]["packet_output"] == str(out_path)


def _batch_args(**overrides):
    values = {
        "window": [
            "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
            "beta:2026-06-18T14:00:00+00:00:2026-06-18T15:00:00+00:00",
        ],
        "limit": 0,
        "calibration_venue": "kalshi",
        "calibration_market": None,
        "min_spike_multiplier": None,
        "min_trade_usd": None,
        "min_baseline_trades": None,
        "low_notional_min_baseline_trades": 50,
        "low_notional_min_baseline_median_usd": 150,
        "low_notional_max_spike_multiplier": 24,
        "low_notional_threshold_usd": None,
        "history_max": None,
        "cold_start": True,
        "packet_output_prefix": "independent",
        "packet_limit": 0,
        "format": "json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_cmd_calibration_packet_batch_rejects_malformed_window_before_db(capsys):
    from pmfi.commands.alerts import cmd_calibration_packet_batch

    args = _batch_args(window=["missing-separators"])

    with patch("pmfi.commands.alerts.cmd_alerts_volume_spike_calibration") as child:
        rc = cmd_calibration_packet_batch(args)

    assert rc == 1
    child.assert_not_called()
    assert "invalid --window" in capsys.readouterr().out


def test_cmd_calibration_packet_batch_rejects_unsafe_prefix_before_db(capsys):
    from pmfi.commands.alerts import cmd_calibration_packet_batch

    args = _batch_args(packet_output_prefix="..\\outside")

    with patch("pmfi.commands.alerts.cmd_alerts_volume_spike_calibration") as child:
        rc = cmd_calibration_packet_batch(args)

    assert rc == 1
    child.assert_not_called()
    assert "--packet-output-prefix" in capsys.readouterr().out


def test_cmd_calibration_packet_batch_runs_each_window_with_packet_output(
    tmp_path,
    capsys,
):
    from pmfi.commands.alerts import cmd_calibration_packet_batch

    packet_root = tmp_path / "reports" / "calibration-packets"
    calls: list[argparse.Namespace] = []

    def _fake_child(child_args):
        calls.append(child_args)
        return 0

    args = _batch_args()
    with patch("pmfi.commands.alerts._calibration_packet_output_root", return_value=packet_root), \
            patch("pmfi.commands.alerts.cmd_alerts_volume_spike_calibration", side_effect=_fake_child):
        rc = cmd_calibration_packet_batch(args)

    assert rc == 0
    assert len(calls) == 2
    assert calls[0].calibration_from == "2026-06-18T12:00:00+00:00"
    assert calls[0].calibration_to == "2026-06-18T13:00:00+00:00"
    assert calls[1].calibration_from == "2026-06-18T14:00:00+00:00"
    assert calls[1].calibration_to == "2026-06-18T15:00:00+00:00"
    assert [call.packet_output for call in calls] == [
        "independent-alpha.json",
        "independent-beta.json",
    ]
    assert all(call.export_packet is True for call in calls)
    assert all(call.cold_start is True for call in calls)
    assert all(call.calibration_venue == "kalshi" for call in calls)
    assert all(call.low_notional_min_baseline_trades == 50 for call in calls)
    assert all(call.low_notional_min_baseline_median_usd == 150 for call in calls)
    assert all(call.low_notional_max_spike_multiplier == 24 for call in calls)
    assert all(call.format == "text" for call in calls)

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "calibration_packet_batch.v1"
    assert payload["local_only"] is True
    assert payload["validate_only"] is True
    assert payload["db_mutation"] is False
    assert payload["windows"][0]["packet_output"].endswith("independent-alpha.json")
    assert payload["windows"][1]["packet_output"].endswith("independent-beta.json")


def _sweep_args(**overrides):
    values = {
        "window": [
            "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
            "beta:2026-06-18T14:00:00+00:00:2026-06-18T15:00:00+00:00",
        ],
        "limit": 0,
        "calibration_venue": "kalshi",
        "calibration_market": None,
        "low_notional_min_baseline_trades": [30, 50],
        "low_notional_threshold_usd": [5000],
        "low_notional_min_baseline_median_usd": [100, 250],
        "low_notional_max_spike_multiplier": [],
        "cold_start": True,
        "format": "json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {
                "low_notional_min_baseline_trades": [],
                "low_notional_min_baseline_median_usd": [],
            },
            "provide at least one",
        ),
        ({"low_notional_min_baseline_trades": [0]}, "must be > 0"),
        ({"low_notional_threshold_usd": [0]}, "must be > 0"),
        ({"low_notional_min_baseline_median_usd": [0]}, "must be > 0"),
        ({"low_notional_max_spike_multiplier": [0]}, "must be > 0"),
        ({"window": []}, "at least one --window"),
        ({"window": ["bad-window"]}, "invalid --window"),
        ({"low_notional_min_baseline_trades": [30, 30]}, "duplicate candidate"),
        ({"low_notional_min_baseline_median_usd": [100, 100]}, "duplicate candidate"),
        ({"low_notional_max_spike_multiplier": [12, 12]}, "duplicate candidate"),
    ],
)
def test_cmd_volume_spike_calibration_sweep_rejects_invalid_input_before_db(
    overrides,
    expected,
    capsys,
):
    import asyncpg
    from pmfi.commands.alerts import cmd_volume_spike_calibration_sweep

    with patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool, \
            patch("pmfi.volume_spike_calibration.run_volume_spike_calibration_replay") as replay:
        rc = cmd_volume_spike_calibration_sweep(_sweep_args(**overrides))

    assert rc == 1
    create_pool.assert_not_called()
    replay.assert_not_called()
    assert expected in capsys.readouterr().out


def test_cmd_volume_spike_calibration_sweep_runs_cartesian_product_json(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_volume_spike_calibration_sweep

    pool = _make_pool_mock()
    calls = []

    async def _fake_replay(pool_arg, **kwargs):
        assert pool_arg is pool
        calls.append(kwargs)
        candidate = kwargs["candidate"]
        removed = (
            1
            if (
                candidate.low_notional_min_baseline_trades == 30
                and candidate.low_notional_min_baseline_median_usd == Decimal("100")
            )
            else 0
        )
        added = 0
        labels = {"noise": 1} if removed else {}
        categories = {"low_notional_thin_baseline": 1} if removed else {}
        removed_buckets = {
            "unknown": 0,
            "lt_500": 0,
            "500_to_799": 0,
            "800_to_999": removed,
            "gte_1000": 0,
        }
        added_buckets = {
            "unknown": 0,
            "lt_500": 0,
            "500_to_799": 0,
            "800_to_999": 0,
            "gte_1000": 0,
        }
        removed_shape_profile = {
            "total": removed,
            "trade_usd_buckets": removed_buckets,
            "spike_multiplier_buckets": {
                "unknown": 0,
                "lt_5x": 0,
                "5_to_9x": removed,
                "10_to_24x": 0,
                "gte_25x": 0,
            },
            "triage_flag_counts": {
                "low_notional": removed,
                "thin_baseline": removed,
            } if removed else {},
            "near_threshold_count": 0,
            "low_notional_thin_baseline_count": removed,
        }
        added_shape_profile = {
            "total": added,
            "trade_usd_buckets": added_buckets,
            "spike_multiplier_buckets": {
                "unknown": 0,
                "lt_5x": 0,
                "5_to_9x": 0,
                "10_to_24x": 0,
                "gte_25x": 0,
            },
            "triage_flag_counts": {},
            "near_threshold_count": 0,
            "low_notional_thin_baseline_count": 0,
        }
        return {
            "schema_version": "volume_spike_calibration.v1",
            "local_only": True,
            "validate_only": True,
            "candidate": candidate.as_dict(),
            "current": {"normalized_trades": 2, "volume_spike_alerts": 2},
            "candidate_replay": {"volume_spike_alerts": 2 - removed + added},
            "comparison": {
                "removed_volume_spike_alerts": removed,
                "added_volume_spike_alerts": added,
                "removed_low_notional_thin_baseline": removed,
                "removed_trade_usd_buckets": removed_buckets,
                "added_trade_usd_buckets": added_buckets,
                "removed_shape_profile": removed_shape_profile,
                "added_shape_profile": added_shape_profile,
                "removed_review_matches": removed,
                "removed_review_unmatched": 0,
                "removed_review_labels": labels,
                "removed_review_categories": categories,
                "added_review_matches": 0,
                "added_review_labels": {},
            },
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.volume_spike_calibration.run_volume_spike_calibration_replay", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_volume_spike_calibration_sweep(_sweep_args())

    assert rc == 0
    assert len(calls) == 8
    assert [
        (
            call["since_dt"].isoformat(),
            call["candidate"].low_notional_min_baseline_trades,
            call["candidate"].low_notional_min_baseline_median_usd,
        )
        for call in calls
    ] == [
        ("2026-06-18T12:00:00+00:00", 30, Decimal("100")),
        ("2026-06-18T12:00:00+00:00", 30, Decimal("250")),
        ("2026-06-18T12:00:00+00:00", 50, Decimal("100")),
        ("2026-06-18T12:00:00+00:00", 50, Decimal("250")),
        ("2026-06-18T14:00:00+00:00", 30, Decimal("100")),
        ("2026-06-18T14:00:00+00:00", 30, Decimal("250")),
        ("2026-06-18T14:00:00+00:00", 50, Decimal("100")),
        ("2026-06-18T14:00:00+00:00", 50, Decimal("250")),
    ]
    assert {call["candidate"].low_notional_threshold_usd for call in calls} == {Decimal("5000")}
    assert {call["candidate"].low_notional_max_spike_multiplier for call in calls} == {None}
    assert all(call["limit"] == 0 for call in calls)
    assert all(call["venue"] == "kalshi" for call in calls)
    assert all(call["market"] is None for call in calls)
    assert all(call["cold_start"] is True for call in calls)
    pool.execute.assert_not_awaited()

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "volume_spike_calibration_sweep.v1"
    assert payload["local_only"] is True
    assert payload["validate_only"] is True
    assert payload["config_mutation"] is False
    assert payload["db_mutation"] is False
    assert payload["live_calls"] is False
    assert payload["filters"]["venue"] == "kalshi"
    assert payload["candidates"][0] == {
        "label": "baseline-30-threshold-5000-median-100-maxmult-default",
        "low_notional_min_baseline_trades": 30,
        "low_notional_threshold_usd": 5000.0,
        "low_notional_min_baseline_median_usd": 100.0,
        "low_notional_max_spike_multiplier": None,
    }
    assert payload["rows"][0]["window_name"] == "alpha"
    assert payload["rows"][0]["candidate_label"] == "baseline-30-threshold-5000-median-100-maxmult-default"
    assert payload["rows"][0]["candidate_config"]["low_notional_min_baseline_median_usd"] == 100.0
    assert payload["rows"][0]["current_spikes"] == 2
    assert payload["rows"][0]["candidate_spikes"] == 1
    assert payload["rows"][0]["removed_review_labels"] == {"noise": 1}
    assert payload["rows"][0]["removed_trade_usd_buckets"]["800_to_999"] == 1
    assert payload["rows"][0]["added_trade_usd_buckets"]["800_to_999"] == 0
    assert payload["rows"][0]["removed_shape_profile"]["spike_multiplier_buckets"]["5_to_9x"] == 1
    assert payload["rows"][0]["removed_shape_profile"]["triage_flag_counts"] == {
        "low_notional": 1,
        "thin_baseline": 1,
    }
    assert payload["rows"][0]["evidence_reason"] is None
    assert payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"]["windows"] == 2
    assert payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"]["removed_reviewed_noise_or_fp"] == 2
    assert (
        payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"][
            "removed_trade_usd_buckets"
        ]["800_to_999"]
        == 2
    )
    assert (
        payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"][
            "removed_shape_profile"
        ]["spike_multiplier_buckets"]["5_to_9x"]
        == 2
    )
    assert (
        payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"][
            "removed_shape_profile"
        ]["low_notional_thin_baseline_count"]
        == 2
    )
    assert payload["aggregate"]["baseline-30-threshold-5000-median-100-maxmult-default"]["recommendation"] == "change-ready-candidate"
    assert payload["aggregate"]["baseline-50-threshold-5000-median-250-maxmult-default"]["recommendation"] == "no-candidate-effect"


def test_cmd_volume_spike_calibration_sweep_text_surfaces_removed_trade_buckets(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_volume_spike_calibration_sweep

    pool = _make_pool_mock()

    async def _fake_replay(pool_arg, **kwargs):
        assert pool_arg is pool
        candidate = kwargs["candidate"]
        return {
            "schema_version": "volume_spike_calibration.v1",
            "local_only": True,
            "validate_only": True,
            "candidate": candidate.as_dict(),
            "current": {"normalized_trades": 2, "volume_spike_alerts": 2},
            "candidate_replay": {"volume_spike_alerts": 1},
            "comparison": {
                "removed_volume_spike_alerts": 1,
                "added_volume_spike_alerts": 0,
                "removed_low_notional_thin_baseline": 1,
                "removed_trade_usd_buckets": {
                    "unknown": 0,
                    "lt_500": 0,
                    "500_to_799": 0,
                    "800_to_999": 1,
                    "gte_1000": 0,
                },
                "added_trade_usd_buckets": {
                    "unknown": 0,
                    "lt_500": 0,
                    "500_to_799": 0,
                    "800_to_999": 0,
                    "gte_1000": 0,
                },
                "removed_shape_profile": {
                    "total": 1,
                    "trade_usd_buckets": {
                        "unknown": 0,
                        "lt_500": 0,
                        "500_to_799": 0,
                        "800_to_999": 1,
                        "gte_1000": 0,
                    },
                    "spike_multiplier_buckets": {
                        "unknown": 0,
                        "lt_5x": 0,
                        "5_to_9x": 0,
                        "10_to_24x": 1,
                        "gte_25x": 0,
                    },
                    "triage_flag_counts": {
                        "low_notional": 1,
                        "thin_baseline": 1,
                    },
                    "near_threshold_count": 0,
                    "low_notional_thin_baseline_count": 1,
                },
                "added_shape_profile": {
                    "total": 0,
                    "trade_usd_buckets": {
                        "unknown": 0,
                        "lt_500": 0,
                        "500_to_799": 0,
                        "800_to_999": 0,
                        "gte_1000": 0,
                    },
                    "spike_multiplier_buckets": {
                        "unknown": 0,
                        "lt_5x": 0,
                        "5_to_9x": 0,
                        "10_to_24x": 0,
                        "gte_25x": 0,
                    },
                    "triage_flag_counts": {},
                    "near_threshold_count": 0,
                    "low_notional_thin_baseline_count": 0,
                },
                "removed_review_matches": 1,
                "removed_review_unmatched": 0,
                "removed_review_labels": {"tp": 1},
                "removed_review_categories": {"true_positive_risk": 1},
                "added_review_matches": 0,
                "added_review_labels": {},
            },
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.volume_spike_calibration.run_volume_spike_calibration_replay", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_volume_spike_calibration_sweep(_sweep_args(
            window=["tp-risk:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z"],
            low_notional_min_baseline_trades=[],
            low_notional_min_baseline_median_usd=[20],
            low_notional_threshold_usd=[1000],
            format="text",
        ))

    assert rc == 0
    output = capsys.readouterr().out
    assert 'removed_buckets={"500_to_799": 0, "800_to_999": 1' in output
    assert 'removed_spike_buckets={"10_to_24x": 1' in output
    assert "recommendation=blocked-by-true-positive-risk" in output


def test_cmd_volume_spike_calibration_sweep_accepts_median_only_candidate(capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_volume_spike_calibration_sweep

    pool = _make_pool_mock()
    calls = []

    async def _fake_replay(pool_arg, **kwargs):
        assert pool_arg is pool
        calls.append(kwargs)
        candidate = kwargs["candidate"]
        return {
            "schema_version": "volume_spike_calibration.v1",
            "local_only": True,
            "validate_only": True,
            "candidate": candidate.as_dict(),
            "current": {"normalized_trades": 2, "volume_spike_alerts": 2},
            "candidate_replay": {"volume_spike_alerts": 2},
            "comparison": {
                "removed_volume_spike_alerts": 0,
                "added_volume_spike_alerts": 0,
                "removed_low_notional_thin_baseline": 0,
                "removed_review_matches": 0,
                "removed_review_unmatched": 0,
                "removed_review_labels": {},
                "removed_review_categories": {},
                "added_review_matches": 0,
                "added_review_labels": {},
            },
        }

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.volume_spike_calibration.run_volume_spike_calibration_replay", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_volume_spike_calibration_sweep(_sweep_args(
            low_notional_min_baseline_trades=[],
            low_notional_min_baseline_median_usd=[20],
            low_notional_max_spike_multiplier=[24],
            low_notional_threshold_usd=[1000],
        ))

    assert rc == 0
    assert len(calls) == 2
    assert all(call["candidate"].low_notional_min_baseline_trades is None for call in calls)
    assert {call["candidate"].low_notional_min_baseline_median_usd for call in calls} == {Decimal("20")}
    assert {call["candidate"].low_notional_max_spike_multiplier for call in calls} == {Decimal("24")}
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == [{
        "label": "baseline-default-threshold-1000-median-20-maxmult-24",
        "low_notional_min_baseline_trades": None,
        "low_notional_threshold_usd": 1000.0,
        "low_notional_min_baseline_median_usd": 20.0,
        "low_notional_max_spike_multiplier": 24.0,
    }]
    assert payload["aggregate"]["baseline-default-threshold-1000-median-20-maxmult-24"]["recommendation"] == "no-candidate-effect"


def test_cmd_alerts_volume_spike_floor_audit_runs_read_only_current_replay(tmp_path, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_floor_audit
    from pmfi.replay import ReplayResult

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "alert_rules.yaml").write_text(
        "version: alert_rules.v1\n"
        "rules:\n"
        "  volume_spike_v1:\n"
        "    enabled: true\n"
        "    min_trade_usd: 800\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        audit_from="24h",
        audit_to=None,
        limit=0,
        audit_venue="kalshi",
        audit_market="KXBTCD-26JUN1817-T63749.99",
        cold_start=True,
        format="json",
    )
    pool = _make_pool_mock()
    current_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision(this_trade_usd=870.0, min_trade_usd=800.0)],
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
        return current_results

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch("pmfi.commands._shared.ROOT", tmp_path), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.replay.replay_from_db", side_effect=_fake_replay), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_volume_spike_floor_audit(args)

    assert rc == 0
    assert len(replay_calls) == 1
    assert replay_calls[0]["rules_config"]["rules"]["volume_spike_v1"]["min_trade_usd"] == 800
    pool.execute.assert_not_awaited()
    saved = json.loads(capsys.readouterr().out)
    assert saved["schema_version"] == "volume_spike_floor_audit.v1"
    assert saved["configured_rule"]["min_trade_usd"] == 800.0
    assert saved["floor_check"]["passed"] is True
    assert saved["floor_check"]["below_floor_volume_spike_alerts"] == 0
    assert saved["floor_check"]["unknown_trade_usd_volume_spike_alerts"] == 0
    assert saved["current"]["volume_spike_trade_usd_buckets"]["800_to_999"] == 1


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("empty", "no normalized trades"),
        ("no_spikes", "no current volume_spike_v1 alerts"),
    ],
)
def test_cmd_alerts_volume_spike_floor_audit_rejects_insufficient_runtime_evidence(
    case,
    message,
    tmp_path,
    capsys,
):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_floor_audit
    from pmfi.replay import ReplayResult

    if case == "empty":
        replay_results = []
    else:
        replay_results = [
            ReplayResult(
                fixture_path="db:KXBTCD-26JUN1817-T63749.99",
                trade=_calibration_trade(),
                alerts=[],
            )
        ]
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "alert_rules.yaml").write_text(
        "version: alert_rules.v1\n"
        "rules:\n"
        "  volume_spike_v1:\n"
        "    min_trade_usd: 800\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        audit_from="24h",
        audit_to=None,
        limit=0,
        audit_venue=None,
        audit_market=None,
        cold_start=False,
        format="json",
    )
    pool = _make_pool_mock()

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch("pmfi.commands._shared.ROOT", tmp_path), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.replay.replay_from_db", AsyncMock(return_value=replay_results)), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_volume_spike_floor_audit(args)

    assert rc == 1
    assert message in capsys.readouterr().out
    pool.execute.assert_not_awaited()


def test_cmd_alerts_volume_spike_floor_audit_exits_nonzero_on_floor_violation(tmp_path, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_floor_audit
    from pmfi.replay import ReplayResult

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "alert_rules.yaml").write_text(
        "version: alert_rules.v1\n"
        "rules:\n"
        "  volume_spike_v1:\n"
        "    min_trade_usd: 800\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        audit_from="24h",
        audit_to=None,
        limit=0,
        audit_venue=None,
        audit_market=None,
        cold_start=False,
        format="json",
    )
    pool = _make_pool_mock()
    replay_results = [
        ReplayResult(
            fixture_path="db:KXBTCD-26JUN1817-T63749.99",
            trade=_calibration_trade(),
            alerts=[_calibration_spike_decision(this_trade_usd=750.0, min_trade_usd=800.0)],
        )
    ]

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
            patch("pmfi.commands._shared.ROOT", tmp_path), \
            patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
            patch("pmfi.replay.replay_from_db", AsyncMock(return_value=replay_results)), \
            patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_volume_spike_floor_audit(args)

    assert rc == 1
    saved = json.loads(capsys.readouterr().out)
    assert saved["floor_check"]["passed"] is False
    assert saved["floor_check"]["below_floor_volume_spike_alerts"] == 1
    assert saved["evidence_status"] == "below_floor_volume_spikes"
    pool.execute.assert_not_awaited()


def test_cmd_alerts_volume_spike_floor_audit_rejects_missing_floor_before_db(tmp_path, capsys):
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_volume_spike_floor_audit

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "alert_rules.yaml").write_text(
        "version: alert_rules.v1\nrules:\n  volume_spike_v1:\n    enabled: true\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        audit_from="24h",
        audit_to=None,
        limit=0,
        audit_venue=None,
        audit_market=None,
        cold_start=False,
        format="json",
    )

    with patch("pmfi.commands._shared.ROOT", tmp_path), \
            patch.object(asyncpg, "create_pool", new=AsyncMock()) as create_pool:
        rc = cmd_alerts_volume_spike_floor_audit(args)

    assert rc == 1
    create_pool.assert_not_called()
    assert "missing volume_spike_v1.min_trade_usd" in capsys.readouterr().out


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
            if "COALESCE(lr.label, 'unreviewed')" in sql:
                return [{"label": "noise", "cnt": 1}]
            if "WHEN lr.alert_id IS NULL THEN 'unreviewed'" in sql:
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
    assert packet["export_metadata"]["filters"]["review_state"] == "reviewed"
    assert packet["export_metadata"]["filters"]["review_label"] == "noise"
    assert packet["cohort_totals"]["alerts"] == 1
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


def test_get_review_packet_can_export_unreviewed_queue_without_writes():
    """Repository packet helper can export unreviewed alert queues read-only."""
    import asyncio
    from pmfi.db.repos.alerts import get_review_packet

    class Conn:
        def __init__(self):
            self.fetch_sqls: list[str] = []
            self.fetchval_sqls: list[str] = []

        async def fetch(self, sql, *args):
            self.fetch_sqls.append(sql)
            if "SELECT a.alert_id::text AS alert_id" in sql:
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
                        '{"this_trade_usd": 990, "baseline_median_usd": 0.99, '
                        '"spike_multiplier": 994.97, "min_spike_multiplier": 5.0, '
                        '"baseline_trades": 20}'
                    ),
                    "review_id": None,
                    "review_label": None,
                    "review_category": None,
                    "review_notes": None,
                    "reviewed_by": None,
                    "reviewed_at": None,
                }]
            if "COALESCE(lr.label, 'unreviewed')" in sql:
                return [{"label": "unreviewed", "cnt": 1}]
            if "WHEN lr.alert_id IS NULL THEN 'unreviewed'" in sql:
                return [{"category": "unreviewed", "cnt": 1}]
            if "GROUP BY a.rule_key" in sql:
                return [{"rule_key": "volume_spike_v1", "cnt": 1}]
            if "GROUP BY a.venue_code" in sql:
                return [{"venue_code": "kalshi", "cnt": 1}]
            return []

        async def fetchval(self, sql, *args):
            self.fetchval_sqls.append(sql)
            if "SELECT COUNT(*)" in sql and "FROM alerts a" in sql:
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
        review_state="unreviewed",
        limit=10,
    ))

    assert packet["export_metadata"]["filters"]["review_state"] == "unreviewed"
    assert packet["cohort_totals"]["alerts"] == 1
    assert packet["cohort_totals"]["by_label"] == [{"label": "unreviewed", "cnt": 1}]
    assert packet["cohort_totals"]["by_category"] == [{"category": "unreviewed", "cnt": 1}]
    alert = packet["alerts"][0]
    assert alert["latest_review"] == {
        "review_id": None,
        "label": None,
        "category": None,
        "notes": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }
    assert "this_trade_usd=$990" in alert["evidence_summary"]
    assert alert["triage_flags"] == ["low_notional", "thin_baseline"]
    assert any("LEFT JOIN latest_reviews lr" in sql for sql in conn.fetch_sqls)
    assert any("lr.alert_id IS NULL" in sql for sql in conn.fetch_sqls)


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
