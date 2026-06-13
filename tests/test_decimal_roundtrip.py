"""Proves exact Decimal values survive Postgres roundtrip via asyncpg.

Skipped when asyncpg is not installed (CI without DB). Run with a live DB.
"""
from __future__ import annotations
import asyncio
from decimal import Decimal

import pytest

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg not installed")


ROUNDTRIP_VALUES = [
    Decimal("0.01"),
    Decimal("0.33"),
    Decimal("0.67"),
    Decimal("219.217767"),
    Decimal("0.000001"),
    Decimal("99999.999999"),
]

def _get_db_url() -> str:
    import os
    if url := os.environ.get("PMFI_DB_URL"):
        return url
    try:
        from pmfi.config import load_config
        return load_config().database.url
    except Exception:
        return "postgresql://pmfi:pmfi@localhost/pmfi"


def _has_db() -> bool:
    try:
        url = _get_db_url()
        async def _check():
            conn = await asyncpg.connect(url, timeout=10)
            await conn.close()
        asyncio.run(_check())
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _has_db(), reason="local Postgres not available")


@requires_db
@pytest.mark.parametrize("value", ROUNDTRIP_VALUES)
def test_decimal_roundtrip_via_cast(value: Decimal):
    """Verify a Decimal survives a SELECT CAST($1 AS numeric) roundtrip."""
    async def _run():
        conn = await asyncpg.connect(_get_db_url())
        try:
            result = await conn.fetchval("SELECT CAST($1 AS numeric)", value)
            return result
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result == value, f"Expected {value!r}, got {result!r} (type={type(result).__name__})"
    assert isinstance(result, Decimal), f"Expected Decimal, got {type(result).__name__}"


@requires_db
def test_decimal_roundtrip_in_normalized_trades_insert():
    """Verify Decimal columns survive a real normalized_trades INSERT+SELECT cycle.

    Requires at least one row in markets table (populated by pmfi replay --persist).

    The INSERT runs inside a transaction that is always rolled back: RETURNING
    still proves the numeric columns preserve Decimal precision (Postgres has
    already coerced the values into numeric(12,8)/numeric(28,8) by the time it
    returns them), but the canary row never persists into the operator's DB.
    """
    async def _run():
        conn = await asyncpg.connect(_get_db_url())
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                market_id = await conn.fetchval(
                    "SELECT market_id FROM markets WHERE venue_code='polymarket' LIMIT 1"
                )
                if market_id is None:
                    return None
                row = await conn.fetchrow(
                    """
                    INSERT INTO normalized_trades
                        (venue_code, market_id, venue_trade_id, outcome_key,
                         price, contracts, capital_at_risk_usd, payout_notional_usd,
                         received_at)
                    VALUES
                        ('polymarket', $5, 'canary-dt-roundtrip-001', 'yes',
                         $1, $2, $3, $4, now())
                    RETURNING price, contracts, capital_at_risk_usd, payout_notional_usd
                    """,
                    Decimal("0.33"),
                    Decimal("0.67"),
                    Decimal("219.217767"),
                    Decimal("0.01"),
                    market_id,
                )
                return row
            finally:
                # Never persist canary data into the live operator DB.
                await tx.rollback()
        finally:
            await conn.close()

    row = asyncio.run(_run())
    if row is None:
        pytest.skip("No polymarket markets in DB; run 'pmfi replay --persist' first")
    assert row["price"] == Decimal("0.33"), f"price mismatch: {row['price']!r}"
    assert row["contracts"] == Decimal("0.67"), f"contracts mismatch: {row['contracts']!r}"
    assert row["capital_at_risk_usd"] == Decimal("219.217767"), f"capital mismatch: {row['capital_at_risk_usd']!r}"
    assert row["payout_notional_usd"] == Decimal("0.01"), f"payout mismatch: {row['payout_notional_usd']!r}"
