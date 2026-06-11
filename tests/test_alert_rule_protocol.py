"""Offline tests for the AlertRule Protocol contract.

Verifies that:
- All 6 registered rule classes structurally satisfy AlertRule.
- A deliberately malformed object (missing rule_id or missing evaluate) does NOT.
- AlertEngine() construction succeeds (registry validation assertions pass).
"""
from __future__ import annotations

import pytest

from pmfi.pipeline.rules import (
    AlertRule,
    LargeTradeAbsoluteRule,
    MarketRelativeLargeTradeRule,
    OpenInterestShockRule,
    DirectionalClusterRule,
    MomentumRule,
    VolumeSpikeRule,
)
from pmfi.pipeline.engine import AlertEngine
from decimal import Decimal


# ── Fixtures: minimal valid instances of each rule class ────────────────────

@pytest.fixture
def large_trade_abs():
    return LargeTradeAbsoluteRule(
        min_capital_at_risk_usd=Decimal("25000"),
        min_payout_notional_usd=Decimal("100000"),
    )


@pytest.fixture
def market_relative():
    return MarketRelativeLargeTradeRule(
        min_capital_at_risk_usd=Decimal("5000"),
        severity="medium",
    )


@pytest.fixture
def oi_shock():
    return OpenInterestShockRule(
        min_open_interest_fraction=Decimal("0.03"),
        min_capital_at_risk_usd=Decimal("5000"),
        severity="high",
    )


@pytest.fixture
def directional_cluster():
    return DirectionalClusterRule(
        window_seconds=300,
        min_trade_count=3,
        min_net_capital_at_risk_usd=Decimal("15000"),
        min_price_impact_cents=Decimal("2"),
        severity="high",
    )


@pytest.fixture
def momentum():
    return MomentumRule(
        min_trades=5,
        min_net_capital_usd=75000.0,
        min_price_spread=0.03,
        window_seconds=900,
        severity="high",
    )


@pytest.fixture
def volume_spike():
    return VolumeSpikeRule(
        min_spike_multiplier=Decimal("5"),
        min_baseline_trades=20,
        history_max=200,
        severity="medium",
    )


# ── Protocol conformance: all 6 rules ───────────────────────────────────────

def test_large_trade_absolute_is_alert_rule(large_trade_abs):
    assert isinstance(large_trade_abs, AlertRule)


def test_market_relative_is_alert_rule(market_relative):
    assert isinstance(market_relative, AlertRule)


def test_oi_shock_is_alert_rule(oi_shock):
    assert isinstance(oi_shock, AlertRule)


def test_directional_cluster_is_alert_rule(directional_cluster):
    assert isinstance(directional_cluster, AlertRule)


def test_momentum_is_alert_rule(momentum):
    assert isinstance(momentum, AlertRule)


def test_volume_spike_is_alert_rule(volume_spike):
    assert isinstance(volume_spike, AlertRule)


# ── Malformed objects do NOT conform ────────────────────────────────────────

def test_missing_rule_id_not_alert_rule():
    class NoRuleId:
        def evaluate(self, trade, engine):
            return None

    assert not isinstance(NoRuleId(), AlertRule)


def test_missing_evaluate_not_alert_rule():
    class NoEvaluate:
        rule_id = "fake_rule"

    assert not isinstance(NoEvaluate(), AlertRule)


def test_empty_object_not_alert_rule():
    assert not isinstance(object(), AlertRule)


# ── AlertEngine construction passes registry validation ─────────────────────

def test_alert_engine_construction_succeeds():
    engine = AlertEngine()
    assert len(engine._rule_registry) == 7
    rule_ids = {r.rule_id for r in engine._rule_registry}
    assert "price_impact_confirmation_v1" in rule_ids


def test_all_registry_rules_are_alert_rule():
    engine = AlertEngine()
    assert all(isinstance(r, AlertRule) for r in engine._rule_registry)
