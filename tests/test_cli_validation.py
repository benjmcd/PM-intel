"""Offline validation for registry-driven ingest venue selection."""
from __future__ import annotations

import pmfi.cli as cli
from pmfi.commands import _shared
from pmfi.pipeline.venue_dispatch import resolve_venue_subscription_targets


def _watched(*venue_codes: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for venue_code in venue_codes:
        if venue_code == "polymarket":
            rows.append(
                {
                    "market_id": "poly-market-1",
                    "venue_code": "polymarket",
                    "venue_market_id": "poly-condition-1",
                    "title": "Polymarket test market",
                }
            )
        elif venue_code == "kalshi":
            rows.append(
                {
                    "market_id": "kalshi-market-1",
                    "venue_code": "kalshi",
                    "venue_market_id": "KXBTC-25",
                    "title": "Kalshi test market",
                }
            )
    return rows


def _asset_map(*token_ids: str) -> dict[str, dict[str, str]]:
    return {
        token_id: {
            "venue_code": "polymarket",
            "market_id": "poly-market-1",
            "venue_market_id": "poly-condition-1",
        }
        for token_id in token_ids
    }


def test_obsolete_select_ingest_venues_surface_is_not_exported() -> None:
    assert not hasattr(cli, "_select_ingest_venues")
    assert not hasattr(_shared, "_select_ingest_venues")


def test_happy_path_polymarket_kept_no_messages() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket"],
        _watched("polymarket"),
        _asset_map("token-abc", "token-def"),
    )
    assert targets == {"polymarket": ["token-abc", "token-def"]}
    assert messages == []


def test_happy_path_kalshi_kept_no_messages() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["kalshi"],
        _watched("kalshi"),
        {},
    )
    assert targets == {"kalshi": ["KXBTC-25"]}
    assert messages == []


def test_happy_path_both_venues_kept() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket", "kalshi"],
        _watched("polymarket", "kalshi"),
        _asset_map("token-abc"),
    )
    assert targets == {"polymarket": ["token-abc"], "kalshi": ["KXBTC-25"]}
    assert messages == []


def test_polymarket_no_poly_ids_dropped_with_message() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket"],
        _watched("polymarket"),
        {},
    )
    assert targets == {}
    assert len(messages) == 1
    assert "Polymarket" in messages[0]
    assert "discover" in messages[0]


def test_kalshi_no_tickers_dropped_with_message() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["kalshi"],
        [],
        {},
    )
    assert targets == {}
    assert len(messages) == 1
    assert "Kalshi" in messages[0]
    assert "discover" in messages[0]


def test_mixed_venues_drops_empty_keeps_usable_kalshi() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket", "kalshi"],
        _watched("polymarket", "kalshi"),
        {},
    )
    assert targets == {"kalshi": ["KXBTC-25"]}
    assert len(messages) == 1
    assert "Polymarket" in messages[0]


def test_mixed_venues_drops_empty_keeps_polymarket() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket", "kalshi"],
        _watched("polymarket"),
        _asset_map("token-abc"),
    )
    assert targets == {"polymarket": ["token-abc"]}
    assert len(messages) == 1
    assert "Kalshi" in messages[0]


def test_both_empty_returns_no_usable_with_two_messages() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket", "kalshi"],
        _watched("polymarket"),
        {},
    )
    assert targets == {}
    assert len(messages) == 2


def test_kalshi_only_ignores_empty_poly_ids() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["kalshi"],
        _watched("kalshi"),
        {},
    )
    assert targets == {"kalshi": ["KXBTC-25"]}
    assert messages == []


def test_polymarket_only_ignores_empty_kalshi_tickers() -> None:
    targets, messages = resolve_venue_subscription_targets(
        ["polymarket"],
        _watched("polymarket"),
        _asset_map("token-abc"),
    )
    assert targets == {"polymarket": ["token-abc"]}
    assert messages == []
