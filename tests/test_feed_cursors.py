from __future__ import annotations

import json


def test_kalshi_rest_cursor_repo_persists_and_loads_by_ticker() -> None:
    import asyncio

    from pmfi.db.repos.feed_cursors import (
        KALSHI_REST_TRADE_FEED,
        _canonical_cursor_value,
        load_kalshi_rest_trade_cursors,
        upsert_kalshi_rest_trade_cursor,
    )

    class _Conn:
        def __init__(self) -> None:
            self.market_id = "22222222-2222-2222-2222-222222222222"
            self.cursor_execute: tuple[str, tuple] | None = None

        async def fetchrow(self, query: str, *args):
            if "INSERT INTO markets" in query:
                return {"market_id": self.market_id}
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def execute(self, query: str, *args):
            if "feed_cursors" not in query:
                raise AssertionError(f"unexpected execute query: {query}")
            self.cursor_execute = (query, args)
            return "INSERT 0 1"

        async def fetch(self, query: str, *args):
            if "feed_cursors" not in query:
                raise AssertionError(f"unexpected fetch query: {query}")
            assert args == (KALSHI_REST_TRADE_FEED, ["KX-CURSOR"])
            return [
                {
                    "venue_market_id": "KX-CURSOR",
                    "cursor_value": "2026-06-10T10:00:01.000000Z",
                }
            ]

    conn = _Conn()

    asyncio.run(
        upsert_kalshi_rest_trade_cursor(
            conn,  # type: ignore[arg-type]
            ticker="KX-CURSOR",
            cursor_value="2026-06-10T10:00:01.000000Z",
            source_event_id="cursor-001",
        )
    )
    loaded = asyncio.run(load_kalshi_rest_trade_cursors(conn, ["KX-CURSOR"]))  # type: ignore[arg-type]

    assert loaded == {"KX-CURSOR": "2026-06-10T10:00:01.000000Z"}
    assert conn.cursor_execute is not None
    query, args = conn.cursor_execute
    assert "ON CONFLICT (venue_code, feed_name, market_id)" in query
    assert "EXCLUDED.cursor_value::timestamptz >= feed_cursors.cursor_value::timestamptz" in query
    assert args[0] == KALSHI_REST_TRADE_FEED
    assert args[1] == conn.market_id
    assert args[2] == "2026-06-10T10:00:01.000000Z"
    assert json.loads(args[3]) == {"source_event_id": "cursor-001", "ticker": "KX-CURSOR"}
    assert _canonical_cursor_value("2026-06-10T10:00:01Z") == "2026-06-10T10:00:01.000000Z"
    assert _canonical_cursor_value("2026-06-10T06:00:01-04:00") == "2026-06-10T10:00:01.000000Z"


def test_kalshi_rest_cursor_upsert_canonicalizes_timestamp_input() -> None:
    import asyncio

    from pmfi.db.repos.feed_cursors import upsert_kalshi_rest_trade_cursor

    class _Conn:
        def __init__(self) -> None:
            self.market_id = "22222222-2222-2222-2222-222222222222"
            self.cursor_execute: tuple[str, tuple] | None = None

        async def fetchrow(self, query: str, *args):
            if "INSERT INTO markets" in query:
                return {"market_id": self.market_id}
            raise AssertionError(f"unexpected fetchrow query: {query}")

        async def execute(self, query: str, *args):
            self.cursor_execute = (query, args)
            return "INSERT 0 1"

    conn = _Conn()

    asyncio.run(
        upsert_kalshi_rest_trade_cursor(
            conn,  # type: ignore[arg-type]
            ticker="KX-CURSOR",
            cursor_value="2026-06-10T06:00:01-04:00",
            source_event_id="cursor-002",
        )
    )

    assert conn.cursor_execute is not None
    _, args = conn.cursor_execute
    assert args[2] == "2026-06-10T10:00:01.000000Z"
