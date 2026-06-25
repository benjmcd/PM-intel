from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from db_scratch import (
    TESTISO_DB_PREFIX,
    ScratchDatabase,
    create_test_scratch_database,
    drop_test_scratch_database,
)


pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_SCRATCH_DB: ScratchDatabase | None = None


def _dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError(
            "operational dead-letter guards scratch DB was not initialized"
        )
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _operational_deadletter_guards_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("operational_deadletter_guards")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_operational_deadletter_guards_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(
        f"{TESTISO_DB_PREFIX}operational_deadletter_guards_"
    )
    assert _SCRATCH_DB.name in _dsn()


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _cleanup(conn, source_channel: str) -> None:
    await conn.execute("DELETE FROM dead_letters WHERE source_channel = $1", source_channel)
    await conn.execute("DELETE FROM raw_events WHERE source_channel = $1", source_channel)


async def _seed_raw_events(conn, source_channel: str, count: int) -> None:
    for idx in range(count):
        await conn.execute(
            """INSERT INTO raw_events
                 (venue_code, source_channel, source_event_type, source_event_id,
                  venue_market_id, received_at, payload)
               VALUES ('polymarket', $1, 'last_trade_price', $2, $3, now(), '{}'::jsonb)""",
            source_channel,
            f"{source_channel}-raw-{idx}",
            f"{source_channel}-market",
        )


async def _seed_dead_letters(
    conn,
    source_channel: str,
    count: int,
    *,
    resolved: bool = False,
) -> None:
    for idx in range(count):
        await conn.execute(
            """INSERT INTO dead_letters
                 (venue_code, source_channel, failure_stage, error_class,
                  error_message, payload, resolved, created_at)
               VALUES ('polymarket', $1, 'normalization', 'ops_guard_test',
                       $2, '{}'::jsonb, $3, now())""",
            source_channel,
            f"{source_channel}-dead-letter-{idx}",
            resolved,
        )


def test_dead_letter_rate_guard_degrades_and_surfaces_health_on_breach(tmp_path, capsys) -> None:
    from pmfi.commands.reporting import cmd_health
    from pmfi.db import create_pool
    from pmfi.health import write_heartbeat
    from pmfi.operational_health import DeadLetterRateGuard, OperationalHealthState

    async def run() -> dict:
        pool = await create_pool(_dsn())
        source_channel = f"ops-guards-dl-rate-{uuid4()}"
        try:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
                await _seed_raw_events(conn, source_channel, 1)
                await _seed_dead_letters(conn, source_channel, 1)
            state = OperationalHealthState()
            guard = DeadLetterRateGuard(threshold_fraction=0.0, lookback_seconds=3600)
            return await guard.evaluate(pool, state)
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
            await pool.close()

    snapshot = asyncio.run(run())
    assert snapshot["status"] == "DEGRADED"
    assert snapshot["intake_allowed"] is True
    assert snapshot["reasons"][0]["reason"] == "dead_letter_rate_high"
    assert snapshot["reasons"][0]["observed"]["dead_letters_1h"] >= 1

    hb_path = tmp_path / "heartbeat.json"
    write_heartbeat(
        hb_path,
        events_total=0,
        alerts_total=0,
        started_at=_now(),
        now=_now(),
        operational_health=snapshot,
    )
    rc = cmd_health(
        SimpleNamespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=999999999,
            json_output=False,
            venue_stale_seconds=None,
        )
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "operational=DEGRADED" in out
    assert "dead_letter_rate_high" in out


def test_dead_letter_rate_guard_stays_ok_when_below_threshold() -> None:
    from pmfi.db import create_pool
    from pmfi.operational_health import DeadLetterRateGuard, OperationalHealthState

    async def run() -> dict:
        pool = await create_pool(_dsn())
        source_channel = f"ops-guards-dl-rate-ok-{uuid4()}"
        try:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
                await _seed_raw_events(conn, source_channel, 1)
                await _seed_dead_letters(conn, source_channel, 1)
            state = OperationalHealthState()
            guard = DeadLetterRateGuard(threshold_fraction=999.0, lookback_seconds=3600)
            return await guard.evaluate(pool, state)
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
            await pool.close()

    snapshot = asyncio.run(run())
    assert snapshot["status"] == "OK"
    assert snapshot["reasons"] == []


def test_unresolved_dead_letter_guard_halts_and_surfaces_health_on_breach(tmp_path, capsys) -> None:
    from pmfi.commands.reporting import cmd_health
    from pmfi.db import create_pool
    from pmfi.health import write_heartbeat
    from pmfi.operational_health import OperationalHealthState, UnresolvedDeadLetterHaltGuard

    async def run() -> dict:
        pool = await create_pool(_dsn())
        source_channel = f"ops-guards-dl-unresolved-{uuid4()}"
        try:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
                await _seed_dead_letters(conn, source_channel, 1)
            state = OperationalHealthState()
            guard = UnresolvedDeadLetterHaltGuard(max_unresolved=0)
            return await guard.evaluate(pool, state)
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
            await pool.close()

    snapshot = asyncio.run(run())
    assert snapshot["status"] == "HALTED"
    assert snapshot["intake_allowed"] is False
    assert snapshot["reasons"][0]["reason"] == "unresolved_dead_letters_over_cap"
    assert snapshot["reasons"][0]["observed"]["unresolved_dead_letters"] >= 1

    hb_path = tmp_path / "heartbeat.json"
    write_heartbeat(
        hb_path,
        events_total=0,
        alerts_total=0,
        started_at=_now(),
        now=_now(),
        operational_health=snapshot,
    )
    rc = cmd_health(
        SimpleNamespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=999999999,
            json_output=False,
            venue_stale_seconds=None,
        )
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "operational=HALTED" in out
    assert "unresolved_dead_letters_over_cap" in out
    assert "intake_paused=true" in out


def test_unresolved_dead_letter_guard_stays_ok_when_below_threshold() -> None:
    from pmfi.db import create_pool
    from pmfi.operational_health import OperationalHealthState, UnresolvedDeadLetterHaltGuard

    async def run() -> dict:
        pool = await create_pool(_dsn())
        source_channel = f"ops-guards-dl-unresolved-ok-{uuid4()}"
        try:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
                await _seed_dead_letters(conn, source_channel, 1)
            state = OperationalHealthState()
            guard = UnresolvedDeadLetterHaltGuard(max_unresolved=1_000_000_000)
            return await guard.evaluate(pool, state)
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, source_channel)
            await pool.close()

    snapshot = asyncio.run(run())
    assert snapshot["status"] == "OK"
    assert snapshot["reasons"] == []
