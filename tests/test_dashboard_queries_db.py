"""DB-gated contract test for the dashboard rate/volume query layer.

Skips without PMFI_DB_URL so the default offline verify stays green. Seeds synthetic
raw_events / metric_windows / dead_letters, asserts the per-venue aggregates, and
cleans up all synthetic rows.
"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_feed_health_and_volume_timeseries():
    import asyncpg
    from pmfi.dashboard.queries import feed_health, volume_timeseries

    poly_mkt = f"0xDASHTEST{uuid4().hex[:12]}"
    kalshi_tkr = f"KS-DASHTEST-{uuid4().hex[:8]}"
    dl_msg = f"dashtest-{uuid4().hex[:10]}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        mid = None
        try:
            mid = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'dashtest', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at=now()
                   RETURNING market_id""",
                poly_mkt,
            )
            # raw_events: 2 polymarket non-trade events + 1 kalshi, all "now"
            for et in ("book", "price_change"):
                await conn.execute(
                    """INSERT INTO raw_events (venue_code, source_channel, source_event_type, venue_market_id, received_at, payload)
                       VALUES ('polymarket','ws_clob',$1,$2, now(), $3::jsonb)""",
                    et, poly_mkt, json.dumps({"event_type": et, "market": poly_mkt}),
                )
            await conn.execute(
                """INSERT INTO raw_events (venue_code, source_channel, source_event_type, venue_market_id, received_at, payload)
                   VALUES ('kalshi','rest_trades','trade',$1, now(), $2::jsonb)""",
                kalshi_tkr, json.dumps({"ticker": kalshi_tkr}),
            )
            # metric_windows: one polymarket bucket (trades=7, volume=1234.50)
            await conn.execute(
                """INSERT INTO metric_windows (market_id, venue_code, window_start, window_seconds, trade_count, gross_capital_at_risk_usd, sample_size)
                   VALUES ($1, 'polymarket', now(), 300, 7, 1234.50, 7)""",
                mid,
            )
            # unresolved polymarket dead-letter
            await conn.execute(
                """INSERT INTO dead_letters (venue_code, failure_stage, error_class, error_message, resolved, created_at)
                   VALUES ('polymarket','normalization','dashtest',$1, false, now())""",
                dl_msg,
            )

            health = await feed_health(conn)
            by_venue = {h["venue_code"]: h for h in health}
            assert "polymarket" in by_venue, f"polymarket missing: {health}"
            p = by_venue["polymarket"]
            assert p["events_60s"] >= 2, p
            assert p["events_5m"] >= 2, p
            assert p["last_event_age_s"] is not None and p["last_event_age_s"] < 120, p
            assert p["unresolved_dead_letters_1h"] >= 1, p
            assert "kalshi" in by_venue and by_venue["kalshi"]["events_60s"] >= 1, health

            vol = await volume_timeseries(conn, lookback_minutes=60)
            poly_buckets = [v for v in vol if v["venue_code"] == "polymarket" and v["trades"] >= 7]
            assert poly_buckets, f"expected a polymarket bucket with trades>=7: {vol[:5]}"
            assert any(abs(v["volume_usd"] - 1234.50) < 0.01 for v in poly_buckets), poly_buckets
        finally:
            if mid:
                await conn.execute("DELETE FROM metric_windows WHERE market_id=$1", mid)
            await conn.execute(
                "DELETE FROM raw_events WHERE venue_market_id = ANY($1::text[])",
                [poly_mkt, kalshi_tkr],
            )
            await conn.execute("DELETE FROM dead_letters WHERE error_message=$1", dl_msg)
            if mid:
                await conn.execute("DELETE FROM markets WHERE market_id=$1", mid)
            await conn.close()

    asyncio.run(_run())
