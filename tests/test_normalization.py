from decimal import Decimal
from pathlib import Path

import pytest

from pmfi.fixtures import load_raw_event
from pmfi.normalization import NormalizationError, normalize_kalshi_fixture, normalize_polymarket_fixture

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_normalize_polymarket_fixture_computes_notionals():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    trade = normalize_polymarket_fixture(raw)
    assert trade.venue_code == "polymarket"
    assert trade.price == Decimal("0.42")
    assert trade.contracts == Decimal("80000")
    assert trade.capital_at_risk_usd == Decimal("33600.00")
    assert trade.payout_notional_usd == Decimal("80000")
    assert trade.directional_side == "yes"


def test_normalize_kalshi_fixture_computes_notionals():
    raw = load_raw_event(FIXTURES / "kalshi_trade.json")
    trade = normalize_kalshi_fixture(raw)
    assert trade.venue_code == "kalshi"
    assert trade.price == Decimal("0.37")
    assert trade.contracts == Decimal("72000")
    assert trade.capital_at_risk_usd == Decimal("26640.00")
    assert trade.payout_notional_usd == Decimal("72000")
    assert trade.directional_side == "yes"


def test_invalid_price_fails_closed():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    raw.payload["price"] = "1.42"
    with pytest.raises(NormalizationError):
        normalize_polymarket_fixture(raw)


def test_malformed_optional_fee_is_warning_not_trade_drop():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    raw.payload["fee_usd"] = "not-a-decimal"

    trade = normalize_polymarket_fixture(raw)

    assert trade.fee_usd is None
    assert "invalid_fee_usd" in trade.warnings


def test_normalize_polymarket_live_ws_format():
    """Prove the pipeline handles real Polymarket CLOB WS trade event structure."""
    raw = load_raw_event(FIXTURES / "polymarket_live_ws_trade.json")
    trade = normalize_polymarket_fixture(raw)
    assert trade.venue_code == "polymarket"
    assert trade.price == Decimal("0.65")
    assert trade.contracts == Decimal("50000")
    assert trade.capital_at_risk_usd == Decimal("32500.00")
    assert trade.outcome_key == "yes"
    assert trade.directional_side == "yes"
    assert trade.aggressor_side == "buy"
    assert trade.venue_market_id == "0xabc1234condition"


def test_normalize_kalshi_live_ws_cent_price():
    """Live Kalshi WS: integer cent price + taker_side as direction (yes/no not buy/sell)."""
    raw = load_raw_event(FIXTURES / "kalshi_live_ws_trade.json")
    trade = normalize_kalshi_fixture(raw)
    assert trade.venue_code == "kalshi"
    assert trade.price == Decimal("0.37")
    assert trade.contracts == Decimal("72000")
    assert trade.capital_at_risk_usd == Decimal("26640.00")
    assert trade.venue_market_id == "KXEXAMPLE-26JUN03"
    # taker_side "yes" maps directly to directional_side (live WS format)
    assert trade.directional_side == "yes"
    assert trade.outcome_key == "yes"
