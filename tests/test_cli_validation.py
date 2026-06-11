"""Unit tests for _select_ingest_venues — pure function, no DB/network.

Semantics: enabled venues with no resolved subscription targets are DROPPED (with
an informational message) so a mixed-venue operator who only watches one venue
still ingests the usable one. The caller hard-fails only when nothing is usable.
"""
from pmfi.cli import _select_ingest_venues, _should_poll_orderbooks


def test_happy_path_polymarket_kept_no_messages():
    usable, messages = _select_ingest_venues(
        venues=["polymarket"],
        poly_ids=["token-abc", "token-def"],
        kalshi_tickers=[],
    )
    assert usable == ["polymarket"]
    assert messages == []


def test_happy_path_kalshi_kept_no_messages():
    usable, messages = _select_ingest_venues(
        venues=["kalshi"],
        poly_ids=[],
        kalshi_tickers=["KXBTC-25"],
    )
    assert usable == ["kalshi"]
    assert messages == []


def test_happy_path_both_venues_kept():
    usable, messages = _select_ingest_venues(
        venues=["polymarket", "kalshi"],
        poly_ids=["token-abc"],
        kalshi_tickers=["KXBTC-25"],
    )
    assert usable == ["polymarket", "kalshi"]
    assert messages == []


def test_polymarket_no_poly_ids_dropped_with_message():
    usable, messages = _select_ingest_venues(
        venues=["polymarket"],
        poly_ids=[],
        kalshi_tickers=[],
    )
    assert usable == []
    assert len(messages) == 1
    assert "Polymarket" in messages[0]
    assert "discover" in messages[0]


def test_kalshi_no_tickers_dropped_with_message():
    usable, messages = _select_ingest_venues(
        venues=["kalshi"],
        poly_ids=[],
        kalshi_tickers=[],
    )
    assert usable == []
    assert len(messages) == 1
    assert "Kalshi" in messages[0]
    assert "discover" in messages[0]


def test_mixed_venues_drops_empty_keeps_usable():
    """REGRESSION GUARD: both venues enabled, only kalshi watched -> run kalshi,
    drop polymarket with a message (do NOT hard-fail the whole ingest)."""
    usable, messages = _select_ingest_venues(
        venues=["polymarket", "kalshi"],
        poly_ids=[],  # no polymarket tokens resolved
        kalshi_tickers=["KXBTC-25"],  # kalshi has a watched ticker
    )
    assert usable == ["kalshi"]
    assert len(messages) == 1
    assert "Polymarket" in messages[0]


def test_mixed_venues_drops_empty_keeps_polymarket():
    usable, messages = _select_ingest_venues(
        venues=["polymarket", "kalshi"],
        poly_ids=["token-abc"],
        kalshi_tickers=[],  # no kalshi tickers watched
    )
    assert usable == ["polymarket"]
    assert len(messages) == 1
    assert "Kalshi" in messages[0]


def test_both_empty_returns_no_usable_with_two_messages():
    usable, messages = _select_ingest_venues(
        venues=["polymarket", "kalshi"],
        poly_ids=[],
        kalshi_tickers=[],
    )
    assert usable == []
    assert len(messages) == 2


def test_kalshi_only_ignores_empty_poly_ids():
    # kalshi-only venues: empty poly_ids is irrelevant, no message about polymarket
    usable, messages = _select_ingest_venues(
        venues=["kalshi"],
        poly_ids=[],
        kalshi_tickers=["KXBTC-25"],
    )
    assert usable == ["kalshi"]
    assert messages == []


def test_polymarket_only_ignores_empty_kalshi_tickers():
    usable, messages = _select_ingest_venues(
        venues=["polymarket"],
        poly_ids=["token-abc"],
        kalshi_tickers=[],
    )
    assert usable == ["polymarket"]
    assert messages == []


def test_orderbook_polling_requires_polymarket_live_venue():
    assert _should_poll_orderbooks(
        orderbook_enabled=True,
        live_venues=["polymarket"],
    )
    assert not _should_poll_orderbooks(
        orderbook_enabled=True,
        live_venues=["kalshi"],
    )
    assert not _should_poll_orderbooks(
        orderbook_enabled=False,
        live_venues=["polymarket"],
    )
