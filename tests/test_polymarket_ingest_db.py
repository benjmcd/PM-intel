"""DB-gated Polymarket live-WS-path integration test.

Proves the raw-before-derived contract for the live Polymarket feed: a non-trade
WS event (price_change / book — the high-rate majority of the public CLOB stream)
is persisted to raw_events (so the ingest-rate view counts it) but is NOT
normalized; a trade event is persisted AND normalized into normalized_trades.

Skips when PMFI_DB_URL is unset, so the default offline verify stays green.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_polymarket_raw_before_derived_and_trade_normalizes():
    """Non-trade WS event -> raw_events only; trade event -> raw_events + normalized_trades."""
    from pmfi.db import create_pool
    from pmfi.domain import RawEvent
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline

    market = f"0xITEST{uuid4().hex[:16]}"
    trade_id = f"pm-itest-{uuid4().hex[:12]}"
    asset = f"tok{uuid4().hex[:12]}"

    # A non-trade event (price_change) — the high-rate majority of the public WS feed.
    price_change = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="price_change",
        source_event_id=None,  # no stable id -> dedup falls back to payload hash
        venue_market_id=market,
        exchange_ts=None,
        payload={"event_type": "price_change", "market": market, "asset_id": asset,
                 "price": "0.51", "size": "10"},
    )
    # A real trade event (last_trade_price/trade shape).
    trade = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=trade_id,
        venue_market_id=market,
        exchange_ts=None,
        payload={"event_type": "trade", "id": trade_id, "market": market, "asset_id": asset,
                 "outcome": "Yes", "price": "0.65", "size": "50000", "side": "BUY"},
    )

    async def _src():
        for ev in (price_change, trade):
            yield ev

    async def _noop_handler(decision, venue_code, market_id) -> None:
        pass

    async def _run():
        pool = await create_pool(_dsn())
        try:
            engine = AlertEngine()
            # asset_id_map omitted (None): payloads already carry market + outcome, so
            # no asset-id resolution / dead-letter path is exercised here.
            count = await run_adapter_pipeline(_src(), pool, engine, _noop_handler, max_events=2)
            assert count == 2, f"expected 2 events processed, got {count}"

            async with pool.acquire() as conn:
                raw_n = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE venue_code='polymarket' AND venue_market_id=$1",
                    market,
                )
                nt_trade = await conn.fetchval(
                    "SELECT COUNT(*) FROM normalized_trades WHERE venue_code='polymarket' AND venue_trade_id=$1",
                    trade_id,
                )
                nt_total = await conn.fetchval(
                    "SELECT COUNT(*) FROM normalized_trades nt JOIN markets m ON m.market_id=nt.market_id "
                    "WHERE m.venue_market_id=$1",
                    market,
                )
            # raw-before-derived: BOTH events persisted to raw_events
            assert raw_n == 2, f"expected 2 raw_events (raw-before-derived), got {raw_n}"
            # the trade normalized
            assert nt_trade == 1, f"expected the trade in normalized_trades, got {nt_trade}"
            # ONLY the trade normalized — the price_change was skipped, not normalized
            assert nt_total == 1, f"expected ONLY the trade normalized (price_change skipped), got {nt_total}"
        finally:
            async with pool.acquire() as conn:
                mid = await conn.fetchval("SELECT market_id FROM markets WHERE venue_market_id=$1", market)
                if mid is not None:
                    await conn.execute("DELETE FROM alerts WHERE market_id=$1", mid)
                    await conn.execute("DELETE FROM metric_windows WHERE market_id=$1", mid)
                    await conn.execute("DELETE FROM normalized_trades WHERE market_id=$1", mid)
                raw_ids = await conn.fetch(
                    "SELECT raw_event_id FROM raw_events WHERE venue_market_id=$1", market
                )
                if raw_ids:
                    ids = [r["raw_event_id"] for r in raw_ids]
                    await conn.execute(
                        "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])", ids
                    )
                await conn.execute("DELETE FROM raw_events WHERE venue_market_id=$1", market)
                if mid is not None:
                    await conn.execute("DELETE FROM markets WHERE market_id=$1", mid)
            await pool.close()

    asyncio.run(_run())
