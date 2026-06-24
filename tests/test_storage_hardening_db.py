"""DB-gated storage hardening tests.

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.

Tests:
  1. insert_trade populates outcome_id FK from market_outcomes when a matching
     (market_id, outcome_key) row exists.
  2. idx_metric_windows_market_window index exists in pg_indexes (on the parent
     table or on a partition).

All seeded rows are self-cleaned in FK-safe order.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from datetime import datetime, timezone

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
_SYNTH_PREFIX = "STORAGE-HARDENING-SYNTH"
_SCRATCH_DB: ScratchDatabase | None = None


def _get_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("storage hardening scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _storage_hardening_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("storage")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_storage_hardening_uses_scratch_db_not_configured_primary():
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}storage_")
    assert _SCRATCH_DB.name in _get_dsn()


# ---------------------------------------------------------------------------
# Test 1: outcome_id is populated on insert_trade
# ---------------------------------------------------------------------------

def test_insert_trade_populates_outcome_id():
    """insert_trade must resolve and store outcome_id from market_outcomes."""
    import asyncpg
    from pmfi.db.repos.markets import upsert_market, upsert_market_outcome
    from pmfi.db.repos.raw_events import insert_raw_event
    from pmfi.db.repos.trades import insert_trade
    from pmfi.domain import NormalizedTrade, RawEvent

    run_id = os.urandom(6).hex()
    synth_market = f"{_SYNTH_PREFIX}-OID-{run_id}"
    synth_trade_id = f"hardening-oid-trade-{run_id}"

    now = datetime.now(tz=timezone.utc)

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        market_id = None
        raw_event_id = None
        trade_id_str = None
        try:
            # Seed isolated market
            market_id = await upsert_market(
                conn,
                venue_code=_VENUE,
                venue_market_id=synth_market,
                title=synth_market,
            )

            # Seed market_outcome for outcome_key="yes"
            outcome_id = await upsert_market_outcome(
                conn,
                market_id=market_id,
                venue_code=_VENUE,
                venue_outcome_id=f"tok-{run_id}-yes",
                outcome_key="yes",
                outcome_label="Yes",
                is_binary=True,
            )

            # Seed a raw_event so insert_trade has a valid raw_event_id
            raw = RawEvent(
                venue_code=_VENUE,
                source_channel="market_ws",
                source_event_type="last_trade_price",
                source_event_id=synth_trade_id,
                venue_market_id=synth_market,
                exchange_ts=now,
                received_at=now,
                payload={
                    "market": synth_market,
                    "price": "0.55",
                    "size": "1000",
                    "outcome": "yes",
                    "side": "buy",
                    "trade_id": synth_trade_id,
                },
            )
            raw_event_id, _ = await insert_raw_event(conn, raw)

            trade = NormalizedTrade(
                venue_code=_VENUE,
                venue_market_id=synth_market,
                venue_trade_id=synth_trade_id,
                outcome_key="yes",
                aggressor_side="buy",
                directional_side="yes",
                side_confidence="high",
                price=Decimal("0.55"),
                contracts=Decimal("1000"),
                capital_at_risk_usd=Decimal("550.0"),
                payout_notional_usd=Decimal("1000.0"),
                exchange_ts=now,
                received_at=now,
                source_payload=raw.payload,
            )

            trade_id_str = await insert_trade(
                conn, trade, raw_event_id=raw_event_id, market_id=market_id
            )
            assert trade_id_str is not None, "insert_trade must return a trade_id"

            # Assert outcome_id is populated and matches the seeded outcome
            row = await conn.fetchrow(
                "SELECT outcome_id::text FROM normalized_trades WHERE trade_id=$1::uuid",
                trade_id_str,
            )
            assert row is not None, "inserted trade row not found"
            stored_outcome_id = row["outcome_id"]
            assert stored_outcome_id is not None, (
                "outcome_id must be populated on insert_trade when a matching "
                "market_outcomes row exists; got NULL"
            )
            assert stored_outcome_id == outcome_id, (
                f"outcome_id mismatch: stored={stored_outcome_id!r}, "
                f"expected={outcome_id!r}"
            )

        finally:
            # FK-safe self-clean
            if market_id is not None:
                await conn.execute(
                    "DELETE FROM normalized_trades WHERE market_id=$1::uuid", market_id
                )
                await conn.execute(
                    "DELETE FROM market_outcomes WHERE market_id=$1::uuid", market_id
                )
            if raw_event_id is not None:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id=$1", raw_event_id
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys WHERE venue_code=$1 AND source_channel=$2 "
                    "AND first_raw_event_id=$3",
                    _VENUE, "market_ws", raw_event_id,
                )
            if market_id is not None:
                await conn.execute(
                    "DELETE FROM markets WHERE market_id=$1::uuid", market_id
                )
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: idx_metric_windows_market_window exists in pg_indexes
# ---------------------------------------------------------------------------

def test_idx_metric_windows_market_window_exists():
    """idx_metric_windows_market_window must exist on metric_windows or a partition."""
    import asyncpg

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            # Check for the index on the parent table or any partition.
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM pg_indexes
                WHERE schemaname = current_schema()
                  AND indexname = 'idx_metric_windows_market_window'
                """,
            )
            assert int(count) >= 1, (
                "idx_metric_windows_market_window not found in pg_indexes. "
                "Run 'python scripts/db_local.py init' or ensure startup migrations ran."
            )
        finally:
            await conn.close()

    asyncio.run(_run())
