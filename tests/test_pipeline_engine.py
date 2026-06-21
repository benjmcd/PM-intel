from __future__ import annotations
from datetime import datetime, timezone, timedelta
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


def test_alert_engine_accepts_directional_accumulator_bounds():
    engine = AlertEngine(
        directional_accumulator_max_markets=12,
        directional_accumulator_ttl_seconds=600,
    )

    assert engine._accumulator._max_markets == 12
    assert engine._accumulator._market_ttl_seconds == 600
    assert engine._momentum_acc._max_markets == 12
    assert engine._momentum_acc._market_ttl_seconds == 600


def _volume_spike_only_rules() -> dict:
    return {
        "version": "alert_rules.v1",
        "rules": {
            "large_trade_absolute_v1": {"enabled": False},
            "market_relative_large_trade_v1": {"enabled": False},
            "open_interest_shock_v1": {"enabled": False},
            "directional_cluster_v1": {"enabled": False},
            "momentum_v1": {"enabled": False},
            "volume_spike_v1": {
                "enabled": True,
                "min_baseline_trades": 20,
                "min_spike_multiplier": 5.0,
                "history_max": 5,
            },
        },
    }


def _vs_trade(market: str, ts: datetime) -> NormalizedTrade:
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id=market,
        outcome_key="yes",
        price=Decimal("0.50"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("50"),
        payout_notional_usd=Decimal("100"),
        directional_side="yes",
        exchange_ts=ts,
    )


def test_volume_spike_history_evicts_lru_market_keys():
    engine = AlertEngine(
        rules_config=_volume_spike_only_rules(),
        directional_accumulator_max_markets=2,
        directional_accumulator_ttl_seconds=3600,
    )
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    engine.evaluate(_vs_trade("mkt-a", ts))
    engine.evaluate(_vs_trade("mkt-b", ts + timedelta(seconds=1)))
    engine.evaluate(_vs_trade("mkt-a", ts + timedelta(seconds=2)))
    engine.evaluate(_vs_trade("mkt-c", ts + timedelta(seconds=3)))

    assert set(engine._vs_history) == {"polymarket:mkt-a", "polymarket:mkt-c"}


def test_volume_spike_history_evicts_cold_market_keys():
    engine = AlertEngine(
        rules_config=_volume_spike_only_rules(),
        directional_accumulator_max_markets=10,
        directional_accumulator_ttl_seconds=30,
    )
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    engine.evaluate(_vs_trade("old", ts))
    engine.evaluate(_vs_trade("fresh", ts + timedelta(seconds=40)))

    assert set(engine._vs_history) == {"polymarket:fresh"}

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
        directional_side="yes",
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


def test_oi_shock_no_fire_with_zero_oi():
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="oi-market-zero",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        open_interest_contracts=Decimal("0"),
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    oi_hits = [d for d in decisions if d.rule_id == "open_interest_shock_v1"]
    assert not oi_hits, "OI rule must not fire when open_interest_contracts is 0 (avoids division by zero)"


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
        directional_side="yes",
    )
    engine = AlertEngine()  # no baselines
    decisions = engine.evaluate(trade)
    mr_decisions = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert mr_decisions
    assert mr_decisions[0].data_quality == "baseline_pending"
    assert mr_decisions[0].evidence.get("baseline_status") == "baseline_missing"
    assert mr_decisions[0].evidence.get("baseline_state") == "baseline_missing"
    # No-baseline path must emit at floor severity only (not medium).
    assert mr_decisions[0].severity == "low", (
        f"no-baseline market_relative must be severity='low', got {mr_decisions[0].severity!r}"
    )


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
    assert spike_hits[0].severity == "low"
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


def test_volume_spike_min_trade_usd_suppresses_low_notional_review_noise(tmp_path):
    """volume_spike_v1 can suppress reviewed low-notional spike noise."""
    rules_path = tmp_path / "alert_rules.yaml"
    rules_path.write_text(
        """
version: alert_rules.v1
rules:
  volume_spike_v1:
    enabled: true
    min_spike_multiplier: 5.0
    min_baseline_trades: 20
    min_trade_usd: 500
    history_max: 200
    severity: medium
""".strip(),
        encoding="utf-8",
    )
    engine = AlertEngine(rules_path=rules_path)

    def _trade(cap_usd: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="review-noise-spike-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    for _ in range(20):
        engine.evaluate(_trade("10"))

    low_notional_decisions = engine.evaluate(_trade("460"))
    assert not [
        d for d in low_notional_decisions if d.rule_id == "volume_spike_v1"
    ], "$460 reviewed noise should be below the configured volume-spike notional floor"

    threshold_decisions = engine.evaluate(_trade("500"))
    spike_hits = [d for d in threshold_decisions if d.rule_id == "volume_spike_v1"]
    assert spike_hits, "$500 should still fire at the configured notional floor"
    assert spike_hits[0].evidence["min_trade_usd"] == 500.0


def test_volume_spike_default_floor_suppresses_sub_threshold_trade():
    """Default volume_spike_v1 config must not emit below the active notional floor."""
    engine = AlertEngine()

    def _trade(cap_usd: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="recalibrated-spike-floor-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    for _ in range(20):
        engine.evaluate(_trade("100"))

    suppressed = engine.evaluate(_trade("849"))
    assert not [d for d in suppressed if d.rule_id == "volume_spike_v1"]

    threshold = engine.evaluate(_trade("850"))
    spike_hits = [d for d in threshold if d.rule_id == "volume_spike_v1"]
    assert spike_hits
    assert spike_hits[0].evidence["min_trade_usd"] == 850.0


def test_volume_spike_default_config_is_active_low_severity_advisory():
    """Operator-approved advisory demotion keeps volume_spike_v1 active but low severity."""
    import yaml

    rules = yaml.safe_load((ROOT / "config" / "alert_rules.yaml").read_text(encoding="utf-8"))
    spike_cfg = rules["rules"]["volume_spike_v1"]
    assert spike_cfg["enabled"] is True
    assert spike_cfg["severity"] == "low"

    engine = AlertEngine()

    def _trade(cap_usd: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="advisory-spike-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    for _ in range(20):
        engine.evaluate(_trade("100"))

    spike_hits = [
        decision
        for decision in engine.evaluate(_trade("850"))
        if decision.rule_id == "volume_spike_v1"
    ]
    assert spike_hits
    assert spike_hits[0].severity == "low"


def test_volume_spike_low_notional_candidate_requires_mature_baseline(tmp_path):
    """Low-notional spike candidates can require extra history while still learning."""
    rules_path = tmp_path / "alert_rules.yaml"
    rules_path.write_text(
        """
version: alert_rules.v1
rules:
  volume_spike_v1:
    enabled: true
    min_spike_multiplier: 5.0
    min_baseline_trades: 20
    min_trade_usd: 800
    low_notional_threshold_usd: 5000
    low_notional_min_baseline_trades: 30
    history_max: 200
    severity: medium
""".strip(),
        encoding="utf-8",
    )
    engine = AlertEngine(rules_path=rules_path)

    def _trade(cap_usd: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="kalshi",
            venue_market_id="low-notional-maturity-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    for _ in range(20):
        engine.evaluate(_trade("20"))

    first_candidate = engine.evaluate(_trade("900"))
    assert not [d for d in first_candidate if d.rule_id == "volume_spike_v1"]

    for _ in range(9):
        engine.evaluate(_trade("20"))

    mature_candidate = engine.evaluate(_trade("900"))
    spike_hits = [d for d in mature_candidate if d.rule_id == "volume_spike_v1"]
    assert spike_hits
    assert spike_hits[0].evidence["baseline_trades"] == 20
    assert spike_hits[0].evidence["baseline_history_trades"] == 30
    assert spike_hits[0].evidence["low_notional_min_baseline_trades"] == 30
    assert spike_hits[0].evidence["low_notional_threshold_usd"] == 5000.0


def test_volume_spike_low_notional_baseline_median_floor_suppresses_and_preserves_history(tmp_path):
    rules_path = tmp_path / "alert_rules.yaml"
    rules_path.write_text(
        """
version: alert_rules.v1
rules:
  volume_spike_v1:
    enabled: true
    min_spike_multiplier: 5.0
    min_baseline_trades: 20
    min_trade_usd: 800
    low_notional_threshold_usd: 1000
    low_notional_min_baseline_median_usd: 150
    history_max: 200
    severity: medium
""".strip(),
        encoding="utf-8",
    )
    engine = AlertEngine(rules_path=rules_path)

    def _trade(cap_usd: str, market: str = "low-median-market") -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="kalshi",
            venue_market_id=market,
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    for _ in range(20):
        engine.evaluate(_trade("100"))

    suppressed = engine.evaluate(_trade("900"))
    assert not [d for d in suppressed if d.rule_id == "volume_spike_v1"]
    history = engine._vs_history["kalshi:low-median-market"]
    assert len(history) == 21
    assert history[-1] == Decimal("900")

    engine_at_floor = AlertEngine(rules_path=rules_path)
    for _ in range(20):
        engine_at_floor.evaluate(_trade("150", market="median-at-floor-market"))

    retained = engine_at_floor.evaluate(_trade("900", market="median-at-floor-market"))
    spike_hits = [d for d in retained if d.rule_id == "volume_spike_v1"]
    assert spike_hits
    assert spike_hits[0].evidence["baseline_median_usd"] == 150.0
    assert spike_hits[0].evidence["low_notional_min_baseline_median_usd"] == 150.0
    assert spike_hits[0].evidence["low_notional_threshold_usd"] == 1000.0


def test_volume_spike_low_notional_median_floor_can_preserve_extreme_multipliers(tmp_path):
    rules_path = tmp_path / "alert_rules.yaml"
    rules_path.write_text(
        """
version: alert_rules.v1
rules:
  volume_spike_v1:
    enabled: true
    min_spike_multiplier: 5.0
    min_baseline_trades: 20
    min_trade_usd: 800
    low_notional_threshold_usd: 1000
    low_notional_min_baseline_median_usd: 150
    low_notional_max_spike_multiplier: 10
    history_max: 200
    severity: medium
""".strip(),
        encoding="utf-8",
    )

    def _trade(cap_usd: str, market: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="kalshi",
            venue_market_id=market,
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal(cap_usd) * Decimal("2"),
            capital_at_risk_usd=Decimal(cap_usd),
            payout_notional_usd=Decimal(cap_usd) * Decimal("2"),
        )

    near_threshold_engine = AlertEngine(rules_path=rules_path)
    for _ in range(20):
        near_threshold_engine.evaluate(_trade("100", "median-ceiling-near"))
    near_threshold = near_threshold_engine.evaluate(_trade("900", "median-ceiling-near"))
    assert not [d for d in near_threshold if d.rule_id == "volume_spike_v1"]

    extreme_engine = AlertEngine(rules_path=rules_path)
    for _ in range(20):
        extreme_engine.evaluate(_trade("20", "median-ceiling-extreme"))
    extreme = extreme_engine.evaluate(_trade("900", "median-ceiling-extreme"))
    spike_hits = [d for d in extreme if d.rule_id == "volume_spike_v1"]
    assert spike_hits
    assert spike_hits[0].evidence["spike_multiplier"] == 45.0
    assert spike_hits[0].evidence["low_notional_min_baseline_median_usd"] == 150.0
    assert spike_hits[0].evidence["low_notional_max_spike_multiplier"] == 10.0


# ---------------------------------------------------------------------------
# Alert-safety: degraded-data confidence gating (Target 6 acceptance)
# ---------------------------------------------------------------------------

def test_momentum_v1_degraded_warnings_caps_confidence():
    """momentum_v1 with trade warnings must not emit confidence='high'."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _setup_trade(price_str: str) -> NormalizedTrade:
        # Clean trades to build up the accumulator
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-degraded-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("30000"),
            capital_at_risk_usd=Decimal("18000"),
            payout_notional_usd=Decimal("30000"),
            directional_side="yes",
        )

    def _degraded_trade(price_str: str) -> NormalizedTrade:
        # Final triggering trade has a warning attached
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-degraded-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("30000"),
            capital_at_risk_usd=Decimal("18000"),
            payout_notional_usd=Decimal("30000"),
            directional_side="yes",
            warnings=("stale_price_feed",),
        )

    # Build 4 clean trades then one degraded triggering trade
    for p in ["0.50", "0.52", "0.53", "0.55"]:
        engine.evaluate(_setup_trade(p))
    results = engine.evaluate(_degraded_trade("0.58"))

    momentum_hits = [d for d in results if d.rule_id == "momentum_v1"]
    assert momentum_hits, "momentum_v1 should still fire on threshold breach"
    d = momentum_hits[0]
    assert d.confidence != "high", f"confidence must be capped below 'high' for degraded data, got {d.confidence!r}"
    assert d.confidence == "medium", f"warnings-only degradation → expected 'medium', got {d.confidence!r}"
    assert d.data_quality == "degraded"
    assert "stale_price_feed" in d.evidence.get("degraded_reasons", [])


def test_momentum_v1_clean_trade_confidence_unchanged():
    """momentum_v1 with clean directional_side emits confidence='high'."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-clean-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("30000"),
            capital_at_risk_usd=Decimal("18000"),
            payout_notional_usd=Decimal("30000"),
            directional_side="yes",
        )

    results = []
    for p in ["0.50", "0.52", "0.53", "0.55", "0.58"]:
        results = engine.evaluate(_trade(p))

    momentum_hits = [d for d in results if d.rule_id == "momentum_v1"]
    assert momentum_hits, "momentum_v1 should fire"
    d = momentum_hits[0]
    assert d.confidence == "high", f"clean data → expected 'high', got {d.confidence!r}"
    assert d.data_quality == "live"
    assert d.evidence.get("degraded_reasons") == []


def test_momentum_v1_evidence_includes_thresholds():
    """momentum_v1 evidence must include trigger threshold keys."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="momentum-thresh-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("30000"),
            capital_at_risk_usd=Decimal("18000"),
            payout_notional_usd=Decimal("30000"),
            directional_side="yes",
        )

    results = []
    for p in ["0.50", "0.52", "0.53", "0.55", "0.58"]:
        results = engine.evaluate(_trade(p))

    momentum_hits = [d for d in results if d.rule_id == "momentum_v1"]
    assert momentum_hits
    ev = momentum_hits[0].evidence
    assert "min_net_capital_usd" in ev, "evidence must include min_net_capital_usd threshold"
    assert "min_trades" in ev, "evidence must include min_trades threshold"
    assert "min_price_spread" in ev, "evidence must include min_price_spread threshold"


def test_directional_cluster_v1_degraded_warnings_caps_confidence():
    """directional_cluster_v1 with trade warnings must not emit confidence='medium'."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _clean_trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="cluster-degraded-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("20000"),
            capital_at_risk_usd=Decimal("10000"),
            payout_notional_usd=Decimal("20000"),
            directional_side="yes",
        )

    def _degraded_trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="cluster-degraded-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("20000"),
            capital_at_risk_usd=Decimal("10000"),
            payout_notional_usd=Decimal("20000"),
            directional_side="yes",
            warnings=("stale_price_feed",),
        )

    engine.evaluate(_clean_trade("0.50"))
    engine.evaluate(_clean_trade("0.54"))
    all_decisions = engine.evaluate(_degraded_trade("0.58"))
    cluster_hits = [d for d in all_decisions if d.rule_id == "directional_cluster_v1"]
    assert cluster_hits, "directional_cluster_v1 should still fire"
    d = cluster_hits[0]
    assert d.confidence != "medium" or d.data_quality == "degraded", (
        "degraded data must set data_quality='degraded'"
    )
    assert d.data_quality == "degraded"
    assert "stale_price_feed" in d.evidence.get("degraded_reasons", [])


def test_directional_cluster_v1_clean_trade_confidence_unchanged():
    """directional_cluster_v1 with clean data emits confidence='medium'."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="cluster-clean-market2",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("20000"),
            capital_at_risk_usd=Decimal("10000"),
            payout_notional_usd=Decimal("20000"),
            directional_side="yes",
        )

    engine.evaluate(_trade("0.50"))
    engine.evaluate(_trade("0.54"))
    all_decisions = engine.evaluate(_trade("0.58"))
    cluster_hits = [d for d in all_decisions if d.rule_id == "directional_cluster_v1"]
    assert cluster_hits
    d = cluster_hits[0]
    assert d.confidence == "medium"
    assert d.data_quality == "in_window"
    assert d.evidence.get("degraded_reasons") == []


def test_directional_cluster_v1_evidence_includes_thresholds():
    """directional_cluster_v1 evidence must include trigger threshold keys."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _trade(price_str: str) -> NormalizedTrade:
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="cluster-thresh-market",
            outcome_key="yes",
            price=Decimal(price_str),
            contracts=Decimal("20000"),
            capital_at_risk_usd=Decimal("10000"),
            payout_notional_usd=Decimal("20000"),
            directional_side="yes",
        )

    engine.evaluate(_trade("0.50"))
    engine.evaluate(_trade("0.54"))
    all_decisions = engine.evaluate(_trade("0.58"))
    cluster_hits = [d for d in all_decisions if d.rule_id == "directional_cluster_v1"]
    assert cluster_hits
    ev = cluster_hits[0].evidence
    assert "min_trade_count" in ev
    assert "min_net_capital_usd" in ev
    assert "min_price_impact_cents" in ev


def test_open_interest_shock_v1_evidence_includes_thresholds():
    """open_interest_shock_v1 evidence must include trigger threshold keys."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="oi-thresh-market",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        open_interest_contracts=Decimal("200000"),
        directional_side="yes",
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)
    oi_hits = [d for d in decisions if d.rule_id == "open_interest_shock_v1"]
    assert oi_hits
    ev = oi_hits[0].evidence
    assert "min_oi_fraction" in ev
    assert "min_capital_threshold_usd" in ev
    assert "outcome_key" in ev


def test_volume_spike_v1_evidence_includes_min_spike_multiplier():
    """volume_spike_v1 evidence must include min_spike_multiplier threshold (distinct from observed)."""
    from pmfi.domain import NormalizedTrade
    engine = AlertEngine()

    def _small():
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="spike-thresh-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal("200"),
            capital_at_risk_usd=Decimal("100"),
            payout_notional_usd=Decimal("200"),
            directional_side="yes",
        )

    def _big():
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="spike-thresh-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal("12000"),
            capital_at_risk_usd=Decimal("6000"),
            payout_notional_usd=Decimal("12000"),
            directional_side="yes",
        )

    for _ in range(20):
        engine.evaluate(_small())
    decisions = engine.evaluate(_big())
    spike_hits = [d for d in decisions if d.rule_id == "volume_spike_v1"]
    assert spike_hits
    ev = spike_hits[0].evidence
    assert "min_spike_multiplier" in ev, "evidence must expose configured threshold"
    assert "spike_multiplier" in ev, "evidence must expose observed multiplier"


def test_market_relative_large_trade_v1_evidence_includes_threshold_percentile():
    """market_relative_large_trade_v1 evidence must include threshold_percentile."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="mr-thresh-market",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("30000"),
        capital_at_risk_usd=Decimal("15000"),
        payout_notional_usd=Decimal("30000"),
        directional_side="yes",
    )
    baselines = {
        "polymarket:mr-thresh-market": {
            "p99_trade_usd": 10000.0,
            "p995_trade_usd": 14000.0,
            "sample_size": 20,
        }
    }
    engine = AlertEngine(baselines=baselines)
    decisions = engine.evaluate(trade)
    mr_hits = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert mr_hits
    ev = mr_hits[0].evidence
    assert "threshold_percentile" in ev
    assert ev["threshold_percentile"] == "p995"


def test_all_alert_rules_include_operator_evidence_fields():
    """Every emitted rule exposes decision margin and baseline/sample quality for explainability."""
    from datetime import datetime, timezone

    def _assert_operator_fields(decision):
        ev = decision.evidence
        assert "margin_to_threshold" in ev, decision.rule_id
        assert float(ev["margin_to_threshold"]) >= 0.0, decision.rule_id
        assert ev.get("margin_to_threshold_unit") == "relative_ratio"
        assert "baseline_sample_quality" in ev, decision.rule_id
        assert str(ev["baseline_sample_quality"]), decision.rule_id

    decisions_by_rule: dict[str, object] = {}

    large_trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="operator-large-market",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="yes",
    )
    for decision in AlertEngine().evaluate(large_trade):
        if decision.rule_id == "large_trade_absolute_v1":
            decisions_by_rule[decision.rule_id] = decision

    market_relative_trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="operator-relative-market",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("30000"),
        capital_at_risk_usd=Decimal("15000"),
        payout_notional_usd=Decimal("30000"),
        directional_side="yes",
    )
    relative_engine = AlertEngine(
        baselines={
            "polymarket:operator-relative-market": {
                "p99_trade_usd": 10000.0,
                "p995_trade_usd": 14000.0,
                "sample_size": 20,
                "is_fresh": True,
                "computed_at": datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc),
            }
        }
    )
    for decision in relative_engine.evaluate(market_relative_trade):
        if decision.rule_id == "market_relative_large_trade_v1":
            decisions_by_rule[decision.rule_id] = decision

    oi_trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="operator-oi-market",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        open_interest_contracts=Decimal("200000"),
        directional_side="yes",
    )
    for decision in AlertEngine().evaluate(oi_trade):
        if decision.rule_id == "open_interest_shock_v1":
            decisions_by_rule[decision.rule_id] = decision

    cluster_engine = AlertEngine()
    for price in ("0.50", "0.54", "0.58"):
        cluster_decisions = cluster_engine.evaluate(
            NormalizedTrade(
                venue_code="polymarket",
                venue_market_id="operator-cluster-market",
                outcome_key="yes",
                price=Decimal(price),
                contracts=Decimal("20000"),
                capital_at_risk_usd=Decimal("10000"),
                payout_notional_usd=Decimal("20000"),
                directional_side="yes",
            )
        )
    for decision in cluster_decisions:
        if decision.rule_id == "directional_cluster_v1":
            decisions_by_rule[decision.rule_id] = decision

    momentum_engine = AlertEngine()
    for price in ("0.50", "0.52", "0.53", "0.55", "0.58"):
        momentum_decisions = momentum_engine.evaluate(
            NormalizedTrade(
                venue_code="polymarket",
                venue_market_id="operator-momentum-market",
                outcome_key="yes",
                price=Decimal(price),
                contracts=Decimal("30000"),
                capital_at_risk_usd=Decimal("18000"),
                payout_notional_usd=Decimal("30000"),
                directional_side="yes",
            )
        )
    for decision in momentum_decisions:
        if decision.rule_id == "momentum_v1":
            decisions_by_rule[decision.rule_id] = decision

    volume_engine = AlertEngine()
    for _ in range(20):
        volume_engine.evaluate(
            NormalizedTrade(
                venue_code="polymarket",
                venue_market_id="operator-volume-market",
                outcome_key="yes",
                price=Decimal("0.5"),
                contracts=Decimal("200"),
                capital_at_risk_usd=Decimal("100"),
                payout_notional_usd=Decimal("200"),
                directional_side="yes",
            )
        )
    for decision in volume_engine.evaluate(
        NormalizedTrade(
            venue_code="polymarket",
            venue_market_id="operator-volume-market",
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=Decimal("12000"),
            capital_at_risk_usd=Decimal("6000"),
            payout_notional_usd=Decimal("12000"),
            directional_side="yes",
        )
    ):
        if decision.rule_id == "volume_spike_v1":
            decisions_by_rule[decision.rule_id] = decision

    assert set(decisions_by_rule) == {
        "large_trade_absolute_v1",
        "market_relative_large_trade_v1",
        "open_interest_shock_v1",
        "directional_cluster_v1",
        "momentum_v1",
        "volume_spike_v1",
    }
    for decision in decisions_by_rule.values():
        _assert_operator_fields(decision)


# ---------------------------------------------------------------------------
# JSON-serializable evidence (regression guard against Decimal leaks)
# ---------------------------------------------------------------------------

def test_alert_evidence_is_json_serializable():
    """All evidence dicts from AlertDecision must be JSON-serializable.

    Regression guard: a Decimal value in evidence would cause json.dumps to
    raise TypeError. Feed a large-capital trade that triggers large_trade_absolute_v1
    and assert every resulting decision's evidence can be round-tripped through JSON.
    """
    import json
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="json-safety-market",
        outcome_key="yes",
        price=Decimal("0.80"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
    )
    engine = AlertEngine()
    decisions = engine.evaluate(trade)

    # The large-capital trade must fire at least one rule so this guard is non-vacuous
    assert decisions, "Expected at least one AlertDecision from large-capital trade"

    for decision in decisions:
        try:
            result = json.dumps(decision.evidence)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                f"evidence for rule '{decision.rule_id}' is not JSON-serializable: {exc}\n"
                f"evidence={decision.evidence!r}"
            ) from exc
        assert isinstance(result, str), f"json.dumps must return str, got {type(result)}"
