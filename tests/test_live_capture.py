"""Tests proving the real captured Polymarket live book fixture is handled correctly.

No network or DB access. Pure fixture-based. Safe for default offline verify.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_BOOK = FIXTURES / "polymarket_live_book_sample.json"


# ---------------------------------------------------------------------------
# 1. Shape contract — locks the real-world event envelope and payload shape
# ---------------------------------------------------------------------------


def test_live_book_fixture_shape():
    """The promoted live fixture has the expected envelope fields and no 'outcome' in payload."""
    import json

    data = json.loads(LIVE_BOOK.read_text(encoding="utf-8-sig"))

    assert data["source_event_type"] == "book"
    assert data["venue_code"] == "polymarket"
    assert data["source_channel"] == "ws_clob"

    payload = data["payload"]
    assert "market" in payload
    assert "asset_id" in payload
    assert "outcome" not in payload, "live book events must NOT have an 'outcome' key"


# ---------------------------------------------------------------------------
# 2. Orderbook parser — parse_book_levels + compute_book_summary on real data
# ---------------------------------------------------------------------------


def test_live_book_parse_levels_returns_sorted_bids_and_asks():
    """parse_book_levels handles the real captured payload without error and sorts correctly."""
    import json
    from pmfi.orderbook import parse_book_levels

    data = json.loads(LIVE_BOOK.read_text(encoding="utf-8-sig"))
    payload = data["payload"]

    bids, asks = parse_book_levels(payload)

    assert len(bids) > 0, "expected non-empty bids from live book fixture"
    assert len(asks) > 0, "expected non-empty asks from live book fixture"

    # bids are sorted descending
    for i in range(len(bids) - 1):
        assert bids[i]["price"] >= bids[i + 1]["price"], "bids must be sorted descending"

    # asks are sorted ascending
    for i in range(len(asks) - 1):
        assert asks[i]["price"] <= asks[i + 1]["price"], "asks must be sorted ascending"

    # each level has Decimal price and size
    assert isinstance(bids[0]["price"], Decimal)
    assert isinstance(asks[0]["price"], Decimal)


def test_live_book_compute_summary_best_bid_and_ask():
    """compute_book_summary returns a sensible best_bid/best_ask from real data."""
    import json
    from pmfi.orderbook import compute_book_summary, parse_book_levels

    data = json.loads(LIVE_BOOK.read_text(encoding="utf-8-sig"))
    payload = data["payload"]

    bids, asks = parse_book_levels(payload)
    summary = compute_book_summary(bids, asks)

    assert summary["best_bid"] is not None
    assert summary["best_ask"] is not None
    assert Decimal("0") < summary["best_bid"] < Decimal("1")
    assert Decimal("0") < summary["best_ask"] < Decimal("1")
    # spread = ask - bid; for an active market this should be positive
    assert summary["spread"] is not None
    assert summary["spread"] >= Decimal("0")
    assert summary["top_depth_usd"] > Decimal("0")


# ---------------------------------------------------------------------------
# 3. Non-trade handling — normalize_event must NOT silently produce a trade
# ---------------------------------------------------------------------------


def test_live_book_event_is_not_normalized_as_trade():
    """A real live 'book' event must return None from normalize_event (not a trade)."""
    from pmfi.fixtures import load_raw_event
    from pmfi.normalization import NormalizationError
    from pmfi.pipeline.normalize import normalize_event

    raw = load_raw_event(LIVE_BOOK)

    # normalize_event returns None for benign non-trade events (source_event_type not in
    # _POLYMARKET_TRADE_EVENT_TYPES). It must not silently produce a NormalizedTrade.
    try:
        result = normalize_event(raw)
        assert result is None, (
            f"normalize_event returned a NormalizedTrade for a live book event: {result}"
        )
    except NormalizationError:
        # Also acceptable — the code raises to signal a non-trade path
        pass
