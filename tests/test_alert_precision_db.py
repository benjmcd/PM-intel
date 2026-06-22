from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "alert_precision_manifest.yaml"
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _ts(offset_seconds: int) -> datetime:
    return datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _admin_dsn(base_dsn: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _quote_ident(identifier: str) -> str:
    if not _DB_NAME_RE.fullmatch(identifier):
        raise ValueError(f"unsafe scratch database name: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


async def _drop_database(conn: asyncpg.Connection, name: str) -> None:
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
        name,
    )
    await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")
    still_exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
    if still_exists:
        raise RuntimeError(f"scratch database still exists after drop: {name}")


async def _create_scratch_database(base_dsn: str, name: str) -> str:
    admin = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(admin, name)
        await admin.execute(f"CREATE DATABASE {_quote_ident(name)}")
    finally:
        await admin.close()
    scratch_dsn = _database_dsn(base_dsn, name)
    conn = await asyncpg.connect(scratch_dsn, server_settings={"search_path": "pmfi,public"})
    try:
        for path in sorted((ROOT / "sql").glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()
    return scratch_dsn


async def _list_alert_eval_scratch_databases(base_dsn: str) -> list[str]:
    admin = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        rows = await admin.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE 'pmfi_alert_eval_%' ORDER BY datname"
        )
        return [str(row["datname"]) for row in rows]
    finally:
        await admin.close()


async def _cleanup_scratch_database(base_dsn: str, name: str) -> None:
    admin = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(admin, name)
    finally:
        await admin.close()


async def _seed_alert_eval_rows(pool) -> None:
    async with pool.acquire() as conn:
        market_id = await conn.fetchval(
            """INSERT INTO markets (venue_code, venue_market_id, title, status)
               VALUES ('polymarket', 'pm-alert-eval-market', 'Alert Eval Market', 'open')
               RETURNING market_id"""
        )
        await conn.executemany(
            """INSERT INTO normalized_trades
               (venue_code, venue_trade_id, market_id, outcome_key, price, contracts, received_at)
               VALUES ('polymarket', $1, $2::uuid, 'yes', $3, 1, $4)""",
            [
                ("t0", market_id, "0.40", _ts(0)),
                ("t1", market_id, "0.47", _ts(60)),
                ("t2", market_id, "0.43", _ts(80)),
                ("t3", market_id, "0.55", _ts(300)),
            ],
        )
        await conn.executemany(
            """INSERT INTO alerts
               (dedupe_key, rule_key, rule_version, venue_code, market_id, outcome_key,
                severity, confidence, title, summary, fired_at)
               VALUES ($1, $2, 'v1', 'polymarket', $3::uuid, 'yes',
                       'low', 'medium', $4, $5, $6)""",
            [
                (f"alert-eval-{uuid.uuid4().hex}", "volume_spike_v1", market_id, "hit", "proxy hit", _ts(0)),
                (f"alert-eval-{uuid.uuid4().hex}", "volume_spike_v1", market_id, "miss", "proxy miss", _ts(20)),
            ],
        )


def test_alert_precision_measurement_reads_scratch_db_and_cleans_up() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.alert_precision import run_alert_precision_measurement

    async def _run() -> None:
        base_dsn = _dsn()
        scratch_name = f"pmfi_alert_eval_p{os.getpid()}_{uuid.uuid4().hex[:8]}"
        scratch_dsn = await _create_scratch_database(base_dsn, scratch_name)
        pool = await create_pool(scratch_dsn, min_size=1, max_size=1)
        try:
            await _seed_alert_eval_rows(pool)
            evidence = await run_alert_precision_measurement(pool, MANIFEST)

            assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
            assert evidence["scenario_id"] == "M-TRUTH-v2-MEASURE"
            assert evidence["outcome"] == "PASS", {
                "fail_conditions": evidence["fail_conditions"],
                "pass_invariants": evidence["pass_invariants"],
                "measurements": evidence["measurements"],
            }
            assert set(evidence["evidence"]["actual_facets"]) == {
                "OFFLINE",
                "POSTGRES_INTEGRATION",
                "READ_ONLY_PRIMARY",
            }
            assert evidence["completeness_classifications"]["precision"] == "PROXY_BACKTEST_LOCAL"
            measurements = evidence["measurements"]
            assert measurements["alert_count"] == 2
            assert measurements["scorable_alerts"] > 0
            assert measurements["price_source"] == "normalized_trades"
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            grid = {
                (row["rule_key"], row["window_seconds"], row["threshold"]): row
                for row in measurements["per_rule_grid"]
            }
            assert grid[("volume_spike_v1", 60, 0.05)]["proxy_hits"] == 1
            assert grid[("volume_spike_v1", 60, 0.05)]["proxy_misses"] == 1
        finally:
            await pool.close()
            await _cleanup_scratch_database(base_dsn, scratch_name)

        assert await _list_alert_eval_scratch_databases(base_dsn) == []

    asyncio.run(_run())
