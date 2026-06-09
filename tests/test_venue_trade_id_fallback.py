"""Tests for venue_trade_id fallback: trade_id preferred, id used when trade_id absent."""
from __future__ import annotations

from pmfi.domain import RawEvent
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture


def _pm_raw(extra: dict) -> RawEvent:
    payload = {"market": "test-mkt", "price": "0.5", "size": "100", "outcome": "yes", "side": "buy"}
    payload.update(extra)
    return RawEvent(
        venue_code="polymarket",
        source_channel="market_ws",
        source_event_type="last_trade_price",
        payload=payload,
    )


def _ks_raw(extra: dict) -> RawEvent:
    payload = {"ticker": "KS-TEST", "price": "0.5", "count": "100", "yes_no": "yes", "taker_side": "buy"}
    payload.update(extra)
    return RawEvent(
        venue_code="kalshi",
        source_channel="trade_ws",
        source_event_type="trade",
        payload=payload,
    )


# --- Polymarket ---

def test_polymarket_id_only_uses_id_as_venue_trade_id():
    """Payload with only 'id' (no 'trade_id') -> venue_trade_id == str(id)."""
    trade = normalize_polymarket_fixture(_pm_raw({"id": "abc-123"}))
    assert trade.venue_trade_id == "abc-123"


def test_polymarket_trade_id_wins_over_id():
    """When both 'trade_id' and 'id' are present, trade_id wins."""
    trade = normalize_polymarket_fixture(_pm_raw({"trade_id": "tid-999", "id": "abc-123"}))
    assert trade.venue_trade_id == "tid-999"


def test_polymarket_trade_id_alone():
    """Payload with only 'trade_id' -> venue_trade_id == str(trade_id) (unchanged baseline)."""
    trade = normalize_polymarket_fixture(_pm_raw({"trade_id": "tid-42"}))
    assert trade.venue_trade_id == "tid-42"


def test_polymarket_neither_id_field_gives_none():
    """Payload with neither 'trade_id' nor 'id' -> venue_trade_id is None."""
    trade = normalize_polymarket_fixture(_pm_raw({}))
    assert trade.venue_trade_id is None


def test_polymarket_id_is_stringified():
    """Numeric id is stringified."""
    trade = normalize_polymarket_fixture(_pm_raw({"id": 7890}))
    assert trade.venue_trade_id == "7890"


# --- Kalshi parity ---

def test_kalshi_id_only_uses_id_as_venue_trade_id():
    """Kalshi payload with only 'id' (no 'trade_id') -> venue_trade_id == str(id)."""
    trade = normalize_kalshi_fixture(_ks_raw({"id": "ks-id-456"}))
    assert trade.venue_trade_id == "ks-id-456"


def test_kalshi_trade_id_wins_over_id():
    """When both present, trade_id wins."""
    trade = normalize_kalshi_fixture(_ks_raw({"trade_id": "ks-tid-1", "id": "ks-id-2"}))
    assert trade.venue_trade_id == "ks-tid-1"


def test_kalshi_neither_id_field_gives_none():
    """Kalshi payload with neither field -> venue_trade_id is None."""
    trade = normalize_kalshi_fixture(_ks_raw({}))
    assert trade.venue_trade_id is None
