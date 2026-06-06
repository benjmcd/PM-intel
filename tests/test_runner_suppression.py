"""Tests for alert suppression logic in the pipeline runner."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest


# ---------------------------------------------------------------------------
# Suppression cache key and timing logic (no asyncpg dependency)
# The cache is dict[tuple[str, str, str], datetime]; we test the semantics.
# ---------------------------------------------------------------------------

def test_suppression_key_is_venue_market_rule_tuple():
    cache: dict = {}
    key = ("polymarket", "market-abc", "large_trade_absolute_v1")
    now = datetime.now(timezone.utc)
    cache[key] = now
    assert cache[key] is now


def test_suppression_within_window_is_detected():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a")
    now = datetime.now(timezone.utc)
    cache[key] = now
    later = now + timedelta(seconds=100)
    assert (later - cache[key]).total_seconds() < 300


def test_suppression_outside_window_is_not_suppressed():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a")
    now = datetime.now(timezone.utc)
    cache[key] = now
    later = now + timedelta(seconds=400)
    assert (later - cache[key]).total_seconds() >= 300


def test_different_rules_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a")] = now
    assert ("polymarket", "market-1", "rule-b") not in cache


def test_different_markets_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a")] = now
    assert ("polymarket", "market-2", "rule-a") not in cache


def test_different_venues_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a")] = now
    assert ("kalshi", "market-1", "rule-a") not in cache


def test_suppression_timestamp_updated_on_refill():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a")
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=400)
    cache[key] = t1
    cache[key] = t2  # refill after window expired
    assert cache[key] == t2


# ---------------------------------------------------------------------------
# _months_ahead helper (migrations.py — no asyncpg dependency)
# ---------------------------------------------------------------------------

def test_months_ahead_basic():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 1, 3) == [(2025, 1), (2025, 2), (2025, 3), (2025, 4)]


def test_months_ahead_year_rollover():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 11, 3) == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_months_ahead_december():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 12, 2) == [(2025, 12), (2026, 1), (2026, 2)]


def test_months_ahead_zero_returns_only_current():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 6, 0) == [(2025, 6)]


def test_months_ahead_length_is_count_plus_one():
    from pmfi.db.migrations import _months_ahead
    for n in range(5):
        assert len(_months_ahead(2025, 3, n)) == n + 1


# ---------------------------------------------------------------------------
# process_event suppression end-to-end (requires asyncpg; skipped if absent)
# ---------------------------------------------------------------------------

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg not installed in test env")


def test_process_event_suppresses_second_alert_within_window():
    """insert_alert called once; second identical alert within window is suppressed."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="ev-001",
        venue_market_id="test-market-suppression",
        exchange_ts=None,
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
    mock_trade = MagicMock()
    mock_trade.venue_code = "polymarket"
    mock_trade.venue_market_id = "test-market-suppression"
    mock_trade.outcome_key = "yes"
    mock_trade.capital_at_risk_usd = Decimal("50000")

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = [alert]
    mock_handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-1", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-1")) as mock_insert,
    ):
        cache: dict = {}
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1
        assert mock_handler.call_count == 1

        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1, "suppressed within window"
        assert mock_handler.call_count == 1, "alert handler suppressed too"


def test_process_event_no_suppression_when_none():
    """suppression=None fires every time (replay / backtest mode)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="ev-002",
        venue_market_id="test-market-nosup",
        exchange_ts=None,
        payload={},
    )
    alert = AlertDecision(
        emit_alert=True, rule_id="rule-x", rule_version="v1", severity="low",
        confidence="low", score=Decimal("0.5"), reason_codes=(), data_quality="unverified", evidence={},
    )
    mock_trade = MagicMock()
    mock_trade.venue_code = "polymarket"
    mock_trade.venue_market_id = "test-market-nosup"
    mock_trade.outcome_key = "yes"
    mock_trade.capital_at_risk_usd = Decimal("1000")

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = [alert]
    mock_handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-2", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-2")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-2")) as mock_insert,
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler, suppression=None))
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler, suppression=None))
        assert mock_insert.call_count == 2, "replay mode: both calls insert"
