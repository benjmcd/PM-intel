"""Tests for asset_id → venue_market_id pre-normalization resolution in process_event.

These are pure fixture tests — no asyncpg/DB required.
"""
from __future__ import annotations
import dataclasses

import pytest

from pmfi.domain import RawEvent
from pmfi.pipeline.normalize import normalize_event


def _live_event(asset_id: str | None, market: str | None = None) -> RawEvent:
    """Simulate a Polymarket WS event (last_trade_price) with optional asset_id."""
    payload: dict = {"price": "0.5", "size": "10", "side": "BUY", "outcome": "Yes"}
    if asset_id is not None:
        payload["asset_id"] = asset_id
    if market is not None:
        payload["market"] = market
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload=payload,
        venue_market_id=market,  # None when event has no 'market' field
    )


_ASSET_MAP = {
    "token_yes_abc": {
        "venue_market_id": "condition_xyz",
        "venue_code": "polymarket",
        "market_id": "00000000-0000-0000-0000-000000000001",
        "outcome_key": "yes",
        "outcome_label": "Yes",
    },
    "token_no_abc": {
        "venue_market_id": "condition_xyz",
        "venue_code": "polymarket",
        "market_id": "00000000-0000-0000-0000-000000000001",
        "outcome_key": "no",
        "outcome_label": "No",
    },
}


def _resolve(raw: RawEvent, asset_id_map: dict | None) -> RawEvent:
    """Replica of the resolution logic in process_event."""
    if asset_id_map and raw.venue_market_id is None:
        _asset_id = raw.payload.get("asset_id")
        if _asset_id:
            _info = asset_id_map.get(str(_asset_id))
            if _info:
                raw = dataclasses.replace(raw, venue_market_id=_info["venue_market_id"])
    return raw


def test_asset_id_resolves_venue_market_id():
    raw = _live_event("token_yes_abc")
    assert raw.venue_market_id is None

    resolved = _resolve(raw, _ASSET_MAP)
    assert resolved.venue_market_id == "condition_xyz"


def test_resolution_propagates_to_normalizer():
    """After resolution, normalize_event produces the correct venue_market_id."""
    raw = _resolve(_live_event("token_yes_abc"), _ASSET_MAP)
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_market_id == "condition_xyz"


def test_no_map_leaves_venue_market_id_as_unknown():
    """Without a map, the normalizer falls back to 'unknown'."""
    raw = _live_event("token_yes_abc")  # venue_market_id=None
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_market_id == "unknown"


def test_unknown_asset_id_leaves_venue_market_id_none():
    """asset_id not in map: venue_market_id stays None, normalizer uses 'unknown'."""
    raw = _live_event("token_not_in_map")
    resolved = _resolve(raw, _ASSET_MAP)
    assert resolved.venue_market_id is None
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.venue_market_id == "unknown"


def test_event_with_market_field_unaffected():
    """If event already has market field, resolution is a no-op."""
    raw = _live_event("token_yes_abc", market="condition_original")
    assert raw.venue_market_id == "condition_original"
    resolved = _resolve(raw, _ASSET_MAP)
    # venue_market_id is not None so resolution is skipped
    assert resolved.venue_market_id == "condition_original"


def test_no_asset_id_in_payload_leaves_unchanged():
    """Event with no asset_id field: resolution does nothing."""
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"price": "0.5", "size": "10", "side": "BUY", "outcome": "Yes"},
        venue_market_id=None,
    )
    resolved = _resolve(raw, _ASSET_MAP)
    assert resolved.venue_market_id is None


def test_none_map_skips_resolution():
    raw = _live_event("token_yes_abc")
    resolved = _resolve(raw, None)
    assert resolved.venue_market_id is None
