"""Tests for US-03: unknown-market guard, non-binary dead_letter, and title no-clobber.

All tests are pure/offline — no asyncpg connection, no DB, no network.
Mocking pattern mirrors test_runner_suppression.py (AsyncMock/MagicMock/patch).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pmfi.domain import RawEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poly_event_no_market(asset_id: str = "token_abc") -> RawEvent:
    """Polymarket event carrying only asset_id — no 'market' field, no venue_market_id."""
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"price": "0.55", "size": "100", "side": "BUY", "asset_id": asset_id},
        venue_market_id=None,
    )


def _poly_event_with_market(market: str = "condition_xyz") -> RawEvent:
    """Polymarket event that already carries a 'market' field (pre-resolved)."""
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={
            "price": "0.55", "size": "100", "side": "BUY",
            "market": market, "outcome": "yes",
        },
        venue_market_id=market,
    )


def _make_pool(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ---------------------------------------------------------------------------
# 1. asset_map_not_loaded guard: no map + Polymarket + asset_id + no market
# ---------------------------------------------------------------------------

def test_process_event_no_map_writes_dead_letter_not_trade():
    """When asset_id_map is None and the event has asset_id but no market field,
    process_event must write a dead_letter and must NOT call insert_trade."""
    from pmfi.pipeline.runner import process_event

    raw = _poly_event_no_market("token_xyz")
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    dead_letter_calls = []

    async def _capture_dead_letter(conn, **kwargs):
        dead_letter_calls.append(kwargs)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-1", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter", side_effect=_capture_dead_letter) as mock_dl,
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market") as mock_market,
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=None))

    assert mock_dl.call_count == 1, "exactly one dead_letter must be written"
    assert mock_trade.call_count == 0, "insert_trade must NOT be called"
    assert mock_market.call_count == 0, "upsert_market must NOT be called"

    dl_kwargs = dead_letter_calls[0]
    assert dl_kwargs["error_class"] == "asset_map_not_loaded"
    assert dl_kwargs["failure_stage"] == "normalization"
    assert dl_kwargs["venue_code"] == "polymarket"
    assert "token_xyz" in dl_kwargs["error_message"]


def test_process_event_empty_map_writes_dead_letter_not_trade():
    """Same guard fires when asset_id_map is an empty dict (not just None)."""
    from pmfi.pipeline.runner import process_event

    raw = _poly_event_no_market("token_abc")
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-2", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter") as mock_dl,
        patch("pmfi.pipeline.runner.insert_trade") as mock_trade,
        patch("pmfi.pipeline.runner.upsert_market"),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map={}))

    assert mock_dl.call_count == 1
    assert mock_trade.call_count == 0


def test_process_event_with_market_field_not_guarded():
    """Event that already has a 'market' field must proceed normally (guard must NOT fire)."""
    from pmfi.pipeline.runner import process_event

    raw = _poly_event_with_market("condition_xyz")
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_trade_obj = MagicMock()
    mock_trade_obj.venue_code = "polymarket"
    mock_trade_obj.venue_market_id = "condition_xyz"
    mock_trade_obj.outcome_key = "yes"
    mock_trade_obj.capital_at_risk_usd = Decimal("5000")
    mock_engine.evaluate.return_value = []

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-3", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter") as mock_dl,
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade_obj),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=None))

    # guard must NOT trigger; no dead_letter written for missing map
    assert mock_dl.call_count == 0


def test_process_event_resolved_via_map_not_guarded():
    """Event resolved via a populated asset_id_map must proceed normally."""
    from pmfi.pipeline.runner import process_event

    raw = _poly_event_no_market("token_yes_abc")
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_trade_obj = MagicMock()
    mock_trade_obj.venue_code = "polymarket"
    mock_trade_obj.venue_market_id = "condition_xyz"
    mock_trade_obj.outcome_key = "yes"
    mock_trade_obj.capital_at_risk_usd = Decimal("5000")
    mock_engine.evaluate.return_value = []

    asset_map = {
        "token_yes_abc": {
            "venue_market_id": "condition_xyz",
            "venue_code": "polymarket",
            "market_id": "00000000-0000-0000-0000-000000000001",
            "outcome_key": "yes",
            "outcome_label": "Yes",
            "is_binary": True,
        }
    }

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-4", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter") as mock_dl,
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade_obj),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=asset_map))

    # resolved path: no dead_letter (no map-not-loaded, no missing_asset_mapping)
    assert mock_dl.call_count == 0


# ---------------------------------------------------------------------------
# 2. Non-binary token: store + dead_letter with error_class='multi_outcome_unsupported'
# ---------------------------------------------------------------------------

def test_process_event_nonbinary_writes_dead_letter_and_stores_trade():
    """Non-binary token (outcome_key='unknown') writes a dead_letter but also stores the trade."""
    from pmfi.pipeline.runner import process_event

    raw = _poly_event_no_market("token_biden")
    mock_conn = AsyncMock()
    mock_pool = _make_pool(mock_conn)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = []

    mock_trade_obj = MagicMock()
    mock_trade_obj.venue_code = "polymarket"
    mock_trade_obj.venue_market_id = "condition_election"
    mock_trade_obj.outcome_key = "unknown"
    mock_trade_obj.capital_at_risk_usd = Decimal("2000")

    asset_map = {
        "token_biden": {
            "venue_market_id": "condition_election",
            "venue_code": "polymarket",
            "market_id": "00000000-0000-0000-0000-000000000002",
            "outcome_key": "biden",
            "outcome_label": "Biden",
            "is_binary": False,
        }
    }

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-5", False))),
        patch("pmfi.pipeline.runner.insert_dead_letter") as mock_dl,
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade_obj),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-2")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-2")) as mock_trade,
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, AsyncMock(), asset_id_map=asset_map))

    assert mock_dl.call_count == 1, "dead_letter must be written for non-binary token"
    assert mock_trade.call_count == 1, "trade must still be stored (store+dead_letter)"

    dl_kwargs = mock_dl.call_args.kwargs
    assert dl_kwargs["error_class"] == "multi_outcome_unsupported"
    assert dl_kwargs["failure_stage"] == "normalization"
    assert dl_kwargs["venue_code"] == "polymarket"


# ---------------------------------------------------------------------------
# 3. upsert_market title no-clobber logic (pure SQL-arg test, no DB needed)
# ---------------------------------------------------------------------------

class _FakeConn:
    """Minimal fake asyncpg connection that captures the last fetchrow call args."""

    def __init__(self, market_id: str = "00000000-0000-0000-0000-000000000099"):
        self._market_id = market_id
        self.last_query: str | None = None
        self.last_args: tuple = ()

    async def fetchrow(self, query: str, *args):
        self.last_query = query
        self.last_args = args
        return {"market_id": self._market_id}


def test_upsert_market_title_none_uses_coalesce_not_overwrite():
    """When title=None is passed (pipeline default), the SQL must use COALESCE so an
    existing human-readable title is not overwritten by the venue_market_id fallback."""
    import asyncio
    from pmfi.db.repos.markets import upsert_market

    conn = _FakeConn()
    asyncio.run(upsert_market(conn, venue_code="polymarket", venue_market_id="condition_abc", title=None))

    # The SQL must contain COALESCE to preserve existing title
    assert "COALESCE" in (conn.last_query or ""), (
        "upsert_market SQL must use COALESCE to avoid clobbering existing title"
    )
    # The effective_title passed as the INSERT value must be the venue_market_id fallback
    # (args: $1=venue_code, $2=venue_market_id, $3=effective_title, $4=status)
    assert conn.last_args[2] == "condition_abc", (
        "When title=None, the INSERT value must fall back to venue_market_id"
    )


def test_upsert_market_explicit_title_used_for_insert():
    """When an explicit title is passed, it is used as the INSERT value."""
    import asyncio
    from pmfi.db.repos.markets import upsert_market

    conn = _FakeConn()
    asyncio.run(upsert_market(
        conn,
        venue_code="polymarket",
        venue_market_id="condition_abc",
        title="Will Biden win?",
    ))

    assert conn.last_args[2] == "Will Biden win?", (
        "Explicit title must be forwarded to the INSERT"
    )
    # COALESCE still present for safety on conflicts
    assert "COALESCE" in (conn.last_query or "")


def test_upsert_market_returns_market_id_string():
    """upsert_market must return the market_id as a plain string."""
    import asyncio
    from pmfi.db.repos.markets import upsert_market

    conn = _FakeConn("beef0000-0000-0000-0000-000000000001")
    result = asyncio.run(upsert_market(conn, venue_code="kalshi", venue_market_id="KXETH-24"))
    assert isinstance(result, str)
    assert result == "beef0000-0000-0000-0000-000000000001"
