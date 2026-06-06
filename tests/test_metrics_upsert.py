"""Tests for metric_windows upsert — no DB required."""
from __future__ import annotations
import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from decimal import Decimal
from pmfi.db.repos.metrics import upsert_metric_window


def _make_trade(
    *,
    venue_code: str = "polymarket",
    venue_market_id: str = "pm-test-market",
    outcome_key: str = "yes",
    price: str = "0.60",
    capital_at_risk_usd: str = "12000.00",
    payout_notional_usd: str = "20000.00",
):
    from pmfi.domain import NormalizedTrade
    return NormalizedTrade(
        venue_code=venue_code,
        venue_market_id=venue_market_id,
        outcome_key=outcome_key,
        directional_side=outcome_key,
        aggressor_side="unknown",
        price=Decimal(price),
        contracts=Decimal("20000"),
        capital_at_risk_usd=Decimal(capital_at_risk_usd),
        payout_notional_usd=Decimal(payout_notional_usd),
        open_interest_contracts=None,
        venue_trade_id="trade-test-1",
        exchange_ts=None,
        received_at=datetime(2026, 6, 6, 13, 0, 0, tzinfo=timezone.utc),
    )


def test_upsert_metric_window_calls_execute():
    """upsert_metric_window calls conn.execute with INSERT ... ON CONFLICT clause."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    trade = _make_trade()

    asyncio.run(upsert_metric_window(conn, trade, market_id="a" * 32 + "b" * 4))

    assert conn.execute.called
    sql_call = conn.execute.call_args[0][0]
    assert "ON CONFLICT" in sql_call
    assert "DO UPDATE" in sql_call


def test_upsert_metric_window_sql_accumulates_trade_count():
    """SQL must increment trade_count, not overwrite it."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    trade = _make_trade()

    asyncio.run(upsert_metric_window(conn, trade, market_id="a" * 32 + "b" * 4))

    sql = conn.execute.call_args[0][0]
    assert "trade_count" in sql
    assert "trade_count + 1" in sql or "trade_count + EXCLUDED" in sql or "+ 1" in sql


def test_upsert_metric_window_sql_uses_greatest_for_max():
    """SQL must use GREATEST to accumulate max_trade_capital_at_risk_usd."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    trade = _make_trade()

    asyncio.run(upsert_metric_window(conn, trade, market_id="a" * 32 + "b" * 4))

    sql = conn.execute.call_args[0][0]
    assert "GREATEST" in sql
    assert "max_trade_capital_at_risk_usd" in sql


def test_upsert_metric_window_sql_sums_gross_capital():
    """SQL must sum gross_capital_at_risk_usd across trades in the same window."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    trade = _make_trade()

    asyncio.run(upsert_metric_window(conn, trade, market_id="a" * 32 + "b" * 4))

    sql = conn.execute.call_args[0][0]
    assert "gross_capital_at_risk_usd" in sql
    assert "EXCLUDED.gross_capital_at_risk_usd" in sql


def test_upsert_metric_window_sets_both_cap_columns():
    """INSERT must include both gross and max capital columns."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    trade = _make_trade(capital_at_risk_usd="15000.00")

    asyncio.run(upsert_metric_window(conn, trade, market_id="a" * 32 + "b" * 4))

    sql = conn.execute.call_args[0][0]
    assert "max_trade_capital_at_risk_usd" in sql
    assert "gross_capital_at_risk_usd" in sql
