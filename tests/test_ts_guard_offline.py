"""Offline tests for the timestamp sanity guard in parse_ts and _parse_exchange_ts.

No DB or network required. These run as part of the default offline verify.py suite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# parse_ts guard (normalization.py)
# ---------------------------------------------------------------------------

def test_parse_ts_future_returns_none():
    """A value 2h in the future must be rejected and return None."""
    from pmfi.normalization import parse_ts

    future = datetime.now(timezone.utc) + timedelta(hours=2)
    # Pass as an ISO string so parse_ts exercises the string branch.
    result = parse_ts(future.isoformat())
    assert result is None, f"Expected None for future ts, got {result!r}"


def test_parse_ts_past_returns_none():
    """A value 60 days in the past must be rejected and return None."""
    from pmfi.normalization import parse_ts

    past = datetime.now(timezone.utc) - timedelta(days=60)
    result = parse_ts(past.isoformat())
    assert result is None, f"Expected None for far-past ts, got {result!r}"


def test_parse_ts_normal_value_returned():
    """A recent timestamp (within bounds) must be parsed and returned."""
    from pmfi.normalization import parse_ts

    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = parse_ts(recent.isoformat())
    assert result is not None, "Expected a parsed datetime, got None"
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_parse_ts_numeric_future_returns_none():
    """A numeric epoch 2h in the future must be rejected."""
    from pmfi.normalization import parse_ts

    future_epoch = (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp()
    result = parse_ts(future_epoch)
    assert result is None


def test_parse_ts_numeric_past_returns_none():
    """A numeric epoch 60 days in the past must be rejected."""
    from pmfi.normalization import parse_ts

    past_epoch = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    result = parse_ts(past_epoch)
    assert result is None


def test_parse_ts_numeric_recent_returned():
    """A numeric epoch for a recent timestamp must be returned."""
    from pmfi.normalization import parse_ts

    recent_epoch = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    result = parse_ts(recent_epoch)
    assert result is not None
    assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _parse_exchange_ts guard (adapters/polymarket.py)
# ---------------------------------------------------------------------------

def test_parse_exchange_ts_future_returns_none():
    """A payload timestamp 2h in the future must return None."""
    from pmfi.adapters.polymarket import _parse_exchange_ts

    future_epoch = (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp()
    ev = {"timestamp": str(future_epoch)}
    result = _parse_exchange_ts(ev)
    assert result is None, f"Expected None for future ts, got {result!r}"


def test_parse_exchange_ts_past_returns_none():
    """A payload timestamp 60 days in the past must return None."""
    from pmfi.adapters.polymarket import _parse_exchange_ts

    past_epoch = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
    ev = {"timestamp": str(past_epoch)}
    result = _parse_exchange_ts(ev)
    assert result is None, f"Expected None for far-past ts, got {result!r}"


def test_parse_exchange_ts_normal_returned():
    """A recent exchange timestamp must be parsed and returned."""
    from pmfi.adapters.polymarket import _parse_exchange_ts

    recent_epoch = (datetime.now(timezone.utc) - timedelta(minutes=3)).timestamp()
    ev = {"timestamp": str(recent_epoch)}
    result = _parse_exchange_ts(ev)
    assert result is not None
    assert isinstance(result, datetime)
    assert result.tzinfo is not None


def test_parse_exchange_ts_milliseconds_future_returns_none():
    """A millisecond-epoch payload timestamp 2h in the future must return None."""
    from pmfi.adapters.polymarket import _parse_exchange_ts

    future_ms = (datetime.now(timezone.utc) + timedelta(hours=2)).timestamp() * 1000
    ev = {"ts": str(future_ms)}
    result = _parse_exchange_ts(ev)
    assert result is None


def test_parse_exchange_ts_missing_key_returns_none():
    """No recognized timestamp key in payload must return None (existing behaviour)."""
    from pmfi.adapters.polymarket import _parse_exchange_ts

    result = _parse_exchange_ts({"price": "0.55"})
    assert result is None


# ---------------------------------------------------------------------------
# Orderbook Decimal exactness (no DB required)
# ---------------------------------------------------------------------------

def test_orderbook_parse_levels_stays_decimal():
    """parse_book_levels must return Decimal values, not floats."""
    from decimal import Decimal
    from pmfi.orderbook import parse_book_levels

    raw = {
        "bids": [{"price": "0.12345678", "size": "9999.00000001"}],
        "asks": [{"price": "0.87654300", "size": "1234.56789012"}],
    }
    bids, asks = parse_book_levels(raw)
    assert isinstance(bids[0]["price"], Decimal)
    assert isinstance(bids[0]["size"], Decimal)
    assert isinstance(asks[0]["price"], Decimal)
    assert isinstance(asks[0]["size"], Decimal)
    # Exact value preserved — no float rounding
    assert bids[0]["price"] == Decimal("0.12345678")
    assert bids[0]["size"] == Decimal("9999.00000001")


def test_orderbook_compute_summary_stays_decimal():
    """compute_book_summary must return Decimal values."""
    from decimal import Decimal
    from pmfi.orderbook import compute_book_summary

    bids = [{"price": Decimal("0.64"), "size": Decimal("1000")}]
    asks = [{"price": Decimal("0.66"), "size": Decimal("500")}]
    summary = compute_book_summary(bids, asks)
    for key in ("best_bid", "best_ask", "spread", "top_depth_usd"):
        assert isinstance(summary[key], Decimal), (
            f"compute_book_summary[{key!r}] should be Decimal, got {type(summary[key])!r}"
        )
