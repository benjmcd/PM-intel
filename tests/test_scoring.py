from pathlib import Path

from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture
from pmfi.scoring import score_large_trade

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_large_trade_rule_alerts_on_capital_at_risk():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    trade = normalize_polymarket_fixture(raw)
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert "capital_at_risk_threshold" in decision.reason_codes
    assert decision.evidence["capital_at_risk_usd"] == "33600.00"
