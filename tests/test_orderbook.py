"""Tests for orderbook module (no live API, no asyncpg required)."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest


def test_extract_token_id_from_asset_id():
    from pmfi.orderbook import _extract_token_id
    payload = {"asset_id": "tok-abc", "price": "0.65"}
    assert _extract_token_id(payload) == "tok-abc"


def test_extract_token_id_from_token_id_field():
    from pmfi.orderbook import _extract_token_id
    payload = {"token_id": "tok-xyz"}
    assert _extract_token_id(payload) == "tok-xyz"


def test_extract_token_id_missing():
    from pmfi.orderbook import _extract_token_id
    assert _extract_token_id({"price": "0.5"}) is None


def test_parse_book_levels_sorts_bids_descending():
    from pmfi.orderbook import parse_book_levels
    raw = {
        "bids": [{"price": "0.60", "size": "100"}, {"price": "0.65", "size": "200"}],
        "asks": [],
    }
    bids, asks = parse_book_levels(raw)
    assert bids[0]["price"] == Decimal("0.65")
    assert bids[1]["price"] == Decimal("0.60")
    assert asks == []


def test_parse_book_levels_sorts_asks_ascending():
    from pmfi.orderbook import parse_book_levels
    raw = {
        "bids": [],
        "asks": [{"price": "0.70", "size": "100"}, {"price": "0.66", "size": "50"}],
    }
    bids, asks = parse_book_levels(raw)
    assert asks[0]["price"] == Decimal("0.66")
    assert asks[1]["price"] == Decimal("0.70")


def test_compute_book_summary_spread():
    from pmfi.orderbook import compute_book_summary
    bids = [{"price": Decimal("0.64"), "size": Decimal("1000")}]
    asks = [{"price": Decimal("0.66"), "size": Decimal("500")}]
    summary = compute_book_summary(bids, asks)
    assert summary["best_bid"] == Decimal("0.64")
    assert summary["best_ask"] == Decimal("0.66")
    assert summary["spread"] == Decimal("0.02")


def test_compute_book_summary_empty():
    from pmfi.orderbook import compute_book_summary
    summary = compute_book_summary([], [])
    assert summary["best_bid"] is None
    assert summary["best_ask"] is None
    assert summary["spread"] is None


def test_compute_book_summary_top_depth():
    from pmfi.orderbook import compute_book_summary
    bids = [{"price": Decimal("0.60"), "size": Decimal("100")}]
    asks = [{"price": Decimal("0.65"), "size": Decimal("50")}]
    summary = compute_book_summary(bids, asks)
    expected = Decimal("0.60") * 100 + Decimal("0.65") * 50
    assert summary["top_depth_usd"] == expected


def test_orderbook_module_importable():
    import pmfi.orderbook  # noqa: F401


def test_orderbook_repo_importable():
    import pmfi.db.repos.orderbook  # noqa: F401


def test_rate_limit_skips_repeat_fetch():
    """Second fetch within rate-limit window returns None without making a network request."""
    import asyncio
    from datetime import datetime, timezone
    from pmfi import orderbook as ob_mod

    # Pre-fill the rate limit cache with a very recent entry
    token = "tok-ratelimit-test"
    ob_mod._last_fetch[token] = datetime.now(timezone.utc)

    # The rate-limit branch returns None before any network call is made
    result = asyncio.run(ob_mod.fetch_polymarket_book(token))

    assert result is None

    # Cleanup
    del ob_mod._last_fetch[token]


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


class _FakeOrderbookConn:
    def __init__(self):
        self.fetchrow_calls = []
        self.execute_calls = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        if "INSERT INTO orderbook_snapshots" in query:
            return {"orderbook_snapshot_id": "00000000-0000-0000-0000-000000000001"}
        if "SELECT captured_at FROM orderbook_snapshots" in query:
            return {"captured_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        return None

    async def execute(self, query, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"


class _FakeKalshiOrderbookConn:
    async def fetch(self, query, *args):
        return [
            {
                "market_id": "00000000-0000-0000-0000-000000000020",
                "venue_market_id": "KX-TEST",
            }
        ]


def test_insert_orderbook_snapshot_uses_supplied_outcome_key():
    from pmfi.db.repos.orderbook import insert_orderbook_snapshot

    conn = _FakeOrderbookConn()
    asyncio.run(
        insert_orderbook_snapshot(
            conn,
            venue_code="polymarket",
            market_id="00000000-0000-0000-0000-000000000010",
            outcome_key="no",
            bids=[{"price": Decimal("0.40"), "size": Decimal("100")}],
            asks=[{"price": Decimal("0.42"), "size": Decimal("50")}],
        )
    )

    assert len(conn.execute_calls) == 2
    insert_query, insert_args = conn.fetchrow_calls[0]
    assert "outcome_key" in insert_query
    assert insert_args[-1] == "no"
    assert all(args[3] == "no" for _, args in conn.execute_calls)


def test_periodic_orderbook_poll_stores_snapshot_and_liquidity_alert():
    from pmfi.orderbook import poll_polymarket_orderbooks

    class Engine:
        _rules = {
            "rules": {
                "liquidity_wall_v1": {
                    "enabled": True,
                    "min_wall_usd": "10",
                    "levels": 3,
                }
            }
        }

    async def fake_fetch_book(token_id):
        assert token_id == "tok-no"
        return {
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.42", "size": "1"}],
        }

    captured_snapshot = {}

    async def fake_insert_snapshot(conn, **kwargs):
        captured_snapshot.update(kwargs)
        return "snapshot-1"

    async def fake_insert_alert(conn, decision, **kwargs):
        assert decision.evidence["note"].startswith("periodic Polymarket orderbook snapshot")
        captured_snapshot["alert_kwargs"] = kwargs
        return "alert-1"

    alert_handler = AsyncMock()
    result = asyncio.run(
        poll_polymarket_orderbooks(
            _FakePool(object()),
            token_ids=["tok-no", "tok-missing", "tok-no"],
            asset_id_map={
                "tok-no": {
                    "venue_code": "polymarket",
                    "market_id": "00000000-0000-0000-0000-000000000010",
                    "venue_market_id": "condition-1",
                    "outcome_key": "no",
                }
            },
            engine=Engine(),
            alert_handler=alert_handler,
            fetch_book=fake_fetch_book,
            insert_snapshot=fake_insert_snapshot,
            insert_alert_func=fake_insert_alert,
        )
    )

    assert result.attempted == 1
    assert result.fetched == 1
    assert result.snapshots == 1
    assert result.alerts == 1
    assert result.skipped == 1
    assert captured_snapshot["outcome_key"] == "no"
    assert captured_snapshot["is_reconstructed"] is False
    assert captured_snapshot["alert_kwargs"]["dedupe_context"] == "orderbook_poll:tok-no"
    alert_handler.assert_awaited_once()


def test_periodic_orderbook_poll_continues_after_one_token_failure():
    from pmfi.orderbook import poll_polymarket_orderbooks

    class Engine:
        _rules = {
            "rules": {
                "liquidity_wall_v1": {
                    "enabled": True,
                    "min_wall_usd": "10",
                    "levels": 3,
                }
            }
        }

    fetched_tokens = []

    async def fake_fetch_book(token_id):
        fetched_tokens.append(token_id)
        return {
            "bids": [{"price": "0.40", "size": "100"}],
            "asks": [{"price": "0.42", "size": "1"}],
        }

    async def fake_insert_snapshot(conn, **kwargs):
        if kwargs["outcome_key"] == "yes":
            raise RuntimeError("snapshot insert failed")
        return "snapshot-2"

    async def fake_insert_alert(conn, decision, **kwargs):
        return "alert-2"

    alert_handler = AsyncMock(side_effect=RuntimeError("delivery failed"))
    result = asyncio.run(
        poll_polymarket_orderbooks(
            _FakePool(object()),
            token_ids=["tok-yes", "tok-no"],
            asset_id_map={
                "tok-yes": {
                    "venue_code": "polymarket",
                    "market_id": "00000000-0000-0000-0000-000000000011",
                    "venue_market_id": "condition-1",
                    "outcome_key": "yes",
                },
                "tok-no": {
                    "venue_code": "polymarket",
                    "market_id": "00000000-0000-0000-0000-000000000010",
                    "venue_market_id": "condition-1",
                    "outcome_key": "no",
                },
            },
            engine=Engine(),
            alert_handler=alert_handler,
            fetch_book=fake_fetch_book,
            insert_snapshot=fake_insert_snapshot,
            insert_alert_func=fake_insert_alert,
        )
    )

    assert fetched_tokens == ["tok-yes", "tok-no"]
    assert result.attempted == 2
    assert result.fetched == 2
    assert result.snapshots == 1
    assert result.alerts == 1
    alert_handler.assert_awaited_once()


def test_parse_kalshi_orderbook_reconstructs_implied_asks():
    from pmfi.orderbook import compute_book_summary, parse_kalshi_orderbook

    books = parse_kalshi_orderbook({
        "orderbook_fp": {
            "yes_dollars": [["0.0100", "200.00"], ["0.4200", "13.00"]],
            "no_dollars": [["0.0100", "100.00"], ["0.5600", "17.00"]],
        }
    })

    yes_bids, yes_asks = books["yes"]
    no_bids, no_asks = books["no"]
    assert yes_bids[0]["price"] == Decimal("0.4200")
    assert yes_asks[0]["price"] == Decimal("0.4400")
    assert no_bids[0]["price"] == Decimal("0.5600")
    assert no_asks[0]["price"] == Decimal("0.5800")
    assert compute_book_summary(yes_bids, yes_asks)["spread"] == Decimal("0.0200")


def test_kalshi_orderbook_poll_stores_yes_and_no_snapshots():
    from pmfi.orderbook import poll_kalshi_orderbooks

    class Engine:
        _rules = {"rules": {"liquidity_wall_v1": {"enabled": False}}}

    async def fake_fetch_book(ticker, *, depth=100):
        assert ticker == "KX-TEST"
        assert depth == 25
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.4200", "13.00"]],
                "no_dollars": [["0.5600", "17.00"]],
            }
        }

    snapshots = []

    async def fake_insert_snapshot(conn, **kwargs):
        snapshots.append(kwargs)
        return f"snapshot-{len(snapshots)}"

    result = asyncio.run(
        poll_kalshi_orderbooks(
            _FakePool(_FakeKalshiOrderbookConn()),
            tickers=["KX-TEST", "KX-TEST"],
            engine=Engine(),
            depth=25,
            fetch_book=fake_fetch_book,
            insert_snapshot=fake_insert_snapshot,
        )
    )

    assert result.attempted == 1
    assert result.fetched == 1
    assert result.snapshots == 2
    assert result.alerts == 0
    assert [s["outcome_key"] for s in snapshots] == ["yes", "no"]
    assert all(s["venue_code"] == "kalshi" for s in snapshots)
    assert all(s["is_reconstructed"] is True for s in snapshots)
    assert all("orderbook_fp" in s["payload"] for s in snapshots)
    assert snapshots[0]["best_bid"] == Decimal("0.4200")
    assert snapshots[0]["best_ask"] == Decimal("0.4400")


def test_kalshi_orderbook_poll_continues_after_one_outcome_failure():
    from pmfi.orderbook import poll_kalshi_orderbooks

    class Engine:
        _rules = {"rules": {"liquidity_wall_v1": {"enabled": False}}}

    async def fake_fetch_book(ticker, *, depth=100):
        assert ticker == "KX-TEST"
        assert depth == 100
        return {
            "orderbook_fp": {
                "yes_dollars": [["0.4200", "13.00"]],
                "no_dollars": [["0.5600", "17.00"]],
            }
        }

    stored_outcomes = []

    async def fake_insert_snapshot(conn, **kwargs):
        if kwargs["outcome_key"] == "yes":
            raise RuntimeError("yes insert failed")
        stored_outcomes.append(kwargs["outcome_key"])
        return "snapshot-no"

    result = asyncio.run(
        poll_kalshi_orderbooks(
            _FakePool(_FakeKalshiOrderbookConn()),
            tickers=["KX-TEST"],
            engine=Engine(),
            fetch_book=fake_fetch_book,
            insert_snapshot=fake_insert_snapshot,
        )
    )

    assert result.attempted == 1
    assert result.fetched == 1
    assert result.snapshots == 1
    assert stored_outcomes == ["no"]
