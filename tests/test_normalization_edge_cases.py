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


def test_polymarket_buy_yes_is_bullish():
    raw = RawEvent(
        venue_code="polymarket", source_channel="ws", source_event_type="trade",
        payload={"market": "m", "price": "0.6", "size": "1000", "outcome": "yes", "side": "buy"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.directional_side == "yes"


def test_polymarket_sell_yes_is_bearish():
    raw = RawEvent(
        venue_code="polymarket", source_channel="ws", source_event_type="trade",
        payload={"market": "m", "price": "0.6", "size": "1000", "outcome": "yes", "side": "sell"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.directional_side == "no"


def test_polymarket_buy_no_is_bearish():
    raw = RawEvent(
        venue_code="polymarket", source_channel="ws", source_event_type="trade",
        payload={"market": "m", "price": "0.6", "size": "1000", "outcome": "no", "side": "buy"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.directional_side == "no"


def test_polymarket_sell_no_is_bullish():
    raw = RawEvent(
        venue_code="polymarket", source_channel="ws", source_event_type="trade",
        payload={"market": "m", "price": "0.6", "size": "1000", "outcome": "no", "side": "sell"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.directional_side == "yes"


def test_normalize_event_non_trade_event_type_returns_none():
    """Non-trade event_type is benign — returns None without raising."""
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="test",
        source_event_type="subscription_confirmed",
        payload={"status": "ok"},
    )
    result = normalize_event(raw)
    assert result is None


def test_normalize_event_empty_payload_raises():
    """Trade event with unparseable payload raises NormalizationError (structured dead letter)."""
    from pmfi.normalization import NormalizationError
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="test",
        source_event_type="trade",
        payload={},
    )
    with pytest.raises(NormalizationError):
        normalize_event(raw)


def test_replay_skips_malformed_fixture():
    from pathlib import Path
    from pmfi.replay import replay_fixtures
    fixture_dir = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw"
    results = replay_fixtures(fixture_dir)
    # malformed_payload.json should be skipped; valid fixtures should still return results
    markets = [r.trade.venue_market_id for r in results]
    assert "pm-bad-market" not in markets
    assert len(results) >= 1


def test_kalshi_cent_price_converts_to_decimal():
    """Live Kalshi WS sends price as integer cents (37 = 0.37). Normalizer must convert."""
    trade = normalize_kalshi_fixture(_ks_raw("37", "100", yes_no="yes"))
    assert trade.price == Decimal("0.37")
    assert trade.capital_at_risk_usd == Decimal("0.37") * 100


def test_kalshi_cent_price_boundary_99():
    """Price of 99 cents = 0.99 decimal, valid."""
    trade = normalize_kalshi_fixture(_ks_raw("99", "1000", yes_no="no"))
    assert trade.price == Decimal("0.99")


def test_kalshi_decimal_price_unchanged():
    """Decimal price below 1 (fixture format) is not modified."""
    trade = normalize_kalshi_fixture(_ks_raw("0.37", "100", yes_no="yes"))
    assert trade.price == Decimal("0.37")


def _ks_live_raw(yes_price: int, no_price: int, count: int, taker_side: str) -> RawEvent:
    """Live Kalshi WS format: yes_price + no_price in cents, no explicit price field."""
    return RawEvent(
        venue_code="kalshi",
        source_channel="ws_trade",
        source_event_type="trade",
        payload={
            "market_ticker": "KS-LIVE",
            "trade_id": "live-1",
            "yes_price": yes_price,
            "no_price": no_price,
            "count": count,
            "taker_side": taker_side,
        },
    )


def test_kalshi_live_yes_taker_uses_yes_price():
    """YES taker should use yes_price for capital calculation."""
    trade = normalize_kalshi_fixture(_ks_live_raw(37, 63, 1000, "yes"))
    assert trade.price == Decimal("0.37")
    assert trade.directional_side == "yes"
    assert trade.capital_at_risk_usd == Decimal("0.37") * 1000


def test_kalshi_live_no_taker_uses_no_price():
    """NO taker should use no_price (not yes_price) for capital calculation.
    This was a bug: the old code always picked yes_price first regardless of taker side.
    """
    trade = normalize_kalshi_fixture(_ks_live_raw(37, 63, 1000, "no"))
    assert trade.price == Decimal("0.63"), "NO taker pays no_price=63, not yes_price=37"
    assert trade.directional_side == "no"
    assert trade.capital_at_risk_usd == Decimal("0.63") * 1000


def test_kalshi_live_yes_taker_high_price():
    """YES taker at high price — verifies cent conversion and side selection."""
    trade = normalize_kalshi_fixture(_ks_live_raw(82, 18, 500, "yes"))
    assert trade.price == Decimal("0.82")
    assert trade.capital_at_risk_usd == Decimal("0.82") * 500


def test_polymarket_missing_outcome_gives_unknown():
    """Live Polymarket events often lack 'outcome' field (they use asset_id instead).
    Without asset_id mapping, outcome_key must be 'unknown', not a guessed 'yes'."""
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"market": "some-condition-id", "price": "0.65", "size": "500", "side": "buy", "asset_id": "token_xyz"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.outcome_key == "unknown"
    assert trade.directional_side == "unknown"


def test_polymarket_explicit_outcome_no_unchanged():
    """When 'outcome' is explicitly provided as 'no', it must be preserved."""
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        payload={"market": "m", "price": "0.4", "size": "1000", "side": "buy", "outcome": "no"},
    )
    trade = normalize_polymarket_fixture(raw)
    assert trade.outcome_key == "no"
    assert trade.directional_side == "no"


def test_kalshi_rest_trade_fixture_normalizes():
    """Full pipeline: load REST fixture -> normalize -> valid trade."""
    from pathlib import Path
    from pmfi.fixtures import load_raw_event
    from pmfi.pipeline.normalize import normalize_event
    from decimal import Decimal
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw" / "kalshi_rest_trade.json"
    raw = load_raw_event(fixture)
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_code == "kalshi"
    assert trade.venue_market_id == "KXBTCD-23DEC3100"
    assert trade.price == Decimal("0.55")
    assert trade.contracts == Decimal("100")
    assert trade.directional_side == "yes"


def test_kalshi_rest_trade_no_side_fixture_normalizes():
    """REST fixture with taker_side=no uses no_price for price."""
    from pathlib import Path
    from pmfi.fixtures import load_raw_event
    from pmfi.pipeline.normalize import normalize_event
    from decimal import Decimal
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "raw" / "kalshi_rest_trade_no_side.json"
    raw = load_raw_event(fixture)
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.price == Decimal("0.63")
    assert trade.directional_side == "no"
    assert trade.contracts == Decimal("500")
