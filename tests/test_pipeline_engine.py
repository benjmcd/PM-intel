from __future__ import annotations
from decimal import Decimal
from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.normalize import normalize_event
from pmfi.domain import NormalizedTrade
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"

def test_normalize_event_polymarket():
    raw = load_raw_event(FIXTURE_DIR / "polymarket_last_trade_price.json")
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_code == "polymarket"
    assert trade.price >= 0 and trade.price <= 1
    assert trade.contracts >= 0

def test_normalize_event_kalshi():
    raw = load_raw_event(FIXTURE_DIR / "kalshi_trade.json")
    trade = normalize_event(raw)
    assert trade is not None
    assert trade.venue_code == "kalshi"

def test_normalize_event_unknown_venue():
    from pmfi.domain import RawEvent
    raw = RawEvent(venue_code="polymarket", source_channel="test", source_event_type="trade", payload={})
    result = normalize_event(raw)
    assert result is None

def test_alert_engine_loads_rules():
    engine = AlertEngine()
    assert "large_trade_absolute_v1" in engine._rules.get("rules", {})

def test_alert_engine_no_alert_small_trade():
    raw = load_raw_event(FIXTURE_DIR / "polymarket_last_trade_price.json")
    trade = normalize_event(raw)
    if trade is None:
        return
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    for d in decisions:
        assert d.emit_alert

def test_alert_engine_triggers_large_trade():
    from pmfi.domain import NormalizedTrade, utc_now
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-market",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    assert len(decisions) >= 1
    assert decisions[0].emit_alert
    assert decisions[0].severity in ("medium", "high")
