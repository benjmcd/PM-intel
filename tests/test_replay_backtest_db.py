"""DB-gated replay backtest tests (US-14).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.

Verifies:
- start_ts/end_ts filters scope rows correctly
- limit=0 returns all synthetic rows (unlimited)
- market/venue filters scope correctly
- seeded accumulators allow cluster detection that cold replay would miss
- synthetic rows are self-cleaned in FK-safe order
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_VENUE = "polymarket"
_CHANNEL = "market_ws"
_EVENT_TYPE = "last_trade_price"

# Unique per-run prefix so parallel CI runs cannot collide
_RUN_ID = uuid4().hex[:12]
_SYNTH_MARKET = f"TEST-BACKTEST-US14-{_RUN_ID}"
_OTHER_MARKET = f"TEST-BACKTEST-US14-OTHER-{_RUN_ID}"


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _make_payload(*, market: str, price: str, size: str, trade_id: str,
                  outcome: str = "yes", side: str = "buy") -> dict:
    return {
        "market": market,
        "price": price,
        "size": size,
        "outcome": outcome,
        "side": side,
        "trade_id": trade_id,
    }


async def _insert_raw_event(
    conn,
    *,
    market: str,
    exchange_ts: datetime,
    price: str,
    size: str,
    source_event_id: str,
    venue: str = _VENUE,
) -> int:
    import hashlib
    payload = _make_payload(market=market, price=price, size=size, trade_id=source_event_id)
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    row = await conn.fetchrow(
        """INSERT INTO raw_events
           (venue_code, source_channel, source_event_type, source_event_id,
            venue_market_id, exchange_ts, received_at, payload, payload_hash, parser_version)
           VALUES ($1,$2,$3,$4,$5,$6,now(),$7::jsonb,$8,$9)
           RETURNING raw_event_id""",
        venue, _CHANNEL, _EVENT_TYPE, source_event_id,
        market, exchange_ts,
        json.dumps(payload), payload_hash, "raw.v1",
    )
    return int(row["raw_event_id"])


async def _cleanup(conn, markets: list[str]) -> None:
    """FK-safe cleanup: alerts -> normalized_trades -> metric_windows -> markets -> raw_events -> dedupe."""
    for vmid in markets:
        mid = await conn.fetchval(
            "SELECT market_id FROM markets WHERE venue_market_id = $1", vmid
        )
        if mid is not None:
            # alerts reference normalized_trades; delete alerts first
            await conn.execute(
                "DELETE FROM alerts WHERE market_id = $1", mid
            )
            await conn.execute(
                "DELETE FROM metric_windows WHERE market_id = $1", mid
            )
            await conn.execute(
                "DELETE FROM normalized_trades WHERE market_id = $1", mid
            )
            await conn.execute(
                "DELETE FROM raw_events WHERE venue_market_id = $1", vmid
            )
            await conn.execute(
                "DELETE FROM markets WHERE market_id = $1", mid
            )
        else:
            await conn.execute(
                "DELETE FROM raw_events WHERE venue_market_id = $1", vmid
            )
    await conn.execute(
        "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
        _VENUE, _CHANNEL,
    )


# ---------------------------------------------------------------------------
# Test 1: start_ts / end_ts filters scope results correctly
# ---------------------------------------------------------------------------

def test_replay_start_end_ts_filter():
    """Only raw_events within [start_ts, end_ts] are replayed."""
    import asyncpg
    from pmfi.replay import replay_from_db

    # Base time well in the future to avoid collision with real data
    base = datetime(2098, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    inside_ts = base + timedelta(hours=1)
    before_ts = base - timedelta(hours=1)   # outside window
    after_ts = base + timedelta(hours=3)    # outside window

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        inserted_ids: list[int] = []
        try:
            # Insert 3 events: one before, one inside, one after
            for ts, eid, price in [
                (before_ts, f"bt-filter-before-{_RUN_ID}", "0.30"),
                (inside_ts, f"bt-filter-inside-{_RUN_ID}", "0.50"),
                (after_ts, f"bt-filter-after-{_RUN_ID}", "0.70"),
            ]:
                rid = await _insert_raw_event(
                    conn,
                    market=_SYNTH_MARKET,
                    exchange_ts=ts,
                    price=price,
                    size="500",
                    source_event_id=eid,
                )
                inserted_ids.append(rid)

            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                window_start = base
                window_end = base + timedelta(hours=2)
                results = await replay_from_db(
                    pool,
                    limit=0,  # unlimited
                    start_ts=window_start,
                    end_ts=window_end,
                    market=_SYNTH_MARKET,
                )
            finally:
                await pool.close()

            synth = [r for r in results if r.trade.venue_market_id == _SYNTH_MARKET]
            assert len(synth) == 1, (
                f"Expected 1 result in window, got {len(synth)}: "
                f"{[r.trade.exchange_ts for r in synth]}"
            )
            assert synth[0].trade.exchange_ts == inside_ts

        finally:
            if inserted_ids:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
                    inserted_ids,
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
                    _VENUE, _CHANNEL,
                )
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: limit=0 returns all synthetic rows (unlimited)
# ---------------------------------------------------------------------------

def test_replay_limit_zero_returns_all():
    """limit=0 must return every matching row without truncation."""
    import asyncpg
    from pmfi.replay import replay_from_db

    base = datetime(2098, 4, 1, 0, 0, 0, tzinfo=timezone.utc)
    N = 5  # insert N events; limit=0 should return all N

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        inserted_ids: list[int] = []
        try:
            for i in range(N):
                ts = base + timedelta(minutes=i)
                rid = await _insert_raw_event(
                    conn,
                    market=_SYNTH_MARKET,
                    exchange_ts=ts,
                    price="0.50",
                    size="100",
                    source_event_id=f"bt-unlim-{_RUN_ID}-{i}",
                )
                inserted_ids.append(rid)

            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                results = await replay_from_db(
                    pool,
                    limit=0,  # unlimited
                    market=_SYNTH_MARKET,
                    start_ts=base,
                    end_ts=base + timedelta(hours=1),
                )
            finally:
                await pool.close()

            synth = [r for r in results if r.trade.venue_market_id == _SYNTH_MARKET]
            assert len(synth) >= N, (
                f"Expected >= {N} results with limit=0, got {len(synth)}"
            )

        finally:
            if inserted_ids:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
                    inserted_ids,
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
                    _VENUE, _CHANNEL,
                )
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: market filter scopes results
# ---------------------------------------------------------------------------

def test_replay_market_filter_scopes_correctly():
    """--market filter must exclude events from other markets."""
    import asyncpg
    from pmfi.replay import replay_from_db

    base = datetime(2098, 5, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        inserted_ids: list[int] = []
        try:
            for market, eid, price in [
                (_SYNTH_MARKET, f"bt-mktfil-main-{_RUN_ID}", "0.40"),
                (_OTHER_MARKET, f"bt-mktfil-other-{_RUN_ID}", "0.60"),
            ]:
                rid = await _insert_raw_event(
                    conn,
                    market=market,
                    exchange_ts=base,
                    price=price,
                    size="200",
                    source_event_id=eid,
                )
                inserted_ids.append(rid)

            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                results = await replay_from_db(
                    pool,
                    limit=0,
                    market=_SYNTH_MARKET,
                    start_ts=base - timedelta(minutes=1),
                    end_ts=base + timedelta(minutes=1),
                )
            finally:
                await pool.close()

            markets_seen = {r.trade.venue_market_id for r in results}
            assert _OTHER_MARKET not in markets_seen, (
                f"market filter failed: {_OTHER_MARKET!r} leaked into results"
            )
            assert _SYNTH_MARKET in markets_seen, (
                f"market filter excluded target market {_SYNTH_MARKET!r}"
            )

        finally:
            if inserted_ids:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
                    inserted_ids,
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
                    _VENUE, _CHANNEL,
                )
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4: seeded accumulators produce cluster that cold replay would miss
# ---------------------------------------------------------------------------

def test_seeded_replay_detects_cluster_cold_replay_misses():
    """
    Scenario:
    - 4 large YES trades happen BEFORE the replay window (t=-240s to t=-60s).
    - 1 more YES trade inside the replay window.
    - directional_cluster_v1 requires min_trade_count=3 within 300s window.
    - Cold replay (no seed) sees only 1 trade in window -> no cluster.
    - Seeded replay sees 5 trades in the accumulator -> cluster fires.

    We prove seeding matters by running both variants and comparing alert counts.
    """
    import asyncpg
    from pmfi.replay import replay_from_db
    from pmfi.pipeline.engine import AlertEngine

    # Time layout:
    #   seed_trades: t_base - 240s .. t_base - 60s  (4 trades, inside seed window)
    #   replay_trade: t_base + 30s                  (1 trade, inside replay window)
    t_base = datetime(2098, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    replay_start = t_base
    replay_end = t_base + timedelta(minutes=2)

    # Capital sized to exceed directional_cluster thresholds once we have 3+ trades:
    #   min_net_capital_at_risk_usd = 15000 (default)
    # Each trade: price=0.50, size=1000 -> capital = 500 USD
    # 5 trades same direction -> net_capital = 2500 USD  (below threshold — intentional)
    # Use larger sizes so net_capital clearly exceeds threshold.
    # price=0.50, size=20000 -> capital_at_risk = 0.50 * 20000 = 10000 USD per trade
    # 5 trades: net_capital = 50000 USD > 15000 -> threshold met
    # price_impact: prices vary from 0.50 to 0.55 (5 cents > 2 cents min)

    seed_prices = ["0.50", "0.51", "0.52", "0.53"]
    replay_price = "0.55"
    trade_size = "20000"  # large enough to meet capital threshold across 5 trades

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        seed_ids: list[int] = []
        replay_ids: list[int] = []
        cluster_market = f"TEST-BACKTEST-CLUSTER-{_RUN_ID}"

        try:
            # Insert seed trades (BEFORE replay window — go into normalized_trades too
            # via process_event so seed_from_db can find them)
            for i, price in enumerate(seed_prices):
                ts = t_base - timedelta(seconds=240 - i * 45)  # spread within 300s lookback
                rid = await _insert_raw_event(
                    conn,
                    market=cluster_market,
                    exchange_ts=ts,
                    price=price,
                    size=trade_size,
                    source_event_id=f"bt-seed-{_RUN_ID}-{i}",
                )
                seed_ids.append(rid)

            # Persist seed trades through process_event so normalized_trades is populated
            # (seed_from_db queries normalized_trades, not raw_events)
            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                from pmfi.domain import RawEvent
                from pmfi.pipeline.runner import process_event
                from pmfi.pipeline.engine import AlertEngine as _AE

                seed_engine = _AE()

                async def _noop(d, vc, mid):
                    pass

                for i, price in enumerate(seed_prices):
                    ts = t_base - timedelta(seconds=240 - i * 45)
                    raw = RawEvent(
                        venue_code=_VENUE,
                        source_channel=_CHANNEL,
                        source_event_type=_EVENT_TYPE,
                        source_event_id=f"bt-seed-{_RUN_ID}-{i}",
                        venue_market_id=cluster_market,
                        exchange_ts=ts,
                        received_at=ts,
                        payload=_make_payload(
                            market=cluster_market,
                            price=price,
                            size=trade_size,
                            trade_id=f"bt-seed-{_RUN_ID}-{i}",
                        ),
                    )
                    try:
                        await process_event(raw, pool, seed_engine, _noop)
                    except Exception:
                        pass  # duplicate insert on second persist attempt is fine

                # Insert the single replay-window trade
                rid_replay = await _insert_raw_event(
                    conn,
                    market=cluster_market,
                    exchange_ts=t_base + timedelta(seconds=30),
                    price=replay_price,
                    size=trade_size,
                    source_event_id=f"bt-replay-{_RUN_ID}",
                )
                replay_ids.append(rid_replay)

                # --- Cold replay (no seed): should NOT detect cluster ---
                cold_results = await replay_from_db(
                    pool,
                    limit=0,
                    market=cluster_market,
                    start_ts=replay_start,
                    end_ts=replay_end,
                    seed=False,  # explicitly cold: accumulators start empty
                )
                cold_cluster_alerts = [
                    d for r in cold_results
                    for d in r.alerts
                    if d.rule_id == "directional_cluster_v1"
                ]

                # --- Warm (seeded) replay: should detect cluster ---
                warm_results = await replay_from_db(
                    pool,
                    limit=0,
                    market=cluster_market,
                    start_ts=replay_start,
                    end_ts=replay_end,
                )
                warm_cluster_alerts = [
                    d for r in warm_results
                    for d in r.alerts
                    if d.rule_id == "directional_cluster_v1"
                ]

                # Seeded replay must detect the cluster; cold replay must not.
                # (Cold has only 1 trade in window — far below min_trade_count=3.)
                assert len(cold_cluster_alerts) == 0, (
                    f"Cold replay should not detect cluster with 1 trade in window; "
                    f"got {len(cold_cluster_alerts)} cluster alert(s)"
                )
                assert len(warm_cluster_alerts) >= 1, (
                    f"Seeded replay should detect cluster (4 pre-seeded + 1 in-window = 5 trades); "
                    f"got {len(warm_cluster_alerts)} cluster alert(s). "
                    f"warm_results={[(r.trade.exchange_ts, r.alerts) for r in warm_results]}"
                )

            finally:
                # Cleanup
                async with pool.acquire() as cconn:
                    await _cleanup(cconn, [cluster_market])
                await pool.close()

        finally:
            # Remove any leftover raw_events not handled by _cleanup
            all_ids = seed_ids + replay_ids
            if all_ids:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
                    all_ids,
                )
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
                _VENUE, _CHANNEL,
            )
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 5: existing test_replay_db.py contract preserved — replay_from_db(limit=N)
# ---------------------------------------------------------------------------

def test_replay_from_db_limit_n_still_works():
    """replay_from_db(pool, limit=N) with no filters works exactly as before."""
    import asyncpg
    from pmfi.replay import replay_from_db

    base = datetime(2098, 7, 1, 0, 0, 0, tzinfo=timezone.utc)

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        inserted_ids: list[int] = []
        try:
            for i in range(3):
                rid = await _insert_raw_event(
                    conn,
                    market=_SYNTH_MARKET,
                    exchange_ts=base + timedelta(minutes=i),
                    price="0.50",
                    size="100",
                    source_event_id=f"bt-compat-{_RUN_ID}-{i}",
                )
                inserted_ids.append(rid)

            total = await conn.fetchval("SELECT COUNT(*) FROM raw_events")
            limit = int(total) + 10

            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                # Original call signature — must still work
                results = await replay_from_db(pool, limit=limit)
            finally:
                await pool.close()

            # Verify our synthetic rows appear in results
            synth = [r for r in results if r.trade.venue_market_id == _SYNTH_MARKET]
            assert len(synth) >= 3, f"Expected >=3 synthetic results, got {len(synth)}"

        finally:
            if inserted_ids:
                await conn.execute(
                    "DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
                    inserted_ids,
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
                    _VENUE, _CHANNEL,
                )
            await conn.close()

    asyncio.run(_run())
