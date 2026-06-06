from __future__ import annotations
import pytest
from decimal import Decimal
from pmfi.domain import RawEvent
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError
from pmfi.pipeline.normalize import normalize_event


def _pm_raw(price: str, size: str, outcome: str = "yes", side: str = "buy") -> RawEvent:
    return RawEvent(
        venue_code="polymarket",
        source_channel="market_ws",
        source_event_type="last_trade_price",
        payload={"market": "test-mkt", "price": price, "size": size, "outcome": outcome, "side": side},
    )


def _ks_raw(price: str, count: str, yes_no: str = "yes", taker_side: str = "buy") -> RawEvent:
    return RawEvent(
        venue_code="kalshi",
        source_channel="trade_ws",
        source_event_type="trade",
        payload={"ticker": "KS-TEST", "price": price, "count": count, "yes_no": yes_no, "taker_side": taker_side},
    )


def test_polymarket_price_zero():
    trade = normalize_polymarket_fixture(_pm_raw("0", "10000"))
    assert trade.price == Decimal("0")
    assert trade.capital_at_risk_usd == Decimal("0")


def test_polymarket_price_one():
    trade = normalize_polymarket_fixture(_pm_raw("1", "5000"))
    assert trade.price == Decimal("1")
    assert trade.capital_at_risk_usd == Decimal("5000")


def test_polymarket_rejects_price_above_one():
    with pytest.raises(NormalizationError):
        normalize_polymarket_fixture(_pm_raw("1.01", "5000"))


def test_polymarket_rejects_price_below_zero():
    with pytest.raises(NormalizationError):
        normalize_polymarket_fixture(_pm_raw("-0.01", "5000"))


def test_polymarket_rejects_non_numeric_price():
    with pytest.raises(NormalizationError):
        normalize_polymarket_fixture(_pm_raw("abc", "5000"))


def test_polymarket_zero_contracts():
    trade = normalize_polymarket_fixture(_pm_raw("0.5", "0"))
    assert trade.contracts == Decimal("0")
    assert trade.capital_at_risk_usd == Decimal("0")


def test_polymarket_oi_extracted():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="market_ws",
        source_event_type="last_trade_price",
        payload={"market": "oi-mkt", "price": "0.5", "size": "1000", "open_interest": "50000"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.open_interest_contracts == Decimal("50000")


def test_polymarket_oi_absent_is_none():
    trade = normalize_polymarket_fixture(_pm_raw("0.5", "1000"))
    assert trade.open_interest_contracts is None


def test_kalshi_directional_side_no():
    trade = normalize_kalshi_fixture(_ks_raw("0.6", "5000", yes_no="no"))
    assert trade.directional_side == "no"
    assert trade.outcome_key == "no"


def test_kalshi_directional_side_unknown():
    trade = normalize_kalshi_fixture(_ks_raw("0.6", "5000", yes_no="maybe"))
    assert trade.directional_side == "unknown"


def test_normalize_event_unknown_venue_returns_none():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="test",
        source_event_type="trade",
        payload={},
    )
    result = normalize_event(raw)
    assert result is None


def test_replay_skips_malformed_fixture():
    from pathlib import Path
    from pmfi.replay import replay_fixtures
    fixture_dir = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw"
    results = replay_fixtures(fixture_dir)
    # malformed_payload.json should be skipped; valid fixtures should still return results
    markets = [r.trade.venue_market_id for r in results]
    assert "pm-bad-market" not in markets
    assert len(results) >= 1
