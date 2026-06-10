"""Offline tests for mid-session Polymarket/Kalshi subscription refresh.

No DB, no network.  All pool/DB interactions are faked via AsyncMock / MagicMock.
Imports the real production code (pmfi.commands._shared._refresh_subscriptions).
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_pool_with_fetch(watched_rows: list[dict], asset_rows: list[dict]) -> MagicMock:
    """Return a mock pool whose acquire() context manager yields a conn that
    returns *watched_rows* from fetch_watched_markets and whose load_asset_id_mapping
    call returns a dict built from *asset_rows*.

    asset_rows: list of dicts with keys matching load_asset_id_mapping return format:
        {"venue_outcome_id": str, "venue_code": str, "market_id": str, ...}
    """
    mock_conn = AsyncMock()
    # fetch_watched_markets calls conn.fetch(...)
    mock_conn.fetch = AsyncMock(return_value=watched_rows)

    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # load_asset_id_mapping issues its own pool.acquire(); wire a second acquire call
    # that returns a conn whose fetch returns asset_rows.
    mock_conn2 = AsyncMock()
    mock_conn2.fetch = AsyncMock(return_value=asset_rows)
    pool.acquire.return_value.__aenter__ = AsyncMock(
        side_effect=[mock_conn, mock_conn2, mock_conn, mock_conn2,
                     mock_conn, mock_conn2, mock_conn, mock_conn2]
    )
    return pool


def _watched_poly(market_id: str, venue_market_id: str = "cond-1") -> dict:
    return {
        "market_id": market_id,
        "venue_code": "polymarket",
        "venue_market_id": venue_market_id,
        "title": "Test market",
    }


def _watched_kalshi(ticker: str) -> dict:
    return {
        "market_id": "k-" + ticker,
        "venue_code": "kalshi",
        "venue_market_id": ticker,
        "title": "Kalshi market",
    }


def _outcome_row(token_id: str, market_id: str, venue_market_id: str = "cond-1") -> dict:
    """Simulate a row as returned by the DB query inside load_asset_id_mapping."""
    return {
        "venue_outcome_id": token_id,
        "outcome_key": "yes",
        "outcome_label": "Yes",
        "is_binary": True,
        "market_id": market_id,
        "venue_market_id": venue_market_id,
        "venue_code": "polymarket",
    }


# ---------------------------------------------------------------------------
# _refresh_subscriptions: in-place map update is visible via same reference
# ---------------------------------------------------------------------------

def test_refresh_subscriptions_updates_asset_id_map_in_place():
    """asset_id_map dict updated in-place by _refresh_subscriptions is visible
    to a consumer that holds a reference to the same dict object."""
    from pmfi.commands._shared import _refresh_subscriptions

    market_id = "mkt-abc"
    token_id = "tok-001"

    watched_rows = [_watched_poly(market_id, venue_market_id="cond-abc")]
    asset_rows = [_outcome_row(token_id, market_id, venue_market_id="cond-abc")]

    asset_id_map: dict = {}  # starts empty — refresh must populate it
    consumer_ref = asset_id_map  # consumer holds the SAME reference

    async def _fake_fetch_watched(conn):
        return watched_rows

    async def _fake_load_map(pool):
        return {
            row["venue_outcome_id"]: {
                "market_id": row["market_id"],
                "venue_market_id": row["venue_market_id"],
                "venue_code": row["venue_code"],
                "outcome_key": row["outcome_key"],
                "outcome_label": row["outcome_label"],
                "is_binary": row["is_binary"],
            }
            for row in asset_rows
        }

    pool = MagicMock()
    mock_conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with patch("pmfi.db.repos.markets.fetch_watched_markets", side_effect=_fake_fetch_watched):
            with patch("pmfi.markets.load_asset_id_mapping", side_effect=_fake_load_map):
                poly_ids, kalshi_tickers = await _refresh_subscriptions(pool, asset_id_map)

        return poly_ids, kalshi_tickers

    poly_ids, kalshi_tickers = asyncio.run(_run())

    # The in-place update must have added the token
    assert token_id in consumer_ref, "consumer_ref must see the new token via the same dict object"
    assert token_id in asset_id_map
    assert poly_ids == [token_id]
    assert kalshi_tickers == []


# ---------------------------------------------------------------------------
# _refresh_subscriptions: stale keys are removed from asset_id_map
# ---------------------------------------------------------------------------

def test_refresh_subscriptions_removes_stale_keys():
    """Keys no longer present in the fresh DB snapshot are removed in-place."""
    from pmfi.commands._shared import _refresh_subscriptions

    stale_token = "tok-stale"
    fresh_token = "tok-fresh"
    market_id = "mkt-xyz"

    asset_id_map: dict = {
        stale_token: {"market_id": market_id, "venue_market_id": "cond-xyz", "venue_code": "polymarket",
                      "outcome_key": "yes", "outcome_label": "Yes", "is_binary": True},
    }

    watched_rows = [_watched_poly(market_id, venue_market_id="cond-xyz")]
    fresh_map = {
        fresh_token: {"market_id": market_id, "venue_market_id": "cond-xyz", "venue_code": "polymarket",
                      "outcome_key": "yes", "outcome_label": "Yes", "is_binary": True},
    }

    async def _fake_fetch_watched(conn):
        return watched_rows

    async def _fake_load_map(pool):
        return dict(fresh_map)

    pool = MagicMock()
    mock_conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        with patch("pmfi.db.repos.markets.fetch_watched_markets", side_effect=_fake_fetch_watched):
            with patch("pmfi.markets.load_asset_id_mapping", side_effect=_fake_load_map):
                return await _refresh_subscriptions(pool, asset_id_map)

    poly_ids, _ = asyncio.run(_run())

    assert stale_token not in asset_id_map, "stale key must be removed"
    assert fresh_token in asset_id_map, "fresh key must be present"
    assert fresh_token in poly_ids


# ---------------------------------------------------------------------------
# _refresh_subscriptions: failure is non-fatal; previous map retained
# ---------------------------------------------------------------------------

def test_refresh_subscriptions_failure_leaves_previous_values(caplog):
    """When the helper raises, the caller retains previous poly_ids and kalshi_tickers."""
    from pmfi.commands._shared import _refresh_subscriptions

    token_id = "tok-prev"
    market_id = "mkt-prev"
    prev_asset_id_map: dict = {
        token_id: {"market_id": market_id, "venue_market_id": "cond-prev", "venue_code": "polymarket",
                   "outcome_key": "yes", "outcome_label": "Yes", "is_binary": True},
    }
    prev_poly_ids = [token_id]
    prev_kalshi = ["KXBTC-24"]

    pool = MagicMock()
    mock_conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _failing_fetch_watched(conn):
        raise RuntimeError("DB blip")

    async def _run():
        # Caller pattern: non-fatal try/except around _refresh_subscriptions
        _poly = list(prev_poly_ids)
        _kalshi = list(prev_kalshi)
        _map = dict(prev_asset_id_map)

        try:
            with patch("pmfi.db.repos.markets.fetch_watched_markets", side_effect=_failing_fetch_watched):
                _poly, _kalshi = await _refresh_subscriptions(pool, _map)
        except Exception as exc:
            # caller logs and retains old values — this is the non-fatal pattern
            logging.getLogger("test").warning("refresh failed (non-fatal): %s", exc)

        return _poly, _kalshi, _map

    poly, kalshi, amap = asyncio.run(_run())

    # On failure, caller retains the previous values
    assert poly == prev_poly_ids
    assert kalshi == prev_kalshi
    # asset_id_map was NOT mutated (failure happened before any update)
    assert token_id in amap


# ---------------------------------------------------------------------------
# _refresh_subscriptions: no-change idempotence
# ---------------------------------------------------------------------------

def test_refresh_subscriptions_idempotent_when_db_unchanged():
    """refresh with identical DB state yields equal subscription list and no churn."""
    from pmfi.commands._shared import _refresh_subscriptions

    market_id = "mkt-id"
    token_id = "tok-id"

    existing_map = {
        token_id: {"market_id": market_id, "venue_market_id": "cond-id", "venue_code": "polymarket",
                   "outcome_key": "yes", "outcome_label": "Yes", "is_binary": True},
    }
    watched_rows = [_watched_poly(market_id, venue_market_id="cond-id")]

    async def _fake_fetch_watched(conn):
        return watched_rows

    async def _fake_load_map(pool):
        return dict(existing_map)

    pool = MagicMock()
    mock_conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    asset_id_map = dict(existing_map)

    async def _run():
        with patch("pmfi.db.repos.markets.fetch_watched_markets", side_effect=_fake_fetch_watched):
            with patch("pmfi.markets.load_asset_id_mapping", side_effect=_fake_load_map):
                return await _refresh_subscriptions(pool, asset_id_map)

    poly_ids, kalshi_tickers = asyncio.run(_run())

    assert poly_ids == [token_id]
    assert kalshi_tickers == []
    assert set(asset_id_map) == {token_id}


# ---------------------------------------------------------------------------
# factory re-resolution: _make_poly reads _current_poly_ids at call time
# ---------------------------------------------------------------------------

def test_factory_rereads_ids_between_supervisor_restarts():
    """Simulate _make_poly closing over a mutable list and supervise calling it
    twice: the second call should use the updated list (new market added mid-session).

    This mirrors the pattern in cmd_ingest where _current_poly_ids[:] = _new_poly
    and _make_poly returns PolymarketAdapter(asset_ids=list(_current_poly_ids)).
    """
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from unittest.mock import MagicMock, AsyncMock
    from pmfi.pipeline.runner import IngestConnectionLost

    # Simulate _current_poly_ids container (mutable list)
    _current_poly_ids: list = ["tok-initial"]

    captured_ids: list[list] = []

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]

        def make_adapter():
            # Captures the current value of _current_poly_ids at factory call time
            ids = list(_current_poly_ids)
            captured_ids.append(ids)
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                # Simulate mid-session watch: update the mutable container
                _current_poly_ids[:] = ["tok-initial", "tok-new"]
                raise IngestConnectionLost("simulated restart")
            # Second run: succeed and then trigger shutdown
            shutdown.set()

        pm = PoolManager("fake_dsn")

        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()
        pm._pool = mock_pool

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            await asyncio.wait_for(
                supervise(
                    "polymarket", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.01, max_backoff=0.1, jitter=False,
                ),
                timeout=5.0,
            )

        return captured_ids

    result = asyncio.run(_run())

    assert len(result) == 2, f"Expected 2 make_adapter calls, got {len(result)}"
    assert result[0] == ["tok-initial"], f"First call should have initial ids, got {result[0]}"
    assert result[1] == ["tok-initial", "tok-new"], f"Second call should include new id, got {result[1]}"


# ---------------------------------------------------------------------------
# Kalshi tickers: same mutable-container pattern
# ---------------------------------------------------------------------------

def test_kalshi_factory_rereads_tickers_between_restarts():
    """Kalshi _make_kalshi reads _current_kalshi_tickers at call time."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    _current_kalshi_tickers: list = ["KXBTC-24"]
    captured_tickers: list[list] = []

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]

        def make_adapter():
            tickers = list(_current_kalshi_tickers)
            captured_tickers.append(tickers)
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                _current_kalshi_tickers[:] = ["KXBTC-24", "KXETH-24"]
                raise IngestConnectionLost("simulated restart")
            shutdown.set()

        pm = PoolManager("fake_dsn")
        mock_pool = MagicMock()
        mock_pool.close = AsyncMock()
        pm._pool = mock_pool

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            await asyncio.wait_for(
                supervise(
                    "kalshi", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.01, max_backoff=0.1, jitter=False,
                ),
                timeout=5.0,
            )

        return captured_tickers

    result = asyncio.run(_run())

    assert len(result) == 2
    assert result[0] == ["KXBTC-24"]
    assert result[1] == ["KXBTC-24", "KXETH-24"]
