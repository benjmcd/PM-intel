from __future__ import annotations

"""Tests for cmd_alerts_review and cmd_alerts_fp_rate commands."""

import argparse
import asyncio
import json
import sys
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
    """cmd_alerts_review inserts into alert_reviews with correct arguments."""
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

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 0
    pool.execute.assert_awaited_once()
    call_args = pool.execute.call_args
    # First positional arg is the SQL string
    sql = call_args[0][0]
    assert "INSERT INTO alert_reviews" in sql
    # Remaining positional args are the bind parameters
    bound = call_args[0][1:]
    assert _alert_id in bound
    assert "fp" in bound


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

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
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
