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
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from pmfi.commands._shared import is_loopback_db_url
from pmfi.qualification.soak_stability import _drop_database, _init_schema, _quote_ident

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
_REPLAYBT_DB_PREFIX = "pmfi_replaybt_"
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SCRATCH_DSN: str | None = None
_SCRATCH_DB_NAME: str | None = None


def _get_dsn() -> str:
    if _SCRATCH_DSN is None:
        raise RuntimeError("replay backtest scratch DB was not initialized")
    return _SCRATCH_DSN


def _configured_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _admin_dsn(base_dsn: str) -> str:
    if not is_loopback_db_url(base_dsn):
        raise RuntimeError("replay backtest DB tests require a loopback PMFI_DB_URL")
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _scratch_database_name() -> str:
    name = f"{_REPLAYBT_DB_PREFIX}p{os.getpid()}_{uuid4().hex[:8]}"
    if not name.startswith(_REPLAYBT_DB_PREFIX) or not _DB_NAME_RE.fullmatch(name):
        raise RuntimeError(f"unsafe replay backtest scratch database name: {name!r}")
    return name


def _ensure_replaybt_database(name: str) -> None:
    if not name.startswith(_REPLAYBT_DB_PREFIX) or not _DB_NAME_RE.fullmatch(name):
        raise RuntimeError("target must be a replay-backtest scratch database named pmfi_replaybt_*")


async def _create_scratch_database(base_dsn: str, name: str) -> str:
    _ensure_replaybt_database(name)
    conn = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(conn, name)
        await conn.execute(f"CREATE DATABASE {_quote_ident(name)}")
    finally:
        await conn.close()
    scratch_dsn = _database_dsn(base_dsn, name)
    await _init_schema(scratch_dsn)
    return scratch_dsn


async def _drop_scratch_database(base_dsn: str, name: str) -> None:
    _ensure_replaybt_database(name)
    conn = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(conn, name)
    finally:
        await conn.close()


async def _list_replaybt_scratch_databases(base_dsn: str) -> list[str]:
    conn = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE 'pmfi_replaybt_%' ORDER BY datname"
        )
        return [str(row["datname"]) for row in rows]
    finally:
        await conn.close()


@pytest.fixture(scope="module", autouse=True)
def _replay_backtest_scratch_database():
    global _SCRATCH_DSN, _SCRATCH_DB_NAME  # noqa: PLW0603
    base_dsn = _configured_dsn()
    scratch_name = _scratch_database_name()
    _SCRATCH_DB_NAME = scratch_name
    _SCRATCH_DSN = asyncio.run(_create_scratch_database(base_dsn, scratch_name))
    try:
        yield
    finally:
        asyncio.run(_drop_scratch_database(base_dsn, scratch_name))
        _SCRATCH_DSN = None
        _SCRATCH_DB_NAME = None


def test_replay_backtest_uses_scratch_db_not_configured_primary():
    """DB-gated replay backtest tests must not write to configured primary."""
    configured = os.environ["PMFI_DB_URL"]

    assert _get_dsn() != configured
    assert "pmfi_replaybt_" in _get_dsn()
    assert _SCRATCH_DB_NAME is not None
    assert _SCRATCH_DB_NAME.startswith(_REPLAYBT_DB_PREFIX)


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
    outcome: str = "yes",
    side: str = "buy",
) -> int:
    payload = _make_payload(
        market=market,
        price=price,
        size=size,
        trade_id=source_event_id,
        outcome=outcome,
        side=side,
    )
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


def _event_dedupe_key(source_event_id: str, *, venue: str = _VENUE) -> str:
    return hashlib.sha256(f"{venue}:{_CHANNEL}:{source_event_id}".encode()).hexdigest()


async def _delete_event_dedupe_keys(conn, source_event_ids: list[str]) -> None:
    if not source_event_ids:
        return
    keys = [_event_dedupe_key(source_event_id) for source_event_id in source_event_ids]
    await conn.execute("DELETE FROM event_dedupe_keys WHERE dedupe_key = ANY($1::text[])", keys)


async def _delete_raw_events_and_dedupe(conn, raw_event_ids: list[int]) -> None:
    if not raw_event_ids:
        return
    rows = await conn.fetch(
        "SELECT source_event_id FROM raw_events WHERE raw_event_id = ANY($1::bigint[])",
        raw_event_ids,
    )
    source_event_ids = [str(row["source_event_id"]) for row in rows if row["source_event_id"]]
    await conn.execute("DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])", raw_event_ids)
    await _delete_event_dedupe_keys(conn, source_event_ids)


async def _cleanup(conn, markets: list[str]) -> None:
    """FK-safe cleanup: alerts -> normalized_trades -> metric_windows -> markets -> raw_events -> synthetic dedupe."""
    for vmid in markets:
        event_rows = await conn.fetch(
            "SELECT source_event_id FROM raw_events WHERE venue_market_id = $1",
            vmid,
        )
        source_event_ids = [
            str(row["source_event_id"]) for row in event_rows if row["source_event_id"]
        ]
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
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = $1", mid
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
        await _delete_event_dedupe_keys(conn, source_event_ids)


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
                await _delete_raw_events_and_dedupe(conn, inserted_ids)
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
                await _delete_raw_events_and_dedupe(conn, inserted_ids)
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
                await _delete_raw_events_and_dedupe(conn, inserted_ids)
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
                    await process_event(raw, pool, seed_engine, _noop)

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
                await _delete_raw_events_and_dedupe(conn, all_ids)
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 5 (new): persist=True path seeds accumulators and detects cluster
# ---------------------------------------------------------------------------

def test_persist_replay_seeds_accumulators_and_detects_cluster():
    """
    Proves the fix: replay_from_db(persist=True, seed=True) seeds accumulators so
    cluster/momentum/volume_spike fire where a cold persist run would miss them.

    Setup:
    - 4 large YES trades inserted directly into normalized_trades BEFORE replay window
      (bypassing process_event to avoid setup-phase alerts polluting counts).
    - 1 in-window raw_event inserted for the replay run.

    Phase 1 — cold persist (seed=False):
      - replay_from_db processes 1 in-window trade with fresh engine -> no cluster.
      - Alerts written during this exact phase == 0 (scoped by fired_at timestamp).

    Phase 2 — warm persist (seed=True, after resetting dedup + clearing phase-1 data):
      - seed_from_db loads 4 pre-window normalized_trades into engine accumulators.
      - process_event sees 5 accumulated trades -> cluster fires.
      - Alerts written during this exact phase >= 1.

    Cleanup removes all synthetic data in FK-safe order.
    """
    import asyncpg
    from pmfi.replay import replay_from_db

    t_base = datetime(2098, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    replay_start = t_base
    replay_end = t_base + timedelta(minutes=2)

    seed_prices = ["0.50", "0.51", "0.52", "0.53"]
    replay_price = "0.55"
    trade_size = "20000"

    persist_market = f"TEST-BACKTEST-PERSIST-SEED-{_RUN_ID}"

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        replay_raw_ids: list[int] = []

        try:
            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=3,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                # Ensure market row exists first (needed for FK in normalized_trades).
                from pmfi.db.repos.markets import upsert_market
                async with pool.acquire() as _mc:
                    mid = await upsert_market(
                        _mc, venue_code=_VENUE, venue_market_id=persist_market, title=None
                    )

                # Insert seed trades DIRECTLY into normalized_trades (bypasses
                # process_event so no setup-phase alerts are written to DB).
                for i, price in enumerate(seed_prices):
                    ts = t_base - timedelta(seconds=240 - i * 45)
                    eid = f"bt-pseed-{_RUN_ID}-{i}"
                    # Insert raw_event first (normalized_trades FK requires it)
                    rid = await _insert_raw_event(
                        conn,
                        market=persist_market,
                        exchange_ts=ts,
                        price=price,
                        size=trade_size,
                        source_event_id=eid,
                    )
                    # Insert directly into normalized_trades using insert_trade
                    # via a minimal NormalizedTrade so all schema constraints are met.
                    from pmfi.domain import NormalizedTrade as _NT
                    from decimal import Decimal as _D
                    _trade = _NT(
                        venue_code=_VENUE,
                        venue_market_id=persist_market,
                        venue_trade_id=eid,
                        outcome_key="yes",
                        aggressor_side="unknown",
                        directional_side="yes",
                        side_confidence="high",
                        price=_D(price),
                        contracts=_D(trade_size),
                        capital_at_risk_usd=_D(price) * _D(trade_size),
                        payout_notional_usd=_D(trade_size),
                        exchange_ts=ts,
                        received_at=ts,
                    )
                    from pmfi.db.repos.trades import insert_trade as _it
                    async with pool.acquire() as _tc:
                        await _it(_tc, _trade, raw_event_id=rid, market_id=str(mid))

                # Insert the single in-window raw_event
                rid_replay = await _insert_raw_event(
                    conn,
                    market=persist_market,
                    exchange_ts=t_base + timedelta(seconds=30),
                    price=replay_price,
                    size=trade_size,
                    source_event_id=f"bt-preplay-{_RUN_ID}",
                )
                replay_raw_ids.append(rid_replay)

                # ── Phase 1: cold persist (seed=False) ──
                # Record timestamp just before the replay call so we can scope
                # the alert count to only alerts fired during this exact run.
                cold_before = await conn.fetchval("SELECT now()")
                await replay_from_db(
                    pool,
                    limit=0,
                    market=persist_market,
                    start_ts=replay_start,
                    end_ts=replay_end,
                    persist=True,
                    seed=False,
                )
                cold_alert_count = await conn.fetchval(
                    """SELECT COUNT(*) FROM alerts a
                       JOIN markets m ON a.market_id = m.market_id
                       WHERE m.venue_market_id = $1
                         AND a.rule_key = 'directional_cluster_v1'
                         AND a.fired_at >= $2""",
                    persist_market, cold_before,
                )
                assert int(cold_alert_count) == 0, (
                    f"Cold persist replay should not write cluster alert (1 in-window trade "
                    f"with cold engine); got {cold_alert_count} alert(s)"
                )

                # ── Reset between phases ──
                # Remove the in-window normalized_trade and dedup key so the warm
                # replay can re-process the same raw_event via process_event.
                await conn.execute(
                    "DELETE FROM normalized_trades WHERE market_id = $1 "
                    "AND COALESCE(exchange_ts, received_at) >= $2",
                    mid, replay_start,
                )
                await conn.execute(
                    "DELETE FROM alerts WHERE market_id = $1::uuid",
                    str(mid),
                )
                # Remove the raw-event dedupe key so the warm replay can re-process.
                _replay_eid = f"bt-preplay-{_RUN_ID}"
                await conn.execute(
                    "DELETE FROM normalized_trade_dedupe_keys "
                    "WHERE venue_code = $1 AND venue_trade_id = $2",
                    _VENUE,
                    _replay_eid,
                )
                await _delete_event_dedupe_keys(conn, [_replay_eid])

                # ── Phase 2: warm persist (seed=True) ──
                warm_before = await conn.fetchval("SELECT now()")
                await replay_from_db(
                    pool,
                    limit=0,
                    market=persist_market,
                    start_ts=replay_start,
                    end_ts=replay_end,
                    persist=True,
                    seed=True,
                )
                warm_alert_count = await conn.fetchval(
                    """SELECT COUNT(*) FROM alerts a
                       JOIN markets m ON a.market_id = m.market_id
                       WHERE m.venue_market_id = $1
                         AND a.rule_key = 'directional_cluster_v1'
                         AND a.fired_at >= $2""",
                    persist_market, warm_before,
                )
                assert int(warm_alert_count) >= 1, (
                    f"Seeded persist replay should write cluster alert "
                    f"(4 pre-seeded + 1 in-window = 5 trades); got {warm_alert_count} alert(s)"
                )

            finally:
                async with pool.acquire() as cconn:
                    await _cleanup(cconn, [persist_market])
                await pool.close()

        finally:
            if replay_raw_ids:
                await _delete_raw_events_and_dedupe(conn, replay_raw_ids)
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 6: persisted directional cluster outcome follows dominant_side
# ---------------------------------------------------------------------------

def test_persisted_directional_alert_outcome_matches_dominant_side_audit():
    """
    Persisted directional_cluster_v1 rows must store the dominant_side as
    alerts.outcome_key, even when the triggering trade's own outcome is the
    opposite side.
    """
    import asyncpg
    from pmfi.replay import replay_from_db

    t_base = datetime(2098, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
    replay_start = t_base
    replay_end = t_base + timedelta(minutes=2)

    seed_prices = ["0.40", "0.41", "0.42", "0.43"]
    replay_price = "0.60"
    trade_size = "20000"
    audit_market = f"TEST-BACKTEST-DOM-SIDE-{_RUN_ID}"

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        raw_ids: list[int] = []

        try:
            pool = await asyncpg.create_pool(
                _get_dsn(), min_size=1, max_size=3,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                from pmfi.db.repos.alerts import get_directional_outcome_audit
                from pmfi.db.repos.markets import upsert_market
                from pmfi.db.repos.trades import insert_trade
                from pmfi.domain import NormalizedTrade

                async with pool.acquire() as market_conn:
                    market_id = await upsert_market(
                        market_conn,
                        venue_code=_VENUE,
                        venue_market_id=audit_market,
                        title=None,
                    )

                # Seed no-side historical flow directly, bypassing process_event
                # so setup writes trades but no setup alerts.
                for i, price in enumerate(seed_prices):
                    ts = t_base - timedelta(seconds=240 - i * 45)
                    eid = f"bt-dom-seed-{_RUN_ID}-{i}"
                    raw_id = await _insert_raw_event(
                        conn,
                        market=audit_market,
                        exchange_ts=ts,
                        price=price,
                        size=trade_size,
                        source_event_id=eid,
                        outcome="no",
                    )
                    raw_ids.append(raw_id)
                    trade = NormalizedTrade(
                        venue_code=_VENUE,
                        venue_market_id=audit_market,
                        venue_trade_id=eid,
                        outcome_key="no",
                        aggressor_side="unknown",
                        directional_side="no",
                        side_confidence="high",
                        price=Decimal(price),
                        contracts=Decimal(trade_size),
                        capital_at_risk_usd=Decimal(price) * Decimal(trade_size),
                        payout_notional_usd=Decimal(trade_size),
                        exchange_ts=ts,
                        received_at=ts,
                        source_payload=_make_payload(
                            market=audit_market,
                            price=price,
                            size=trade_size,
                            trade_id=eid,
                            outcome="no",
                        ),
                    )
                    async with pool.acquire() as trade_conn:
                        inserted = await insert_trade(
                            trade_conn,
                            trade,
                            raw_event_id=raw_id,
                            market_id=str(market_id),
                        )
                    assert inserted is not None, "seed normalized trade was unexpectedly deduped"

                replay_eid = f"bt-dom-replay-{_RUN_ID}"
                replay_raw_id = await _insert_raw_event(
                    conn,
                    market=audit_market,
                    exchange_ts=t_base + timedelta(seconds=30),
                    price=replay_price,
                    size=trade_size,
                    source_event_id=replay_eid,
                    outcome="yes",
                )
                raw_ids.append(replay_raw_id)

                fired_since = await conn.fetchval("SELECT now()")
                await replay_from_db(
                    pool,
                    limit=0,
                    market=audit_market,
                    start_ts=replay_start,
                    end_ts=replay_end,
                    persist=True,
                    seed=True,
                )
                fired_until = await conn.fetchval("SELECT now()")

                rows = await conn.fetch(
                    """SELECT a.alert_id::text AS alert_id,
                              a.outcome_key,
                              a.evidence,
                              a.fired_at
                       FROM alerts a
                       JOIN markets m ON a.market_id = m.market_id
                       WHERE m.venue_market_id = $1
                         AND a.rule_key = 'directional_cluster_v1'
                         AND a.fired_at >= $2
                         AND a.fired_at <= $3
                       ORDER BY a.fired_at DESC""",
                    audit_market,
                    fired_since,
                    fired_until,
                )
                matches = []
                debug_rows = []
                for row in rows:
                    evidence = row["evidence"]
                    if isinstance(evidence, str):
                        evidence = json.loads(evidence)
                    else:
                        evidence = dict(evidence)
                    debug_rows.append((row["outcome_key"], evidence))
                    if (
                        row["outcome_key"] == "no"
                        and evidence.get("outcome_key") == "yes"
                        and evidence.get("directional_side") == "yes"
                        and evidence.get("dominant_side") == "no"
                    ):
                        matches.append((row, evidence))

                assert matches, (
                    "Expected persisted directional_cluster_v1 alert with stored "
                    "outcome_key='no' and yes-side triggering evidence; got "
                    f"{debug_rows}"
                )

                async with pool.acquire() as audit_conn:
                    audit = await get_directional_outcome_audit(
                        audit_conn,
                        since=fired_since - timedelta(seconds=1),
                        until=fired_until + timedelta(seconds=1),
                        rules=["directional_cluster_v1"],
                        limit=25,
                    )
                audited = [
                    row for row in audit["rows"]
                    if (
                        row["market_id"] == str(market_id)
                        and row["stored_outcome_key"] == "no"
                        and row["evidence_outcome_key"] == "yes"
                        and row["directional_side"] == "yes"
                        and row["dominant_side"] == "no"
                    )
                ]
                assert audited, f"Expected audit row for {audit_market}; got {audit['rows']}"
                assert audited[0]["status"] == "match"

            finally:
                async with pool.acquire() as cleanup_conn:
                    await _cleanup(cleanup_conn, [audit_market])
                await pool.close()

        finally:
            if raw_ids:
                await _delete_raw_events_and_dedupe(conn, raw_ids)
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 7: existing test_replay_db.py contract preserved — replay_from_db(limit=N)
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
                await _delete_raw_events_and_dedupe(conn, inserted_ids)
            await conn.close()

    asyncio.run(_run())
