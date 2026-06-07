"""Tests for asset_id -> venue_market_id pre-normalization resolution in process_event.

These are pure fixture tests — no asyncpg/DB required.
Uses resolve_asset_outcome directly from runner.py (no hand-copied replica).
"""
from __future__ import annotations

import pytest

from pmfi.domain import RawEvent
from pmfi.pipeline.normalize import normalize_event
from pmfi.pipeline.runner import resolve_asset_outcome


def _live_event(
    asset_id: str | None,
    market: str | None = None,
    outcome: str | None = None,
) -> RawEvent:
    """Simulate a Polymarket WS event (last_trade_price) with optional asset_id."""
    payload: dict = {"price": "0.5", "size": "10", "side": "BUY"}
    if asset_id is not None:
        payload["asset_id"] = asset_id
    if market is not None:
        payload["market"] = market
    if outcome is not None:
        payload["outcome"] = outcome
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
        "is_binary": True,
    },
    "token_no_abc": {
        "venue_market_id": "condition_xyz",
        "venue_code": "polymarket",
        "market_id": "00000000-0000-0000-0000-000000000001",
        "outcome_key": "no",
        "outcome_label": "No",
        "is_binary": True,
    },
    "token_biden": {
        "venue_market_id": "condition_election",
        "venue_code": "polymarket",
        "market_id": "00000000-0000-0000-0000-000000000002",
        "outcome_key": "biden",
        "outcome_label": "Biden",
        "is_binary": False,
    },
}


# ---------------------------------------------------------------------------
# Core resolution tests
# ---------------------------------------------------------------------------

def test_asset_id_resolves_venue_market_id():
    raw = _live_event("token_yes_abc")
    assert raw.venue_market_id is None

    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.venue_market_id == "condition_xyz"
    assert missing is None


def test_resolution_propagates_to_normalizer():
    """After resolution, normalize_event produces the correct venue_market_id."""
    resolved, _ = resolve_asset_outcome(_live_event("token_yes_abc"), _ASSET_MAP)
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.venue_market_id == "condition_xyz"


def test_no_map_leaves_venue_market_id_as_unknown():
    """Without a map, the normalizer falls back to 'unknown'."""
    raw = _live_event("token_yes_abc")  # venue_market_id=None
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_market_id == "unknown"


def test_unknown_asset_id_returns_missing_asset_id():
    """asset_id not in map AND outcome unknown: returns missing_asset_id for dead-letter."""
    raw = _live_event("token_not_in_map")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert missing == "token_not_in_map"
    assert resolved.venue_market_id is None


def test_unknown_asset_id_normalizer_uses_unknown():
    """asset_id not in map: normalizer still produces venue_market_id='unknown'."""
    raw = _live_event("token_not_in_map")
    resolved, _ = resolve_asset_outcome(raw, _ASSET_MAP)
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.venue_market_id == "unknown"


# ---------------------------------------------------------------------------
# No-clobber: venue_market_id already set must be preserved
# ---------------------------------------------------------------------------

def test_event_with_market_field_vmid_preserved_outcome_patched():
    """If event has market field (vmid set) but no outcome, vmid is preserved and outcome is injected."""
    raw = _live_event("token_yes_abc", market="condition_original")
    assert raw.venue_market_id == "condition_original"

    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    # venue_market_id must NOT be clobbered — original preserved
    assert resolved.venue_market_id == "condition_original"
    assert missing is None
    # outcome was missing so it should be injected from the map
    assert resolved.payload["outcome"] == "yes"
    # market field in payload should also remain original (no-clobber)
    assert resolved.payload.get("market") == "condition_original"


def test_existing_valid_outcome_not_remapped():
    """When a valid outcome is already present, it must not be overwritten."""
    raw = _live_event("token_yes_abc", outcome="no")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    # outcome was already "no" (valid, not unknown) — must not be changed
    assert resolved.payload["outcome"] == "no"
    assert missing is None


def test_outcome_unknown_string_treated_as_missing():
    """Outcome value 'unknown' (case-insensitive) is treated as missing and gets mapped."""
    raw = _live_event("token_yes_abc", outcome="Unknown")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.payload["outcome"] == "yes"
    assert missing is None


# ---------------------------------------------------------------------------
# Non-binary token path
# ---------------------------------------------------------------------------

def test_nonbinary_token_no_yes_no_injected():
    """Non-binary token in map: no yes/no outcome injected into payload."""
    raw = _live_event("token_biden")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert missing is None
    # outcome must NOT be "yes" or "no"
    injected = resolved.payload.get("outcome")
    assert injected not in ("yes", "no"), f"non-binary token must not inject yes/no, got {injected!r}"


def test_nonbinary_token_does_not_inject_outcome():
    """Non-binary token: no outcome injected, no marker, missing_asset_id is None.

    The normalizer receives the event without an outcome key and yields
    outcome_key='unknown', which assess_data_quality flags as degraded.
    """
    raw = _live_event("token_biden")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert missing is None
    assert "outcome" not in resolved.payload, (
        f"non-binary token must not inject any outcome, got {resolved.payload.get('outcome')!r}"
    )
    assert "_multi_outcome_unsupported" not in resolved.payload, (
        "dead marker must not be written to payload"
    )
    # Normalizer should yield outcome_key="unknown" for unresolved outcome
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.outcome_key == "unknown"


def test_nonbinary_token_vmid_filled():
    """Non-binary token still resolves venue_market_id when it was None."""
    raw = _live_event("token_biden")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.venue_market_id == "condition_election"
    assert missing is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_no_asset_id_in_payload_leaves_unchanged():
    """Event with no asset_id field: resolution does nothing."""
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"price": "0.5", "size": "10", "side": "BUY"},
        venue_market_id=None,
    )
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.venue_market_id is None
    assert missing is None


def test_none_map_skips_resolution():
    raw = _live_event("token_yes_abc")
    resolved, missing = resolve_asset_outcome(raw, None)
    assert resolved.venue_market_id is None
    assert missing is None


def test_non_polymarket_venue_skips_resolution():
    """resolve_asset_outcome only applies to polymarket venue_code."""
    raw = RawEvent(
        venue_code="kalshi",
        source_channel="ws",
        source_event_type="trade",
        payload={"asset_id": "token_yes_abc", "price": "0.5", "count": "10", "yes_no": "yes", "taker_side": "buy"},
        venue_market_id=None,
    )
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.venue_market_id is None
    assert missing is None


def test_no_token_maps_to_outcome_no():
    """token_no_abc has outcome_key='no' in the map; resolution injects it into payload."""
    raw = _live_event("token_no_abc")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.venue_market_id == "condition_xyz"
    assert resolved.payload["outcome"] == "no"
    assert missing is None
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.outcome_key == "no"


def test_yes_token_maps_to_outcome_yes():
    """token_yes_abc has outcome_key='yes'; resolution preserves correct label."""
    raw = _live_event("token_yes_abc")
    resolved, missing = resolve_asset_outcome(raw, _ASSET_MAP)
    assert resolved.payload["outcome"] == "yes"
    assert missing is None
    trade = normalize_event(resolved)
    assert trade is not None
    assert trade.outcome_key == "yes"
