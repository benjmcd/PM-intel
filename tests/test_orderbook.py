"""Tests for orderbook module (no live API, no asyncpg required)."""
from decimal import Decimal
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
