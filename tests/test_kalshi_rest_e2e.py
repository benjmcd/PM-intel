"""End-to-end tests for real Kalshi REST trade shape normalization.

All tests are offline (no DB, no network). Fixtures use the real trade object
captured live from api.elections.kalshi.com on 2026-06-07.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from pmfi.domain import NormalizedTrade, RawEvent
from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_kalshi_fixture
from pmfi.pipeline.normalize import normalize_event

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


class TestRealRestTradeE2E:
    """Load real captured fixture through the full dispatch pipeline."""

    def test_fixture_parses_as_raw_event(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        assert raw.venue_code == "kalshi"
        assert raw.source_channel == "rest_trades"
        assert raw.venue_market_id == "KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE"

    def test_normalize_event_returns_normalized_trade(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        result = normalize_event(raw)
        assert result is not None
        assert isinstance(result, NormalizedTrade)

    def test_outcome_key_is_yes(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.outcome_key == "yes"

    def test_directional_side_is_yes(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.directional_side == "yes"

    def test_price_is_0_91(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.price == Decimal("0.9100")

    def test_contracts_equals_49(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.contracts == Decimal("49.00")
        assert trade.contracts == 49  # numeric equality

    def test_capital_at_risk_approx_44_59(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        expected = Decimal("0.9100") * Decimal("49.00")
        assert trade.capital_at_risk_usd == expected

    def test_venue_market_id_preserved(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.venue_market_id == "KXATPCHALLENGERMATCH-26JUN07BAEMOL-BAE"

    def test_venue_trade_id_preserved(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade.json")
        trade = normalize_event(raw)
        assert trade.venue_trade_id == "f272e06b-c375-72df-d6d4-36b5e0b0b482"


class TestNoDollarDivision:
    """Guard: yes_price_dollars / no_price_dollars must NOT be divided by 100."""

    def test_yes_price_dollars_0_50_not_divided(self):
        """yes_price_dollars='0.50' with taker_side='yes' -> price == 0.50, NOT 0.005."""
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="rest_trades",
            source_event_type="trade",
            payload={
                "ticker": "KS-TEST",
                "trade_id": "t1",
                "yes_price_dollars": "0.50",
                "no_price_dollars": "0.50",
                "count_fp": "10.00",
                "taker_side": "yes",
                "created_time": "2026-06-07T10:00:00Z",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.price == Decimal("0.50"), (
            f"expected 0.50 but got {trade.price} — dollars field must not be divided by 100"
        )

    def test_no_price_dollars_0_09_not_divided(self):
        """no_price_dollars='0.0900' with taker_side='no' -> price == 0.09."""
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="rest_trades",
            source_event_type="trade",
            payload={
                "ticker": "KS-TEST",
                "trade_id": "t2",
                "yes_price_dollars": "0.9100",
                "no_price_dollars": "0.0900",
                "count_fp": "5.00",
                "taker_side": "no",
                "created_time": "2026-06-07T10:00:00Z",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.price == Decimal("0.0900")

    def test_legacy_cents_yes_price_37_converts_to_0_37(self):
        """Legacy: yes_price=37 (integer cents) with taker_side='yes' -> price == 0.37."""
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="ws_trade",
            source_event_type="trade",
            payload={
                "market_ticker": "KS-LEGACY",
                "trade_id": "t3",
                "yes_price": 37,
                "no_price": 63,
                "count": 1000,
                "taker_side": "yes",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.price == Decimal("0.37"), (
            f"expected 0.37 (cents->decimal) but got {trade.price}"
        )
        assert trade.directional_side == "yes"

    def test_legacy_cents_no_price_63_converts_to_0_63(self):
        """Legacy: no_price=63 with taker_side='no' -> price == 0.63."""
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="ws_trade",
            source_event_type="trade",
            payload={
                "market_ticker": "KS-LEGACY",
                "trade_id": "t4",
                "yes_price": 37,
                "no_price": 63,
                "count": 500,
                "taker_side": "no",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.price == Decimal("0.63")
        assert trade.directional_side == "no"


class TestCountFpFallback:
    """count_fp preferred over count; count preferred over contracts."""

    def test_count_fp_string_decimal(self):
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="rest_trades",
            source_event_type="trade",
            payload={
                "ticker": "KS-TEST",
                "count_fp": "49.00",
                "count": 999,
                "taker_side": "yes",
                "yes_price_dollars": "0.50",
                "no_price_dollars": "0.50",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.contracts == Decimal("49.00")

    def test_count_fallback_when_no_count_fp(self):
        raw = RawEvent(
            venue_code="kalshi",
            source_channel="ws_trade",
            source_event_type="trade",
            payload={
                "ticker": "KS-TEST",
                "count": 72000,
                "price": "0.37",
                "taker_side": "yes",
            },
        )
        trade = normalize_kalshi_fixture(raw)
        assert trade.contracts == Decimal("72000")


class TestRealRestTradeNoSideE2E:
    """Live capture: taker_side='no', modern dollars format, received_at present."""

    def test_fixture_parses_as_raw_event(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        assert raw.venue_code == "kalshi"
        assert raw.source_channel == "rest_trades"

    def test_normalize_returns_normalized_trade(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert isinstance(trade, NormalizedTrade)

    def test_outcome_key_is_no(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert trade.outcome_key == "no"

    def test_directional_side_is_no(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert trade.directional_side == "no"

    def test_price_uses_no_price_dollars(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert trade.price == Decimal("0.0100")

    def test_contracts_uses_count_fp(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert trade.contracts == Decimal("551.95")

    def test_capital_at_risk_correct(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        expected = Decimal("0.0100") * Decimal("551.95")
        assert trade.capital_at_risk_usd == expected

    def test_venue_trade_id_preserved(self):
        raw = load_raw_event(FIXTURES / "kalshi_live_rest_trade_no_side.json")
        trade = normalize_event(raw)
        assert trade.venue_trade_id == "205ed191-d15c-7cd0-ae0f-8cb250220d64"
