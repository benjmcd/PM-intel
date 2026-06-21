"""DB-gated Kalshi REST polling ingest integration test.

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.
Proves the full path: KalshiRestPollingAdapter.events() -> run_adapter_pipeline
-> Postgres (raw_events + normalized_trades persisted), and that a repeated poll
of the same trade is deduplicated at the storage layer.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _make_kalshi_trade(*, ticker: str, trade_id: str, created_time: str) -> dict:
    """Return a real-shaped Kalshi REST trade dict for the synthetic ticker."""
    return {
        "trade_id": trade_id,
        "ticker": ticker,
        # count_fp: string decimal contracts
        "count_fp": "10",
        # yes_price_dollars: already in [0,1] per normalizer comments
        "yes_price_dollars": "0.91",
        "no_price_dollars": "0.09",
        "taker_side": "yes",
        "created_time": created_time,
    }


def test_kalshi_ingest_persists_and_deduplicates():
    """Full-path ingest: adapter -> pipeline -> DB, then repeat proves dedup."""
    import asyncpg
    from pmfi.db import create_pool
    from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
    from pmfi.db.repos.raw_events import _compute_dedupe_key
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline

    ticker = f"KS-ITEST-{uuid4().hex[:10]}"
    trade_id = f"kalshi-itest-{uuid4().hex[:12]}"
    created_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    trade_dict = _make_kalshi_trade(
        ticker=ticker,
        trade_id=trade_id,
        created_time=created_time,
    )
    raw_dedupe_key = _compute_dedupe_key("kalshi", "rest_trades", trade_id, "")

    async def _noop_handler(decision, venue_code, market_id) -> None:
        pass

    async def _run():
        pool = await create_pool(_get_dsn())
        try:
            # --- Test 1: first poll persists raw_events + normalized_trades ---
            adapter = KalshiRestPollingAdapter(
                tickers=[ticker],
                poll_interval_seconds=0.01,
            )
            await adapter.connect()
            engine = AlertEngine()

            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                new_callable=AsyncMock,
                return_value=[trade_dict],
            ), patch(
                "pmfi.adapters.kalshi_rest.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                count = await run_adapter_pipeline(
                    adapter.events(),
                    pool,
                    engine,
                    _noop_handler,
                    max_events=1,
                )

            assert count == 1, f"Expected 1 event processed, got {count}"

            # Verify raw_events row was created for this source_event_id
            async with pool.acquire() as conn:
                raw_row = await conn.fetchrow(
                    "SELECT raw_event_id FROM raw_events "
                    "WHERE venue_code = 'kalshi' AND source_event_id = $1",
                    trade_id,
                )
            assert raw_row is not None, (
                f"No raw_events row found for kalshi trade_id={trade_id!r}"
            )
            raw_event_id = raw_row["raw_event_id"]

            # Verify normalized_trades row with expected price and contracts
            async with pool.acquire() as conn:
                trade_row = await conn.fetchrow(
                    "SELECT price, contracts FROM normalized_trades "
                    "WHERE venue_code = 'kalshi' AND venue_trade_id = $1",
                    trade_id,
                )
            assert trade_row is not None, (
                f"No normalized_trades row for kalshi venue_trade_id={trade_id!r}"
            )
            # yes_price_dollars=0.91 -> price should be ~0.91
            price = trade_row["price"]
            assert abs(float(price) - 0.91) < 0.001, (
                f"Expected price ~0.91, got {price!r}"
            )
            # count_fp=10 -> contracts should be 10
            contracts = trade_row["contracts"]
            assert float(contracts) == 10.0, (
                f"Expected contracts=10, got {contracts!r}"
            )

            # --- Test 2: repeat poll deduplicates at storage layer ---
            adapter2 = KalshiRestPollingAdapter(
                tickers=[ticker],
                poll_interval_seconds=0.01,
            )
            await adapter2.connect()
            engine2 = AlertEngine()

            with patch(
                "pmfi.adapters.kalshi_rest.fetch_kalshi_trades",
                new_callable=AsyncMock,
                return_value=[trade_dict],
            ), patch(
                "pmfi.adapters.kalshi_rest.asyncio.sleep",
                new_callable=AsyncMock,
            ):
                count2 = await run_adapter_pipeline(
                    adapter2.events(),
                    pool,
                    engine2,
                    _noop_handler,
                    max_events=1,
                )

            # Pipeline still observes one event from the repeated poll; storage
            # dedupe is proven separately by the unchanged normalized_trades count.
            assert count2 == 1
            async with pool.acquire() as conn:
                nt_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM normalized_trades "
                    "WHERE venue_code = 'kalshi' AND venue_trade_id = $1",
                    trade_id,
                )
            assert int(nt_count) == 1, (
                f"Storage dedup failed: expected 1 normalized_trades row after "
                f"repeated poll, got {nt_count}"
            )

            # Verify event_dedupe_keys duplicate_count incremented
            async with pool.acquire() as conn:
                dup_count = await conn.fetchval(
                    "SELECT duplicate_count FROM event_dedupe_keys "
                    "WHERE venue_code = 'kalshi' AND source_channel = 'rest_trades' "
                    "AND first_raw_event_id = $1",
                    raw_event_id,
                )
            assert dup_count is not None and int(dup_count) >= 1, (
                f"Expected duplicate_count >= 1 in event_dedupe_keys, got {dup_count!r}"
            )

        finally:
            # Clean up ALL synthetic rows in FK-safe order
            async with pool.acquire() as conn:
                # Resolve market_id for the synthetic ticker
                market_id = await conn.fetchval(
                    "SELECT market_id FROM markets WHERE venue_market_id = $1",
                    ticker,
                )
                if market_id is not None:
                    await conn.execute(
                        "DELETE FROM alerts WHERE market_id = $1", market_id
                    )
                    await conn.execute(
                        "DELETE FROM metric_windows WHERE market_id = $1", market_id
                    )
                    await conn.execute(
                        "DELETE FROM normalized_trades WHERE market_id = $1", market_id
                    )
                # Remove raw_events and dedupe keys for this ticker
                raw_ids = await conn.fetch(
                    "SELECT raw_event_id FROM raw_events WHERE venue_market_id = $1",
                    ticker,
                )
                if raw_ids:
                    id_list = [r["raw_event_id"] for r in raw_ids]
                    await conn.execute(
                        "DELETE FROM event_dedupe_keys "
                        "WHERE first_raw_event_id = ANY($1::bigint[])",
                        id_list,
                    )
                await conn.execute(
                    "DELETE FROM raw_events WHERE venue_market_id = $1", ticker
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys "
                    "WHERE dedupe_key = $1",
                    raw_dedupe_key,
                )
                if market_id is not None:
                    await conn.execute(
                        "DELETE FROM markets WHERE market_id = $1", market_id
                    )
            await pool.close()

    asyncio.run(_run())
