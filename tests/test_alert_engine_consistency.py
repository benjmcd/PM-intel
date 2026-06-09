"""Offline tests for alert-engine correctness and consistency fixes.

Covers:
  (a) confidence_floor removed from config — no dead field, no ghost behavior.
  (b) open_interest_shock_v1 confidence: clean data -> "high", degraded -> "medium".
  (c) volume_spike_v1 median: statistics.median on Decimal window stays Decimal (no float upcast).
"""
from __future__ import annotations

import statistics
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from pmfi.domain import NormalizedTrade
from pmfi.pipeline.engine import AlertEngine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
_RULES_PATH = ROOT / "config" / "alert_rules.yaml"


def _oi_trade(*, degraded: bool = False) -> NormalizedTrade:
    """Build a trade that triggers open_interest_shock_v1."""
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="oi-consistency-market",
        outcome_key="yes",
        price=Decimal("0.65"),
        contracts=Decimal("12000"),
        capital_at_risk_usd=Decimal("7800"),
        payout_notional_usd=Decimal("12000"),
        open_interest_contracts=Decimal("200000"),  # 12000/200000 = 6% >= 3%
        directional_side=None if degraded else "yes",
        warnings=("stale_price_feed",) if degraded else (),
    )


# ---------------------------------------------------------------------------
# (a) confidence_floor removed from config
# ---------------------------------------------------------------------------

class TestConfidenceFloorRemoved:
    """Verify confidence_floor is no longer present in alert_rules.yaml."""

    def test_confidence_floor_absent_from_yaml(self):
        rules = yaml.safe_load(_RULES_PATH.read_text(encoding="utf-8")) or {}
        for rule_id, cfg in rules.get("rules", {}).items():
            assert "confidence_floor" not in cfg, (
                f"Rule '{rule_id}' still contains dead 'confidence_floor' field. "
                "Remove it from config/alert_rules.yaml."
            )

    def test_engine_does_not_read_confidence_floor(self):
        """AlertEngine should load rules without referencing confidence_floor."""
        engine = AlertEngine()
        for rule_id, cfg in engine._rules.get("rules", {}).items():
            assert "confidence_floor" not in cfg, (
                f"Loaded rule '{rule_id}' still has 'confidence_floor'; "
                "the config file was not cleaned up."
            )


# ---------------------------------------------------------------------------
# (b) open_interest_shock_v1 confidence
# ---------------------------------------------------------------------------

class TestOIShockConfidence:
    """open_interest_shock_v1 must yield 'high' on clean data, 'medium' on degraded."""

    def _oi_hit(self, trade: NormalizedTrade) -> object:
        engine = AlertEngine()
        decisions = engine.evaluate(trade)
        hits = [d for d in decisions if d.rule_id == "open_interest_shock_v1"]
        assert hits, "open_interest_shock_v1 must fire for this trade"
        return hits[0]

    def test_clean_data_yields_high_confidence(self):
        d = self._oi_hit(_oi_trade(degraded=False))
        assert d.confidence == "high", (
            f"Clean OI-shock trade must have confidence='high', got {d.confidence!r}"
        )
        assert d.data_quality == "oi_present"

    def test_degraded_data_caps_confidence_at_medium(self):
        d = self._oi_hit(_oi_trade(degraded=True))
        assert d.confidence in ("low", "medium"), (
            f"Degraded OI-shock must cap confidence <= 'medium', got {d.confidence!r}"
        )
        assert d.data_quality == "degraded"

    def test_degraded_data_confidence_is_medium_not_low(self):
        """Degraded OI-shock specifically yields 'medium' (not lower) via _cap_confidence('high','medium')."""
        d = self._oi_hit(_oi_trade(degraded=True))
        assert d.confidence == "medium", (
            f"Degraded OI-shock: expected 'medium', got {d.confidence!r}"
        )

    def test_clean_data_confidence_not_medium_or_lower(self):
        """Regression: the old buggy code could never reach 'high'. Confirm it can now."""
        d = self._oi_hit(_oi_trade(degraded=False))
        assert d.confidence != "medium", (
            "OI-shock clean data must NOT be limited to 'medium' any more; bug was regression-introduced."
        )


# ---------------------------------------------------------------------------
# (c) volume_spike_v1 median: Decimal type preservation
# ---------------------------------------------------------------------------

class TestVolumeSpikeMeanDecimal:
    """statistics.median on a Decimal list must return Decimal, not float.

    This is a regression guard: if a Python upgrade silently changed the
    averaging behaviour for even-length sequences the spike comparisons
    (Decimal >= Decimal * Decimal) would start raising TypeError.
    """

    def test_median_odd_length_stays_decimal(self):
        window = [Decimal("10"), Decimal("20"), Decimal("30")]
        result = statistics.median(window)
        assert isinstance(result, Decimal), (
            f"statistics.median on odd-length Decimal list returned {type(result).__name__}, expected Decimal"
        )
        assert result == Decimal("20")

    def test_median_even_length_stays_decimal(self):
        window = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")]
        result = statistics.median(window)
        assert isinstance(result, Decimal), (
            f"statistics.median on even-length Decimal list returned {type(result).__name__}, expected Decimal. "
            "If this fails, engine.py volume_spike_v1 needs a Decimal-exact median implementation."
        )
        assert result == Decimal("2.5")

    def test_volume_spike_fires_without_type_error(self):
        """End-to-end: volume_spike_v1 must fire without TypeError from Decimal/float mix."""
        engine = AlertEngine()

        def _small():
            return NormalizedTrade(
                venue_code="polymarket",
                venue_market_id="spike-decimal-market",
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
                venue_market_id="spike-decimal-market",
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
        assert spike_hits, "volume_spike_v1 must fire on a 60x outlier"
        # If median returned float, Decimal >= float*Decimal would have raised TypeError before here.
        assert spike_hits[0].evidence["spike_multiplier"] >= 5.0
