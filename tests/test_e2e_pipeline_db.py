"""DB-gated end-to-end pipeline integration test.

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.

Drives a small stub async adapter (yields two synthetic RawEvents for an isolated
synthetic market) through run_adapter_pipeline, then asserts:
  - raw_events + normalized_trades rows created for the synthetic market
  - at least one alert fired (trade sized above large_trade_absolute_v1 thresholds)
  - recent_alerts / volume_timeseries / feed_health each surface the synthetic data

All seeded rows are cleaned in FK-safe order in the finally block.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import AsyncIterator
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

# Clearly-synthetic prefix so cleanup queries are precise and cannot accidentally
# touch real production data.
_SYNTH_PREFIX = "E2E-PIPELINE-SYNTH"
_VENUE = "polymarket"
_CHANNEL = "market_ws"
_EVENT_TYPE = "last_trade_price"
_SCRATCH_DB: ScratchDatabase | None = None


def _get_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("e2e pipeline scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _e2e_pipeline_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("e2e")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_e2e_pipeline_uses_scratch_db_not_configured_primary():
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}e2e_")
    assert _SCRATCH_DB.name in _get_dsn()


# ---------------------------------------------------------------------------
# Stub adapter: yields a fixed list of RawEvents then stops
# ---------------------------------------------------------------------------

async def _stub_events(events: list) -> AsyncIterator:
    """Minimal stub adapter — yields pre-built RawEvents then exhausts."""
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_e2e_pipeline_full_path():
    """Stub adapter -> run_adapter_pipeline -> DB; assert full lineage + alerts."""
    import asyncpg
    from pmfi.db import create_pool
    from pmfi.domain import RawEvent
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline
    from pmfi.dashboard.queries import recent_alerts, volume_timeseries, feed_health

    # Unique synthetic identifiers for this run — uuid hex ensures no collision.
    run_id = uuid4().hex[:16]
    synth_market = f"{_SYNTH_PREFIX}-{run_id}"
    trade_id_a = f"e2e-trade-a-{run_id}"
    trade_id_b = f"e2e-trade-b-{run_id}"

    # Size both trades well above large_trade_absolute_v1:
    #   min_capital_at_risk_usd=25000, min_payout_notional_usd=100000
    # price=0.25, contracts=120000 → capital_at_risk_usd = 0.25*120000 = 30000 ✓
    #                               payout_notional_usd = 120000 ✓
    now = datetime.now(tz=timezone.utc)

    raw_events = [
        RawEvent(
            venue_code=_VENUE,
            source_channel=_CHANNEL,
            source_event_type=_EVENT_TYPE,
            source_event_id=trade_id_a,
            venue_market_id=synth_market,
            exchange_ts=now,
            payload={
                "trade_id": trade_id_a,
                "market": synth_market,
                "outcome": "yes",
                "side": "buy",
                "price": "0.25",
                "size": "120000",
            },
        ),
        RawEvent(
            venue_code=_VENUE,
            source_channel=_CHANNEL,
            source_event_type=_EVENT_TYPE,
            source_event_id=trade_id_b,
            venue_market_id=synth_market,
            exchange_ts=now,
            payload={
                "trade_id": trade_id_b,
                "market": synth_market,
                "outcome": "yes",
                "side": "buy",
                "price": "0.26",
                "size": "120000",
            },
        ),
    ]

    async def _noop_handler(decision, venue_code, market_id) -> None:
        pass

    async def _run():
        pool = await create_pool(_get_dsn())
        try:
            engine = AlertEngine()

            processed = await run_adapter_pipeline(
                _stub_events(raw_events),
                pool,
                engine,
                _noop_handler,
            )

            assert processed == 2, f"Expected 2 events processed, got {processed}"

            async with pool.acquire() as conn:
                # --- raw_events created ---
                raw_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE venue_market_id = $1",
                    synth_market,
                )
                assert int(raw_count) == 2, (
                    f"Expected 2 raw_events for {synth_market!r}, got {raw_count}"
                )

                # --- normalized_trades created ---
                trade_count = await conn.fetchval(
                    """SELECT COUNT(*) FROM normalized_trades t
                       JOIN markets m ON t.market_id = m.market_id
                       WHERE m.venue_market_id = $1""",
                    synth_market,
                )
                assert int(trade_count) == 2, (
                    f"Expected 2 normalized_trades for {synth_market!r}, got {trade_count}"
                )

                # --- at least one alert fired for the synthetic market ---
                alert_count = await conn.fetchval(
                    """SELECT COUNT(*) FROM alerts a
                       JOIN markets m ON a.market_id = m.market_id
                       WHERE m.venue_market_id = $1""",
                    synth_market,
                )
                assert int(alert_count) >= 1, (
                    f"Expected >=1 alert for {synth_market!r}, got {alert_count}. "
                    "Trade was sized above large_trade_absolute_v1 threshold "
                    "(capital=30000 >= 25000, payout=120000 >= 100000)."
                )

                # --- recent_alerts surfaces the synthetic market ---
                alerts_out = await recent_alerts(conn, limit=100)
                synth_alerts = [a for a in alerts_out if a["venue_market_id"] == synth_market]
                assert synth_alerts, (
                    f"recent_alerts did not return any rows for {synth_market!r}; "
                    f"returned {[a['venue_market_id'] for a in alerts_out[:5]]}"
                )

                # --- volume_timeseries surfaces the synthetic trades ---
                vol = await volume_timeseries(conn, lookback_minutes=60)
                # capital_at_risk for trade_a = 0.25 * 120000 = 30000
                # capital_at_risk for trade_b = 0.26 * 120000 = 31200
                # combined synthetic floor = 61200; assert >= floor
                synth_vol = [v for v in vol if v["venue_code"] == _VENUE and v["trades"] >= 1]
                # More precisely: find bucket where volume accounts for our synthetic trades
                # We check floor assertion: there exists a bucket with volume >= 30000 (one trade)
                assert any(v["volume_usd"] >= 30000.0 for v in synth_vol), (
                    f"No polymarket volume bucket >= 30000; got {synth_vol[:5]}"
                )

                # --- feed_health reports the synthetic venue ---
                health = await feed_health(conn)
                by_venue = {h["venue_code"]: h for h in health}
                assert _VENUE in by_venue, f"{_VENUE} not in feed_health: {health}"
                # Use >= so co-mingled real events do not break the assertion
                assert by_venue[_VENUE]["events_5m"] >= 2, by_venue[_VENUE]

        finally:
            # FK-safe self-clean: delete child rows before parents.
            # Order: alerts, metric_windows, normalized_trades, dead_letters,
            #        raw_events, event_dedupe_keys, markets.
            async with pool.acquire() as conn:
                market_id = await conn.fetchval(
                    "SELECT market_id FROM markets WHERE venue_market_id = $1",
                    synth_market,
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

                # Remove dedupe keys before raw_events (FK on first_raw_event_id)
                raw_ids = await conn.fetch(
                    "SELECT raw_event_id FROM raw_events WHERE venue_market_id = $1",
                    synth_market,
                )
                if raw_ids:
                    id_list = [r["raw_event_id"] for r in raw_ids]
                    await conn.execute(
                        "DELETE FROM event_dedupe_keys "
                        "WHERE first_raw_event_id = ANY($1::bigint[])",
                        id_list,
                    )

                await conn.execute(
                    "DELETE FROM dead_letters WHERE venue_code = $1 "
                    "AND raw_event_id IN "
                    "(SELECT raw_event_id FROM raw_events WHERE venue_market_id = $2)",
                    _VENUE, synth_market,
                )
                await conn.execute(
                    "DELETE FROM raw_events WHERE venue_market_id = $1", synth_market
                )

                if market_id is not None:
                    await conn.execute(
                        "DELETE FROM markets WHERE market_id = $1", market_id
                    )

            await pool.close()

    asyncio.run(_run())
