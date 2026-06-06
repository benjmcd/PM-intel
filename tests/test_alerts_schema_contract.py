"""Schema-contract tests: verify alerts.py SQL column names match the live DB.

These tests run only when a local Postgres connection is available.
They catch column-name mismatches that FakeConn mocks would miss.
"""
from __future__ import annotations
import asyncio
import os
import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance"
)


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_alerts_table_has_rule_key_not_rule_id():
    """alerts table must have rule_key column (not rule_id)."""
    import asyncpg

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'alerts'"
            )
            return {r["column_name"] for r in cols}
        finally:
            await conn.close()

    columns = asyncio.run(_run())
    assert "rule_key" in columns, f"alerts table missing rule_key; has: {columns}"
    assert "rule_id" not in columns, f"alerts table has unexpected rule_id column"


def test_list_alerts_executes_without_error():
    """list_alerts() must execute without ColumnNotFoundError on real DB."""
    import asyncpg
    from pmfi.db.repos.alerts import list_alerts

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await list_alerts(conn, limit=1)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert isinstance(result, list)


def test_load_suppression_cache_executes_without_error():
    """load_suppression_cache() must execute without ColumnNotFoundError on real DB."""
    import asyncpg
    from pmfi.db.repos.alerts import load_suppression_cache

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            return await load_suppression_cache(conn, window_seconds=300)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert isinstance(result, dict)


def test_get_alert_summary_executes_without_error():
    """get_alert_summary() must execute without ColumnNotFoundError on real DB."""
    import asyncpg
    from pmfi.db.repos.alerts import get_alert_summary
    from datetime import datetime, timezone, timedelta

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            return await get_alert_summary(conn, since=since)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert "total" in result
    assert "by_rule" in result
