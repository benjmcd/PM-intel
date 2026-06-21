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


def test_polymarket_ws_raw_before_derived_and_trade_normalizes():
    from pmfi.db import create_pool
    from pmfi.domain import RawEvent
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline

    run_id = uuid4().hex[:12]
    venue_market_id = f"PM-INGEST-TEST-{run_id}"
    trade_id = f"pm-ingest-trade-{run_id}"
    asset_id = f"pm-ingest-token-{run_id}"

    price_change = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="price_change",
        source_event_id=None,
        venue_market_id=venue_market_id,
        exchange_ts=None,
        payload={
            "event_type": "price_change",
            "market": venue_market_id,
            "asset_id": asset_id,
            "price": "0.51",
            "size": "10",
        },
    )
    trade = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=trade_id,
        venue_market_id=venue_market_id,
        exchange_ts=None,
        payload={
            "event_type": "trade",
            "id": trade_id,
            "market": venue_market_id,
            "asset_id": asset_id,
            "outcome": "yes",
            "price": "0.65",
            "size": "50000",
            "side": "buy",
        },
    )

    async def _events():
        for event in (price_change, trade):
            yield event

    async def _noop_handler(_decision, _venue_code, _market_id) -> None:
        return None

    async def _run():
        pool = await create_pool(_dsn())
        try:
            processed = await run_adapter_pipeline(
                _events(),
                pool,
                AlertEngine(),
                _noop_handler,
                max_events=2,
            )
            assert processed == 2

            async with pool.acquire() as conn:
                raw_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE venue_code='polymarket' AND venue_market_id=$1",
                    venue_market_id,
                )
                trade_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM normalized_trades WHERE venue_code='polymarket' AND venue_trade_id=$1",
                    trade_id,
                )
                normalized_for_market = await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM normalized_trades nt
                       JOIN markets m ON m.market_id = nt.market_id
                       WHERE m.venue_market_id=$1""",
                    venue_market_id,
                )
                dead_letter_count = await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM dead_letters dl
                       JOIN raw_events re ON re.raw_event_id = dl.raw_event_id
                       WHERE re.venue_market_id=$1""",
                    venue_market_id,
                )

            assert raw_count == 2
            assert trade_count == 1
            assert normalized_for_market == 1
            assert dead_letter_count == 0
        finally:
            async with pool.acquire() as conn:
                market_id = await conn.fetchval(
                    "SELECT market_id FROM markets WHERE venue_market_id=$1",
                    venue_market_id,
                )
                if market_id is not None:
                    await conn.execute("DELETE FROM alerts WHERE market_id=$1", market_id)
                    await conn.execute("DELETE FROM metric_windows WHERE market_id=$1", market_id)
                    await conn.execute("DELETE FROM normalized_trades WHERE market_id=$1", market_id)
                raw_ids = await conn.fetch(
                    "SELECT raw_event_id FROM raw_events WHERE venue_market_id=$1",
                    venue_market_id,
                )
                if raw_ids:
                    id_list = [row["raw_event_id"] for row in raw_ids]
                    await conn.execute(
                        "DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])",
                        id_list,
                    )
                    await conn.execute(
                        "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
                        id_list,
                    )
                await conn.execute("DELETE FROM raw_events WHERE venue_market_id=$1", venue_market_id)
                if market_id is not None:
                    await conn.execute("DELETE FROM markets WHERE market_id=$1", market_id)
            await pool.close()

    asyncio.run(_run())
