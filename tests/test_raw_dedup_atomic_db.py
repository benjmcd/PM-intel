"""DB-gated tests for atomic raw_events dedup (US-02).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.
Tests isolate synthetic data under a unique source_event_id / market prefix rooted
in an existing venue ('polymarket') and self-clean all rows they create.

Tests:
  1. Concurrent insert_raw_event: 10 parallel calls with identical dedupe inputs
     produce EXACTLY one raw_events row.
  2. Null-venue_trade_id trade insert: inserting the same null-id trade twice
     yields exactly 1 normalized_trades row and unchanged metric_windows counters.
  3. Concurrent duplicate venue_trade_id inserts are admitted once by the DB guard.
  4. Concurrent duplicate null-id fingerprints are admitted once by the DB guard.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

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

_VENUE = "polymarket"
_CHANNEL = "market_ws"
_EVENT_TYPE = "last_trade_price"
# Far-future timestamps make the synthetic rows easy to identify and avoid
# colliding with real data windows.
_FAR_FUTURE_TS = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
# Unique synthetic market ID used by both tests — distinct enough to avoid
# production-data collision while remaining within varchar limits.
_SYNTHETIC_MARKET = "TEST-ATOMIC-DEDUP-US02-SYNTHETIC"
# source_event_id used for the concurrent dedup test.
_DEDUP_SOURCE_EVENT_ID = "us02-atomic-dedup-test-event-001"
_SCRATCH_DB: ScratchDatabase | None = None


def _get_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("raw dedup atomic scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _raw_dedup_atomic_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("raw_dedup_atomic")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_raw_dedup_atomic_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}raw_dedup_atomic_")
    assert _SCRATCH_DB.name in _get_dsn()


# ---------------------------------------------------------------------------
# Helper: build a RawEvent domain object
# ---------------------------------------------------------------------------

def _make_raw_event(source_event_id: str = _DEDUP_SOURCE_EVENT_ID) -> "RawEvent":  # noqa: F821
    from pmfi.domain import RawEvent

    return RawEvent(
        venue_code=_VENUE,
        source_channel=_CHANNEL,
        source_event_type=_EVENT_TYPE,
        source_event_id=source_event_id,
        venue_market_id=_SYNTHETIC_MARKET,
        exchange_ts=_FAR_FUTURE_TS,
        received_at=_FAR_FUTURE_TS,
        payload={
            "market": _SYNTHETIC_MARKET,
            "price": "0.55",
            "size": "100",
            "outcome": "yes",
            "side": "buy",
            "trade_id": source_event_id,
        },
    )


# ---------------------------------------------------------------------------
# Helper: build a NormalizedTrade domain object (null venue_trade_id)
# ---------------------------------------------------------------------------

def _make_null_id_trade() -> "NormalizedTrade":  # noqa: F821
    from pmfi.domain import NormalizedTrade

    return NormalizedTrade(
        venue_code=_VENUE,
        venue_market_id=_SYNTHETIC_MARKET,
        venue_trade_id=None,  # the null-id case under test
        outcome_key="yes",
        aggressor_side="buy",
        directional_side="yes",
        side_confidence="high",
        price=Decimal("0.55"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("55.0"),
        payout_notional_usd=Decimal("100.0"),
        exchange_ts=_FAR_FUTURE_TS,
        received_at=_FAR_FUTURE_TS,
        source_payload={"market": _SYNTHETIC_MARKET, "price": "0.55"},
    )


def _make_venue_id_trade(venue_trade_id: str) -> "NormalizedTrade":  # noqa: F821
    from pmfi.domain import NormalizedTrade

    return NormalizedTrade(
        venue_code=_VENUE,
        venue_market_id=_SYNTHETIC_MARKET,
        venue_trade_id=venue_trade_id,
        outcome_key="yes",
        aggressor_side="buy",
        directional_side="yes",
        side_confidence="high",
        price=Decimal("0.55"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("55.0"),
        payout_notional_usd=Decimal("100.0"),
        exchange_ts=_FAR_FUTURE_TS,
        received_at=_FAR_FUTURE_TS,
        source_payload={
            "market": _SYNTHETIC_MARKET,
            "price": "0.55",
            "trade_id": venue_trade_id,
        },
    )


# ---------------------------------------------------------------------------
# Cleanup helper — FK-safe deletion order
# ---------------------------------------------------------------------------

async def _cleanup(conn: "asyncpg.Connection") -> None:  # noqa: F821
    # Resolve the synthetic market UUID (may not exist if test failed early).
    market_id = await conn.fetchval(
        "SELECT market_id FROM markets WHERE venue_market_id = $1",
        _SYNTHETIC_MARKET,
    )
    if market_id is not None:
        await conn.execute(
            "DELETE FROM metric_windows WHERE market_id = $1", market_id
        )
        has_trade_guard = await conn.fetchval(
            "SELECT to_regclass('pmfi.normalized_trade_dedupe_keys') IS NOT NULL"
        )
        if has_trade_guard:
            await conn.execute(
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = $1",
                market_id,
            )
        await conn.execute(
            "DELETE FROM normalized_trades WHERE market_id = $1", market_id
        )

    # Raw events for this synthetic market (all of them, any source_event_id).
    raw_ids = await conn.fetch(
        "SELECT raw_event_id FROM raw_events WHERE venue_market_id = $1",
        _SYNTHETIC_MARKET,
    )
    if raw_ids:
        ids = [r["raw_event_id"] for r in raw_ids]
        await conn.execute(
            "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
            ids,
        )
        await conn.execute(
            "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
            ids,
        )
    # Catch any dangling dedupe_keys whose raw row was never written (e.g. early failure).
    await conn.execute(
        "DELETE FROM event_dedupe_keys "
        "WHERE venue_code = $1 AND source_channel = $2 "
        "  AND first_raw_event_id IS NULL",
        _VENUE, _CHANNEL,
    )

    if market_id is not None:
        await conn.execute(
            "DELETE FROM markets WHERE market_id = $1", market_id
        )


# ---------------------------------------------------------------------------
# Test 1: concurrent insert_raw_event with identical key → exactly 1 raw row
# ---------------------------------------------------------------------------

def test_concurrent_raw_event_insert_atomic():
    """10 concurrent insert_raw_event calls with the same dedupe key must produce
    exactly 1 raw_events row and return is_duplicate=True for the 9 losers.

    NOTE: we do NOT assert that all callers return the same raw_event_id value.
    The winner inserts the raw row then UPDATEs event_dedupe_keys.first_raw_event_id
    in a second statement; a duplicate caller that races between those two statements
    may observe first_raw_event_id=NULL and therefore return a different (or None)
    id.  The core atomicity invariant — exactly ONE raw_events row exists — is
    verified by the DB COUNT check below, which is what matters.
    """
    import asyncpg
    from pmfi.db.repos.raw_events import insert_raw_event

    async def _run() -> None:
        pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=5, max_size=15,
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            raw = _make_raw_event(_DEDUP_SOURCE_EVENT_ID)

            # Fire 10 concurrent inserts for the same event.
            async def _one_insert() -> tuple[int, bool]:
                async with pool.acquire() as conn:
                    return await insert_raw_event(conn, raw)

            results = await asyncio.gather(*[_one_insert() for _ in range(10)])

            # Exactly one must be non-duplicate.
            non_dupes = [r for r in results if not r[1]]
            dupes = [r for r in results if r[1]]
            assert len(non_dupes) == 1, (
                f"Expected exactly 1 non-duplicate insert, got {len(non_dupes)}. "
                f"Results: {results}"
            )
            assert len(dupes) == 9, (
                f"Expected 9 duplicates, got {len(dupes)}. Results: {results}"
            )

            # Exactly 1 raw_events row in the DB for this source_event_id.
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events "
                    "WHERE venue_code = $1 AND source_event_id = $2",
                    _VENUE, _DEDUP_SOURCE_EVENT_ID,
                )
            assert count == 1, (
                f"Expected exactly 1 raw_events row, found {count}. "
                "The check-then-insert TOCTOU race likely survived."
            )

        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: null-venue_trade_id trade insert is idempotent
# ---------------------------------------------------------------------------

def test_null_venue_trade_id_insert_is_idempotent():
    """Inserting the same null-venue_trade_id trade twice must yield exactly 1
    normalized_trades row and leave metric_windows count unchanged after the
    second insert."""
    import asyncpg
    from pmfi.db.repos.markets import upsert_market
    from pmfi.db.repos.raw_events import insert_raw_event
    from pmfi.db.repos.trades import insert_trade
    from pmfi.db.repos.metrics import upsert_metric_window

    # Use a distinct source_event_id for the raw event so it doesn't collide
    # with the concurrent test's event (both run in the same DB session when
    # PMFI_DB_URL is set).
    _RAW_EVENT_ID = "us02-null-trade-raw-event-001"

    async def _run() -> None:
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            raw = _make_raw_event(_RAW_EVENT_ID)
            raw_event_id, _ = await insert_raw_event(conn, raw)

            market_id = await upsert_market(
                conn,
                venue_code=_VENUE,
                venue_market_id=_SYNTHETIC_MARKET,
                title=_SYNTHETIC_MARKET,
            )
            trade = _make_null_id_trade()

            # First insert — must succeed.
            trade_id_1 = await insert_trade(
                conn, trade, raw_event_id=raw_event_id, market_id=market_id
            )
            assert trade_id_1 is not None, (
                "First insert of null-id trade must return a trade_id, got None"
            )
            await upsert_metric_window(conn, trade, market_id=market_id, window_seconds=300)

            # Capture metric_windows count before second insert attempt.
            metrics_before = await conn.fetchval(
                "SELECT COUNT(*) FROM metric_windows WHERE market_id = $1", market_id
            )

            # Second insert — must be detected as duplicate and return None.
            trade_id_2 = await insert_trade(
                conn, trade, raw_event_id=raw_event_id, market_id=market_id
            )
            assert trade_id_2 is None, (
                f"Second insert of same null-id trade must return None (duplicate), "
                f"got {trade_id_2!r}"
            )

            # Caller respects None by skipping upsert_metric_window — simulate that.
            # (We do NOT call upsert_metric_window here, mirroring runner.py behavior.)

            # Exactly 1 normalized_trades row must exist for this market.
            trade_count = await conn.fetchval(
                "SELECT COUNT(*) FROM normalized_trades WHERE market_id = $1", market_id
            )
            assert trade_count == 1, (
                f"Expected exactly 1 normalized_trades row, found {trade_count}. "
                "Null-id trade dedup did not fire on second insert."
            )

            # metric_windows count must be unchanged after the skipped second insert.
            metrics_after = await conn.fetchval(
                "SELECT COUNT(*) FROM metric_windows WHERE market_id = $1", market_id
            )
            assert metrics_after == metrics_before, (
                f"metric_windows changed from {metrics_before} to {metrics_after} "
                "after a skipped (duplicate) null-id trade — double-count bug."
            )

        finally:
            await _cleanup(conn)
            await conn.close()

    asyncio.run(_run())


async def _wait_for_snapshots(
    conn: "asyncpg.Connection",  # noqa: F821
    ready: dict[str, int],
    lock: "asyncio.Lock",
    release: "asyncio.Event",
    expected: int,
) -> None:
    # Establish each repeatable-read snapshot before releasing any writer.
    await conn.fetchval("SELECT 1")
    async with lock:
        ready["count"] += 1
        if ready["count"] == expected:
            release.set()
    await release.wait()


def test_concurrent_venue_trade_id_insert_uses_db_guard():
    """Concurrent duplicate venue_trade_id inserts must create one trade row.

    The transaction snapshots are intentionally established before any writer is
    released. A SELECT-before-INSERT dedupe path therefore sees an empty table in
    every worker and double-inserts; a DB guard admits only one writer.
    """
    import asyncpg
    from pmfi.db.repos.markets import upsert_market
    from pmfi.db.repos.raw_events import insert_raw_event
    from pmfi.db.repos.trades import insert_trade

    workers = 8
    raw_event_id_value = "us02-venue-id-guard-raw-event-001"
    venue_trade_id = "us02-venue-id-guard-trade-001"

    async def _run() -> None:
        pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=workers,
            max_size=workers + 2,
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            async with pool.acquire() as conn:
                raw = _make_raw_event(raw_event_id_value)
                raw_event_id, _ = await insert_raw_event(conn, raw)
                market_id = await upsert_market(
                    conn,
                    venue_code=_VENUE,
                    venue_market_id=_SYNTHETIC_MARKET,
                    title=_SYNTHETIC_MARKET,
                )

            trade = _make_venue_id_trade(venue_trade_id)
            ready = {"count": 0}
            lock = asyncio.Lock()
            release = asyncio.Event()

            async def _one_insert() -> str | None:
                async with pool.acquire() as conn:
                    async with conn.transaction(isolation="repeatable_read"):
                        await _wait_for_snapshots(conn, ready, lock, release, workers)
                        return await insert_trade(
                            conn,
                            trade,
                            raw_event_id=raw_event_id,
                            market_id=market_id,
                        )

            results = await asyncio.gather(*[_one_insert() for _ in range(workers)])
            winners = [result for result in results if result is not None]
            duplicates = [result for result in results if result is None]
            assert len(winners) == 1, f"expected one inserted trade, got {results}"
            assert len(duplicates) == workers - 1, (
                f"duplicate callers must receive None, got {results}"
            )

            async with pool.acquire() as conn:
                trade_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM normalized_trades "
                    "WHERE venue_code = $1 AND venue_trade_id = $2",
                    _VENUE,
                    venue_trade_id,
                )
            assert trade_count == 1, (
                f"expected one normalized_trades row, found {trade_count}"
            )
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn)
            await pool.close()

    asyncio.run(_run())


def test_concurrent_null_id_fingerprint_insert_uses_db_guard():
    """Concurrent duplicate null-id fingerprint inserts must create one trade row."""
    import asyncpg
    from pmfi.db.repos.markets import upsert_market
    from pmfi.db.repos.raw_events import insert_raw_event
    from pmfi.db.repos.trades import insert_trade

    workers = 8
    raw_event_id_value = "us02-null-id-guard-raw-event-001"

    async def _run() -> None:
        pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=workers,
            max_size=workers + 2,
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            async with pool.acquire() as conn:
                raw = _make_raw_event(raw_event_id_value)
                raw_event_id, _ = await insert_raw_event(conn, raw)
                market_id = await upsert_market(
                    conn,
                    venue_code=_VENUE,
                    venue_market_id=_SYNTHETIC_MARKET,
                    title=_SYNTHETIC_MARKET,
                )

            trade = _make_null_id_trade()
            ready = {"count": 0}
            lock = asyncio.Lock()
            release = asyncio.Event()

            async def _one_insert() -> str | None:
                async with pool.acquire() as conn:
                    async with conn.transaction(isolation="repeatable_read"):
                        await _wait_for_snapshots(conn, ready, lock, release, workers)
                        return await insert_trade(
                            conn,
                            trade,
                            raw_event_id=raw_event_id,
                            market_id=market_id,
                        )

            results = await asyncio.gather(*[_one_insert() for _ in range(workers)])
            winners = [result for result in results if result is not None]
            duplicates = [result for result in results if result is None]
            assert len(winners) == 1, f"expected one inserted trade, got {results}"
            assert len(duplicates) == workers - 1, (
                f"duplicate callers must receive None, got {results}"
            )

            async with pool.acquire() as conn:
                trade_count = await conn.fetchval(
                    """SELECT COUNT(*) FROM normalized_trades
                       WHERE venue_code = $1
                         AND market_id = $2::uuid
                         AND exchange_ts IS NOT DISTINCT FROM $3
                         AND price = $4
                         AND contracts = $5
                         AND outcome_key = $6
                         AND venue_trade_id IS NULL""",
                    _VENUE,
                    market_id,
                    trade.exchange_ts,
                    trade.price,
                    trade.contracts,
                    trade.outcome_key,
                )
            assert trade_count == 1, (
                f"expected one normalized_trades row, found {trade_count}"
            )
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn)
            await pool.close()

    asyncio.run(_run())
