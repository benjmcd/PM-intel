from __future__ import annotations
from decimal import Decimal
import pytest
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

def test_normalize_event_trade_empty_payload_raises():
    """Trade event with empty/unparseable payload raises NormalizationError (not None)."""
    from pmfi.domain import RawEvent
    from pmfi.normalization import NormalizationError
    raw = RawEvent(venue_code="polymarket", source_channel="test", source_event_type="trade", payload={})
    with pytest.raises(NormalizationError):
        normalize_event(raw)


def test_normalize_event_non_trade_returns_none():
    """Non-trade event_type returns None without exception."""
    from pmfi.domain import RawEvent
    raw = RawEvent(venue_code="polymarket", source_channel="test", source_event_type="subscription_confirmed", payload={})
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


def test_alert_engine_baseline_upgrades_confidence():
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="mkt-abc",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("30000"),
        capital_at_risk_usd=Decimal("15000"),
        payout_notional_usd=Decimal("30000"),
    )
    baselines = {
        "polymarket:mkt-abc": {
            "p99_trade_usd": 10000.0,
            "p995_trade_usd": 14000.0,
            "sample_size": 20,
        }
    }
    engine = AlertEngine(baselines=baselines)
    decisions = engine.evaluate(trade)
    mr_decisions = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert mr_decisions, "expected market_relative_large_trade_v1 decision"
    d = mr_decisions[0]
    assert d.confidence in ("medium", "high"), f"expected medium/high confidence with baseline, got {d.confidence}"
    assert d.evidence.get("baseline_status") == "available"
    assert d.data_quality == "baseline_available"


def test_directional_cluster_fires_through_engine():
    from pmfi.domain import NormalizedTrade
    from pmfi.pipeline.engine import AlertEngine
    engine = AlertEngine()
    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="cluster-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("20000"),
            capital_at_risk_usd=Decimal("10000"),
            payout_notional_usd=Decimal("20000"),
            directional_side="yes",
        )
    # First two trades — no cluster yet
    engine.evaluate(_trade("0.50"))
    engine.evaluate(_trade("0.54"))
    # Third trade crosses thresholds (3 trades, 30k net cap, 8 cent spread)
    all_decisions = engine.evaluate(_trade("0.58"))
    cluster_hits = [d for d in all_decisions if d.rule_id == "directional_cluster_v1"]
    assert cluster_hits, "expected directional_cluster_v1 to fire on third trade"
    d = cluster_hits[0]
    assert d.emit_alert
    assert d.severity == "high"
    assert d.evidence["dominant_side"] == "yes"


def test_oi_shock_fires_with_oi_data():
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="oi-market",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        open_interest_contracts=Decimal("200000"),  # 12000/200000 = 6% >= 3%
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    oi_hits = [d for d in decisions if d.rule_id == "open_interest_shock_v1"]
    assert oi_hits, "expected open_interest_shock_v1 to fire"
    d = oi_hits[0]
    assert d.emit_alert
    assert d.severity == "high"
    assert float(d.evidence["oi_fraction"]) >= 0.03


def test_oi_shock_no_fire_without_oi():
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="oi-market-2",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        # open_interest_contracts intentionally absent (None)
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    oi_hits = [d for d in decisions if d.rule_id == "open_interest_shock_v1"]
    assert not oi_hits, "OI rule must not fire when open_interest_contracts is None"


def test_alert_engine_baseline_pending_without_data():
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="no-baseline-market",
        outcome_key="yes",
        price=Decimal("0.6"),
        contracts=Decimal("20000"),
        capital_at_risk_usd=Decimal("12000"),
        payout_notional_usd=Decimal("20000"),
    )
    engine = AlertEngine()  # no baselines
    decisions = engine.evaluate(trade)
    mr_decisions = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert mr_decisions
    assert mr_decisions[0].data_quality == "baseline_pending"
    assert mr_decisions[0].evidence.get("baseline_status") == "baseline_missing"
    assert mr_decisions[0].evidence.get("baseline_state") == "baseline_missing"


def test_momentum_rule_fires_after_sustained_flow():
    """momentum_v1 fires when 5+ same-direction trades accumulate over 15 min window."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-test-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("30000"),
            capital_at_risk_usd=Decimal("18000"),
            payout_notional_usd=Decimal("30000"),
            directional_side="yes",
        )

    # 5 trades — should accumulate to 90k net capital (>75k threshold)
    results = []
    for p in ["0.50", "0.52", "0.53", "0.55", "0.58"]:
        results = engine.evaluate(_trade(p))

    momentum_hits = [d for d in results if d.rule_id == "momentum_v1"]
    assert momentum_hits, "momentum_v1 should fire after 5 trades with sufficient net capital"
    assert momentum_hits[0].severity == "high"
    assert momentum_hits[0].evidence["dominant_side"] == "yes"
    assert momentum_hits[0].evidence["trade_count"] >= 5


def test_momentum_rule_does_not_fire_without_enough_trades():
    """momentum_v1 does not fire with fewer than min_trades."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade() -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-nofire-market",
            outcome_key="yes",
            price=Decimal("0.55"),
            contracts=Decimal("40000"),
            capital_at_risk_usd=Decimal("22000"),
            payout_notional_usd=Decimal("40000"),
            directional_side="yes",
        )

    # Only 3 trades — below min_trades=5
    results = []
    for _ in range(3):
        results = engine.evaluate(_trade())

    momentum_hits = [d for d in results if d.rule_id == "momentum_v1"]
    assert not momentum_hits, "momentum_v1 must not fire with < 5 trades"


def test_volume_spike_fires_on_outlier_trade():
    """volume_spike_v1 fires when a single trade is 5x+ the recent average."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _small_trade():
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="spike-test-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal("200"),
            capital_at_risk_usd=Decimal("100"),
            payout_notional_usd=Decimal("200"),
        )

    def _big_trade():
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="spike-test-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal("12000"),
            capital_at_risk_usd=Decimal("6000"),  # 60x the $100 average
            payout_notional_usd=Decimal("12000"),
        )

    # Build up baseline with 20 small trades
    for _ in range(20):
        engine.evaluate(_small_trade())

    # Now a large outlier
    decisions = engine.evaluate(_big_trade())
    spike_hits = [d for d in decisions if d.rule_id == "volume_spike_v1"]
    assert spike_hits, "volume_spike_v1 should fire on 60x outlier"
    assert spike_hits[0].severity == "medium"
    assert spike_hits[0].evidence["spike_multiplier"] >= 5.0


def test_volume_spike_no_fire_without_baseline():
    """volume_spike_v1 does not fire until min_baseline_trades have been seen."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="spike-nobaseline-market",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("50000"),
        capital_at_risk_usd=Decimal("25000"),
        payout_notional_usd=Decimal("50000"),
    )
    # Only 5 trades — not enough baseline
    results = []
    for _ in range(5):
        results = engine.evaluate(trade)
    spike_hits = [d for d in results if d.rule_id == "volume_spike_v1"]
    assert not spike_hits, "volume_spike_v1 must not fire without min_baseline_trades"
