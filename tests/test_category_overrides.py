"""Unit tests for category-specific threshold overrides (suppress-only)."""
from __future__ import annotations

from decimal import Decimal

from pmfi.domain import NormalizedTrade, AlertDecision
from pmfi.pipeline.engine import AlertEngine

_OVERRIDES = {"category_overrides": {"politics": {"r": {"min_capital_at_risk_usd": 100000}}}}


def _trade(category, capital):
    return NormalizedTrade(
        venue_code="polymarket", venue_market_id="m1", outcome_key="yes",
        price=Decimal("0.5"), contracts=Decimal("100"),
        capital_at_risk_usd=Decimal(str(capital)), payout_notional_usd=Decimal("200"),
        category=category,
    )


def _decision(rule_id):
    return AlertDecision(
        emit_alert=True, rule_id=rule_id, rule_version="alert_rules.v1",
        severity="high", confidence="high", score=Decimal("0.8"),
        reason_codes=("x",), evidence={}, data_quality="verified",
    )


def _engine_with_overrides():
    eng = AlertEngine()
    eng._rules = dict(_OVERRIDES)
    return eng


def test_no_category_no_suppression():
    out = _engine_with_overrides()._apply_category_overrides(_trade(None, 1000), [_decision("r")])
    assert len(out) == 1


def test_below_category_floor_suppressed():
    out = _engine_with_overrides()._apply_category_overrides(_trade("politics", 50000), [_decision("r")])
    assert out == []


def test_above_category_floor_kept():
    out = _engine_with_overrides()._apply_category_overrides(_trade("politics", 150000), [_decision("r")])
    assert len(out) == 1


def test_unrelated_category_no_effect():
    out = _engine_with_overrides()._apply_category_overrides(_trade("sports", 1000), [_decision("r")])
    assert len(out) == 1


def test_normalized_trade_accepts_category():
    assert _trade("politics", 1000).category == "politics"
    # default is None (backward compatible)
    t = NormalizedTrade(
        venue_code="polymarket", venue_market_id="m", outcome_key="yes",
        price=Decimal("0.5"), contracts=Decimal("1"),
        capital_at_risk_usd=Decimal("1"), payout_notional_usd=Decimal("1"),
    )
    assert t.category is None
