"""DB-gated test: compute_and_store_baselines writes to market_baselines and
load_baselines returns the stored values.

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.

Insert synthetic market + normalized_trades rows with known capital_at_risk_usd,
call compute_and_store_baselines, then assert load_baselines returns the expected
p99 for that market. Clean up ALL synthetic rows FK-safely in a finally block.
"""
from __future__ import annotations

import asyncio
import os
import uuid
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

_VENUE = "kalshi"
_MIN_SAMPLES = 2
_SCRATCH_DB: ScratchDatabase | None = None


def _get_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("baselines store scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _baselines_store_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("baselines_store")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_baselines_store_uses_scratch_db_not_configured_primary():
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}baselines_store_")
    assert _SCRATCH_DB.name in _get_dsn()


def test_compute_and_store_baselines_roundtrip():
    """Insert synthetic trades, compute+store baselines, assert load_baselines picks them up."""
    import asyncpg
    from pmfi.db import create_pool
    from pmfi.baseline import compute_and_store_baselines, load_baselines

    # Use a unique venue_market_id so parallel runs don't collide.
    venue_market_id = f"KX-BSTEST-{uuid.uuid4().hex[:10]}"
    # Capital values chosen so p99 of [100, 200, 300, 500, 1000] is easily bounded.
    # With 5 samples p99 ~ 1000 (last value), p99.5 ~ 1000.
    capital_values = [100.0, 200.0, 300.0, 500.0, 1000.0]

    async def _run():
        pool = await create_pool(_get_dsn())
        market_id = None
        inserted_trade_ids: list[str] = []

        try:
            async with pool.acquire() as conn:
                # --- Insert synthetic market ---
                row = await conn.fetchrow(
                    """
                    INSERT INTO pmfi.markets (venue_code, venue_market_id, title, status)
                    VALUES ($1, $2, 'Baselines store test market', 'active')
                    RETURNING market_id::text
                    """,
                    _VENUE,
                    venue_market_id,
                )
                market_id = row["market_id"]

                # --- Insert synthetic normalized_trades rows ---
                # received_at must be recent so the window_days=30 filter includes them.
                received_at = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
                for cap in capital_values:
                    trade_uuid = str(uuid.uuid4())
                    inserted_trade_ids.append(trade_uuid)
                    await conn.execute(
                        """
                        INSERT INTO pmfi.normalized_trades (
                            trade_id, venue_code, venue_trade_id, market_id,
                            outcome_key, aggressor_side, directional_side,
                            price, contracts, capital_at_risk_usd,
                            received_at, normalization_version, source_payload
                        ) VALUES (
                            $1::uuid, $2, $3, $4::uuid,
                            'yes', 'unknown', 'unknown',
                            0.50, 10.0, $5,
                            $6, 'trade.v1', '{}'::jsonb
                        )
                        """,
                        trade_uuid, _VENUE, f"vt-{trade_uuid[:12]}", market_id,
                        cap, received_at,
                    )

            # --- Compute and store baselines (the function under test) ---
            result = await compute_and_store_baselines(
                pool, window_days=30, min_samples=_MIN_SAMPLES
            )

            # The function must return an entry for our synthetic market.
            key = f"{_VENUE}:{venue_market_id}"
            assert key in result, (
                f"compute_and_store_baselines did not return entry for {key!r}. "
                f"Keys returned: {list(result.keys())[:10]}"
            )
            entry = result[key]
            assert entry["sample_size"] == len(capital_values), (
                f"Expected sample_size={len(capital_values)}, got {entry['sample_size']}"
            )
            # p99 of [100,200,300,500,1000] must be >= 500 and <= 1000
            p99 = entry["p99_trade_usd"]
            assert 500.0 <= p99 <= 1000.0, (
                f"p99_trade_usd={p99} out of expected range [500, 1000]"
            )

            # --- Verify load_baselines returns the stored entry ---
            loaded = await load_baselines(pool)
            assert key in loaded, (
                f"load_baselines did not return entry for {key!r} after store. "
                f"Keys: {list(loaded.keys())[:10]}"
            )
            loaded_entry = loaded[key]
            loaded_p99 = loaded_entry.get("p99_trade_usd")
            assert loaded_p99 is not None, "p99_trade_usd is None in loaded baseline"
            assert abs(float(loaded_p99) - p99) < 0.01, (
                f"Loaded p99={loaded_p99} differs from computed p99={p99}"
            )

        finally:
            # Clean up ALL synthetic rows in FK-safe order.
            async with pool.acquire() as conn:
                if market_id:
                    await conn.execute(
                        "DELETE FROM pmfi.market_baselines WHERE market_id = $1::uuid",
                        market_id,
                    )
                    await conn.execute(
                        "DELETE FROM pmfi.normalized_trades WHERE market_id = $1::uuid",
                        market_id,
                    )
                    await conn.execute(
                        "DELETE FROM pmfi.markets WHERE market_id = $1::uuid",
                        market_id,
                    )
            await pool.close()

    asyncio.run(_run())
