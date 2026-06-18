from pathlib import Path

from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture
from decimal import Decimal

from pmfi.domain import NormalizedTrade
from pmfi.scoring import score_large_trade

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_large_trade_rule_alerts_on_capital_at_risk():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    trade = normalize_polymarket_fixture(raw)
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert "capital_at_risk_threshold" in decision.reason_codes
    assert decision.evidence["capital_at_risk_usd"] == "33600.00"
    assert decision.data_quality == "complete"


def test_large_trade_rule_marks_warning_trade_partial():
    trade = NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="KXWARN",
        outcome_key="unknown",
        price=Decimal("0.50"),
        contracts=Decimal("60000"),
        capital_at_risk_usd=Decimal("30000.00"),
        payout_notional_usd=Decimal("60000"),
        warnings=("directional side unverified",),
        source_payload={"price": "0.50", "count": "60000"},
    )

    decision = score_large_trade(trade)

    assert decision.emit_alert is True
    assert decision.data_quality == "partial"
