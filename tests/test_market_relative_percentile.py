"""Tests for market_relative_large_trade_v1 percentile-gate correctness (US-04).

The rule must ONLY emit when a trade exceeds a percentile threshold:
  - baseline present, capital < p99      -> NO alert (percentile gate not met)
  - baseline present, p99 <= cap < p995  -> one MEDIUM alert
  - baseline present, cap >= p995        -> one HIGH-confidence alert
  - no baseline, cap >= min_cap          -> one LOW/info alert (not medium)
"""
from __future__ import annotations
from decimal import Decimal

import pytest

from pmfi.domain import NormalizedTrade
from pmfi.pipeline.engine import AlertEngine

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASELINE = {
    "p99_trade_usd": 10000.0,
    "p995_trade_usd": 15000.0,
    "sample_size": 20,
}
_BKEY = "polymarket:pct-test-market"


def _trade(capital: str) -> NormalizedTrade:
    cap = Decimal(capital)
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="pct-test-market",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=cap * 2,
        capital_at_risk_usd=cap,
        payout_notional_usd=cap * 2,
        directional_side="yes",
    )


def _mr_decisions(decisions):
    return [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]


# ---------------------------------------------------------------------------
# 1. Baseline present, capital between min_cap (5000) and p99 (10000) -> NO alert
# ---------------------------------------------------------------------------

def test_below_p99_does_not_fire():
    """Trade above min_cap but below p99 must NOT produce a market_relative alert."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("7500"))
    hits = _mr_decisions(decisions)
    assert hits == [], (
        f"expected zero market_relative alerts for capital below p99, got {len(hits)}: {hits}"
    )


def test_at_min_cap_does_not_fire():
    """Trade exactly at min_cap (5000) but below p99 must NOT produce a market_relative alert."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("5000"))
    hits = _mr_decisions(decisions)
    assert hits == [], (
        f"expected zero market_relative alerts at min_cap below p99, got {len(hits)}"
    )


def test_just_below_p99_does_not_fire():
    """Trade just below p99 must NOT produce a market_relative alert."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("9999"))
    hits = _mr_decisions(decisions)
    assert hits == [], (
        f"expected zero market_relative alerts just below p99, got {len(hits)}"
    )


# ---------------------------------------------------------------------------
# 2. Baseline present, capital >= p99 but < p995 -> exactly one MEDIUM alert
# ---------------------------------------------------------------------------

def test_at_p99_fires_medium():
    """Trade exactly at p99 must fire exactly one medium market_relative alert."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("10000"))
    hits = _mr_decisions(decisions)
    assert len(hits) == 1, f"expected exactly 1 market_relative alert at p99, got {len(hits)}"
    d = hits[0]
    assert d.emit_alert
    assert d.confidence in ("medium", "low"), (
        f"expected medium (or low for sparse) confidence at p99, got {d.confidence!r}"
    )
    assert d.evidence["threshold_percentile"] == "p99"
    assert d.evidence["baseline_status"] == "available"


def test_between_p99_and_p995_fires_medium():
    """Trade between p99 and p995 must fire exactly one market_relative alert."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("12000"))
    hits = _mr_decisions(decisions)
    assert len(hits) == 1, f"expected 1 market_relative alert between p99/p995, got {len(hits)}"
    d = hits[0]
    assert d.emit_alert
    assert d.evidence["threshold_percentile"] == "p99"


def test_just_below_p995_fires_medium():
    """Trade just below p995 threshold stays on the p99 branch."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("14999"))
    hits = _mr_decisions(decisions)
    assert len(hits) == 1
    assert hits[0].evidence["threshold_percentile"] == "p99"


# ---------------------------------------------------------------------------
# 3. Baseline present, capital >= p995 -> one high-confidence alert
# ---------------------------------------------------------------------------

def test_at_p995_fires_high_confidence():
    """Trade at or above p995 must fire with high-confidence path."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("15000"))
    hits = _mr_decisions(decisions)
    assert len(hits) == 1, f"expected 1 market_relative alert at p995, got {len(hits)}"
    d = hits[0]
    assert d.emit_alert
    assert d.confidence in ("high", "medium"), (
        f"expected high (or medium for sparse) confidence at p995, got {d.confidence!r}"
    )
    assert d.evidence["threshold_percentile"] == "p995"
    assert d.evidence["baseline_status"] == "available"


def test_above_p995_fires_high_confidence():
    """Trade well above p995 must fire with high-confidence path."""
    engine = AlertEngine(baselines={_BKEY: _BASELINE})
    decisions = engine.evaluate(_trade("30000"))
    hits = _mr_decisions(decisions)
    assert len(hits) == 1
    d = hits[0]
    assert d.confidence in ("high", "medium")
    assert d.evidence["threshold_percentile"] == "p995"


# ---------------------------------------------------------------------------
# 4. No baseline, capital >= min_cap -> exactly one LOW/info alert (not medium)
# ---------------------------------------------------------------------------

def test_no_baseline_fires_low_severity():
    """Without a baseline the rule must emit at severity='low', not medium."""
    engine = AlertEngine()  # no baselines
    trade = NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="no-baseline-mkt",
        outcome_key="yes",
        price=Decimal("0.6"),
        contracts=Decimal("20000"),
        capital_at_risk_usd=Decimal("12000"),
        payout_notional_usd=Decimal("20000"),
        directional_side="yes",
    )
    decisions = engine.evaluate(trade)
    hits = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert len(hits) == 1, f"expected 1 floor alert without baseline, got {len(hits)}"
    d = hits[0]
    assert d.emit_alert
    assert d.severity == "low", (
        f"no-baseline floor alert must be severity='low', got {d.severity!r}"
    )
    assert d.data_quality == "baseline_pending"
    assert d.evidence.get("baseline_status") == "baseline_missing"


def test_no_baseline_below_min_cap_does_not_fire():
    """Without a baseline AND below min_cap (5000), no alert at all."""
    engine = AlertEngine()
    trade = NormalizedTrade(
        venue_code="kalshi",
        venue_market_id="no-baseline-mkt",
        outcome_key="yes",
        price=Decimal("0.6"),
        contracts=Decimal("6000"),
        capital_at_risk_usd=Decimal("3600"),  # below 5000 min_cap
        payout_notional_usd=Decimal("6000"),
    )
    decisions = engine.evaluate(trade)
    hits = [d for d in decisions if d.rule_id == "market_relative_large_trade_v1"]
    assert hits == [], f"expected no alert below min_cap without baseline, got {len(hits)}"
