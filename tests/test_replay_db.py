"""DB-gated replay proof tests (Targets 2 & 3).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.
Tests isolate synthetic rows under a unique market prefix and clean up after.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_SYNTHETIC_MARKET = "TEST-REPLAY-DB-SYNTHETIC-001"
_VENUE = "polymarket"
_CHANNEL = "market_ws"
_EVENT_TYPE = "last_trade_price"


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pm_payload(
    *,
    market: str = _SYNTHETIC_MARKET,
    price: str = "0.42",
    size: str = "1000",
    outcome: str = "yes",
    side: str = "buy",
    trade_id: str | None = None,
) -> dict:
    p: dict = {
        "market": market,
        "price": price,
        "size": size,
        "outcome": outcome,
        "side": side,
    }
    if trade_id is not None:
        p["trade_id"] = trade_id
    return p


def _fixture_json(
    *,
    venue_market_id: str = _SYNTHETIC_MARKET,
    exchange_ts: str,
    price: str = "0.42",
    size: str = "1000",
    source_event_id: str,
) -> str:
    """Return JSON string for a valid Polymarket fixture file."""
    return json.dumps({
        "venue_code": _VENUE,
        "source_channel": _CHANNEL,
        "source_event_type": _EVENT_TYPE,
        "source_event_id": source_event_id,
        "venue_market_id": venue_market_id,
        "exchange_ts": exchange_ts,
        "payload": _make_pm_payload(
            market=venue_market_id,
            price=price,
            size=size,
            trade_id=source_event_id,
        ),
    }, indent=2)


# ---------------------------------------------------------------------------
# Test 1: replay_from_db returns trades in ascending event-time order
# ---------------------------------------------------------------------------

def test_replay_from_db_event_time_ordering():
    """Rows inserted in non-chronological order must come back sorted by exchange_ts."""
    import asyncpg
    from pmfi.replay import replay_from_db

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        inserted_ids: list[int] = []
        try:
            # Insert 3 raw_events for a synthetic market in reverse time order
            # (t=300s, t=100s, t=200s) so insertion order differs from event-time order.
            base = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            from datetime import timedelta

            entries = [
                # (offset_seconds, source_event_id, price, size)
                (300, "test-ord-c", "0.70", "500"),
                (100, "test-ord-a", "0.30", "200"),
                (200, "test-ord-b", "0.50", "300"),
            ]
            import hashlib

            for offset, eid, price, size in entries:
                ts = base + timedelta(seconds=offset)
                payload = _make_pm_payload(
                    market=_SYNTHETIC_MARKET,
                    price=price,
                    size=size,
                    trade_id=eid,
                )
                payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
                row = await conn.fetchrow(
                    """INSERT INTO raw_events
                       (venue_code, source_channel, source_event_type, source_event_id,
                        venue_market_id, exchange_ts, received_at, payload, payload_hash, parser_version)
                       VALUES ($1,$2,$3,$4,$5,$6,now(),$7::jsonb,$8,$9)
                       RETURNING raw_event_id""",
                    _VENUE, _CHANNEL, _EVENT_TYPE, eid,
                    _SYNTHETIC_MARKET, ts,
                    json.dumps(payload), payload_hash, "raw.v1",
                )
                inserted_ids.append(int(row["raw_event_id"]))

            # Pool required by replay_from_db — build a minimal one.
            # Query total raw_events count so we can set limit large enough that
            # the synthetic far-future rows (exchange_ts=2099, which sort LAST) are
            # included even when the live DB already holds thousands of real rows.
            total_raw = await conn.fetchval("SELECT COUNT(*) FROM raw_events")
            replay_limit = int(total_raw) + 20  # +20 covers our 3 inserted rows plus buffer

            pool = await asyncpg.create_pool(
                _get_dsn(),
                min_size=1, max_size=2,
                server_settings={"search_path": "pmfi,public"},
            )
            try:
                results = await replay_from_db(pool, limit=replay_limit)
            finally:
                await pool.close()

            # Filter to only our synthetic market; assertions are market-scoped so
            # co-mingled real data in other markets does not affect the result.
            synthetic = [
                r for r in results
                if r.trade.venue_market_id == _SYNTHETIC_MARKET
            ]

            assert len(synthetic) >= 3, (
                f"Expected >=3 results for synthetic market, got {len(synthetic)}"
            )

            # Assert ascending exchange_ts
            tss = [r.trade.exchange_ts for r in synthetic]
            assert all(a is not None for a in tss), "exchange_ts should not be None"
            assert tss == sorted(tss), (
                f"Trades not in ascending exchange_ts order: {tss}"
            )

        finally:
            # Clean up synthetic rows
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
# Test 2: replay_fixtures_persist is idempotent
# ---------------------------------------------------------------------------

def test_persisted_replay_twice_idempotent():
    """Running replay_fixtures_persist twice adds zero net normalized_trades or metric_windows."""
    import asyncpg
    from pmfi.replay import replay_fixtures_persist

    async def _run():
        pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=1, max_size=2,
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # Write two valid fixture files with unique trade IDs
                (tmp / "fix_a.json").write_text(
                    _fixture_json(
                        exchange_ts="2099-02-01T10:00:00Z",
                        source_event_id="idem-test-trade-a",
                    ),
                    encoding="utf-8",
                )
                (tmp / "fix_b.json").write_text(
                    _fixture_json(
                        exchange_ts="2099-02-01T10:01:00Z",
                        price="0.55",
                        size="2000",
                        source_event_id="idem-test-trade-b",
                    ),
                    encoding="utf-8",
                )

                async with pool.acquire() as conn:
                    count_trades_before = await conn.fetchval(
                        "SELECT COUNT(*) FROM normalized_trades t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )
                    count_metrics_before = await conn.fetchval(
                        "SELECT COUNT(*) FROM metric_windows t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )

                # First run
                await replay_fixtures_persist(tmp, pool)

                async with pool.acquire() as conn:
                    count_trades_after1 = await conn.fetchval(
                        "SELECT COUNT(*) FROM normalized_trades t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )
                    count_metrics_after1 = await conn.fetchval(
                        "SELECT COUNT(*) FROM metric_windows t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )

                # Second run (must be idempotent)
                await replay_fixtures_persist(tmp, pool)

                async with pool.acquire() as conn:
                    count_trades_after2 = await conn.fetchval(
                        "SELECT COUNT(*) FROM normalized_trades t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )
                    count_metrics_after2 = await conn.fetchval(
                        "SELECT COUNT(*) FROM metric_windows t "
                        "JOIN markets m ON t.market_id = m.market_id "
                        "WHERE m.venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )

                assert (count_trades_after2 - count_trades_after1) == 0, (
                    f"Second run added {count_trades_after2 - count_trades_after1} "
                    "normalized_trades; expected 0 (not idempotent)"
                )
                assert (count_metrics_after2 - count_metrics_after1) == 0, (
                    f"Second run changed metric_windows by "
                    f"{count_metrics_after2 - count_metrics_after1}; expected 0"
                )

        finally:
            # Clean up synthetic rows (cascade deletes handle child FK rows if configured;
            # otherwise delete in FK-safe order).
            async with pool.acquire() as conn:
                market_id = await conn.fetchval(
                    "SELECT market_id FROM markets WHERE venue_market_id = $1",
                    _SYNTHETIC_MARKET,
                )
                if market_id is not None:
                    await conn.execute(
                        "DELETE FROM metric_windows WHERE market_id = $1", market_id
                    )
                    await conn.execute(
                        "DELETE FROM normalized_trades WHERE market_id = $1", market_id
                    )
                    # Remove raw_events for this market
                    await conn.execute(
                        "DELETE FROM raw_events WHERE venue_market_id = $1",
                        _SYNTHETIC_MARKET,
                    )
                    await conn.execute(
                        "DELETE FROM markets WHERE market_id = $1", market_id
                    )
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: replay_fixtures_persist skips malformed payload without crashing
# ---------------------------------------------------------------------------

def test_persisted_replay_skips_malformed_without_crash():
    """A malformed fixture (bad price) must not abort the run; valid fixture is processed."""
    import asyncpg
    from pmfi.replay import replay_fixtures_persist

    run_id = uuid4().hex[:12]
    _BAD_MARKET = f"pm-bad-market-test-{run_id}"
    _GOOD_MARKET = f"TEST-REPLAY-DB-GOOD-ONLY-{run_id}"
    _BAD_TRADE_ID = f"malformed-test-bad-{run_id}"
    _GOOD_TRADE_ID = f"malformed-test-good-{run_id}"

    async def _run():
        pool = await asyncpg.create_pool(
            _get_dsn(),
            min_size=1, max_size=2,
            server_settings={"search_path": "pmfi,public"},
        )
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)

                # Malformed fixture: price is not a number
                malformed = {
                    "venue_code": "polymarket",
                    "source_channel": "market_ws",
                    "source_event_type": "last_trade_price",
                    "source_event_id": _BAD_TRADE_ID,
                    "venue_market_id": _BAD_MARKET,
                    "exchange_ts": "2099-03-01T09:00:00Z",
                    "payload": {
                        "market": _BAD_MARKET,
                        "price": "not-a-number",
                        "size": "5000",
                    },
                }
                (tmp / "bad.json").write_text(
                    json.dumps(malformed, indent=2), encoding="utf-8"
                )

                # Valid fixture with its own unique market to isolate cleanup
                good = {
                    "venue_code": "polymarket",
                    "source_channel": "market_ws",
                    "source_event_type": "last_trade_price",
                    "source_event_id": _GOOD_TRADE_ID,
                    "venue_market_id": _GOOD_MARKET,
                    "exchange_ts": "2099-03-01T09:01:00Z",
                    "payload": {
                        "trade_id": _GOOD_TRADE_ID,
                        "market": _GOOD_MARKET,
                        "outcome": "yes",
                        "side": "buy",
                        "price": "0.60",
                        "size": "1500",
                    },
                }
                (tmp / "good.json").write_text(
                    json.dumps(good, indent=2), encoding="utf-8"
                )

                # Must not raise
                results = await replay_fixtures_persist(tmp, pool)

                # Valid fixture should appear in results
                assert len(results) >= 1, (
                    "Expected at least one result from the valid fixture"
                )
                market_ids = [r.trade.venue_market_id for r in results]
                assert _GOOD_MARKET in market_ids, (
                    f"Good fixture market {_GOOD_MARKET!r} not in results: {market_ids}"
                )
                async with pool.acquire() as conn:
                    bad_dead_letters = await conn.fetchval(
                        """SELECT COUNT(*)
                           FROM dead_letters dl
                           JOIN raw_events re ON re.raw_event_id = dl.raw_event_id
                           WHERE re.venue_market_id = $1
                             AND dl.error_class = 'invalid_price_or_size'""",
                        _BAD_MARKET,
                    )
                    assert int(bad_dead_letters or 0) >= 1, (
                        "Malformed synthetic trade fixture must be accounted by a linked dead_letter"
                    )

        finally:
            async with pool.acquire() as conn:
                markets = [_GOOD_MARKET, _BAD_MARKET]
                trade_ids = [_GOOD_TRADE_ID, _BAD_TRADE_ID]
                for vmid in markets:
                    mid = await conn.fetchval(
                        "SELECT market_id FROM markets WHERE venue_market_id = $1", vmid
                    )
                    if mid is not None:
                        await conn.execute(
                            "DELETE FROM metric_windows WHERE market_id = $1", mid
                        )
                        await conn.execute(
                            "DELETE FROM normalized_trades WHERE market_id = $1", mid
                        )
                        await conn.execute(
                            "DELETE FROM normalized_trade_dedupe_keys "
                            "WHERE venue_code = $1 AND venue_trade_id = ANY($2::text[])",
                            _VENUE, trade_ids,
                        )
                        await conn.execute(
                            "DELETE FROM markets WHERE market_id = $1", mid
                        )
                await conn.execute(
                    "DELETE FROM dead_letters WHERE raw_event_id IN "
                    "(SELECT raw_event_id FROM raw_events WHERE venue_market_id = ANY($1::text[]))",
                    markets,
                )
                await conn.execute(
                    "DELETE FROM event_dedupe_keys "
                    "WHERE venue_code = $1 AND source_channel = $2 "
                    "AND first_raw_event_id IN "
                    "(SELECT raw_event_id FROM raw_events WHERE venue_market_id = ANY($3::text[]))",
                    _VENUE, _CHANNEL, markets,
                )
                await conn.execute(
                    "DELETE FROM raw_events WHERE venue_market_id = ANY($1::text[])",
                    markets,
                )
            await pool.close()

    asyncio.run(_run())
