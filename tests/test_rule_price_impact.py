"""Tests for PriceImpactConfirmationRule (price_impact_confirmation_v1)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from pmfi.domain import NormalizedTrade
from pmfi.pipeline.rules_price_impact import PriceImpactConfirmationRule


def _trade(
    price: str,
    capital: str = "5000",
    venue_market_id: str = "mkt-1",
    outcome_key: str = "yes",
    directional_side: str = "yes",
) -> NormalizedTrade:
    return NormalizedTrade(
        venue_code="polymarket",
        venue_market_id=venue_market_id,
        outcome_key=outcome_key,
        price=Decimal(price),
        contracts=Decimal("1000"),
        capital_at_risk_usd=Decimal(capital),
        payout_notional_usd=Decimal(capital) * 2,
        directional_side=directional_side,  # type: ignore[arg-type]
    )


def _rule(
    min_impact: str = "3",
    min_capital: str = "1000",
    severity: str = "high",
    enabled: bool = True,
) -> PriceImpactConfirmationRule:
    return PriceImpactConfirmationRule(
        min_price_impact_cents=Decimal(min_impact),
        min_capital_at_risk_usd=Decimal(min_capital),
        severity=severity,
        enabled=enabled,
    )


class TestPriceImpactConfirmationRule:
    def test_first_trade_returns_none(self):
        """No prior price — cannot compute impact; must not fire."""
        rule = _rule()
        result = rule.evaluate(_trade("0.50"), engine=object())
        assert result is None

    def test_small_move_does_not_fire(self):
        """Price moves by 1 cent (< 3 cent threshold) — must not fire."""
        rule = _rule(min_impact="3")
        rule.evaluate(_trade("0.50"), engine=object())  # seed prior
        result = rule.evaluate(_trade("0.51"), engine=object())  # 1 cent move
        assert result is None

    def test_large_move_fires(self):
        """Price moves by 5 cents (>= 3 cent threshold) with sufficient capital — must fire."""
        rule = _rule(min_impact="3", min_capital="1000")
        rule.evaluate(_trade("0.50"), engine=object())
        result = rule.evaluate(_trade("0.55", capital="5000"), engine=object())
        assert result is not None
        assert result.emit_alert is True
        assert result.rule_id == "price_impact_confirmation_v1"
        assert "price_impact_confirmed" in result.reason_codes
        assert result.severity == "high"
        assert result.confidence == "high"

    def test_seed_prior_price_enables_first_replayed_trade_to_fire(self):
        """Replay seeding supplies the prior price without emitting during seed."""
        rule = _rule(min_impact="3", min_capital="1000")
        rule.seed_prior_price(
            venue_code="polymarket",
            venue_market_id="mkt-1",
            outcome_key="yes",
            price=Decimal("0.50"),
        )
        result = rule.evaluate(_trade("0.55", capital="5000"), engine=object())
        assert result is not None
        assert result.evidence["prior_price"] == "0.50"
        assert result.evidence["new_price"] == "0.55"

    def test_evidence_fields_populated(self):
        """Evidence dict must contain all required keys with correct values."""
        rule = _rule(min_impact="3", min_capital="1000")
        rule.evaluate(_trade("0.50"), engine=object())
        result = rule.evaluate(_trade("0.55", capital="5000"), engine=object())
        assert result is not None
        ev = result.evidence
        assert ev["prior_price"] == "0.50"
        assert ev["new_price"] == "0.55"
        assert Decimal(ev["price_impact_cents"]) == Decimal("5.00")
        assert ev["venue_market_id"] == "mkt-1"
        assert ev["outcome_key"] == "yes"

    def test_capital_gate_blocks_fire(self):
        """Large price move but capital below threshold — must not fire."""
        rule = _rule(min_impact="3", min_capital="10000")
        rule.evaluate(_trade("0.50"), engine=object())
        result = rule.evaluate(_trade("0.60", capital="500"), engine=object())
        assert result is None

    def test_disabled_rule_returns_none(self):
        """Disabled rule must always return None regardless of move size."""
        rule = _rule(enabled=False)
        rule.evaluate(_trade("0.50"), engine=object())
        result = rule.evaluate(_trade("0.70", capital="99999"), engine=object())
        assert result is None

    def test_state_updates_even_when_disabled(self):
        """Prior-price state still updates while disabled so re-enable works correctly."""
        rule = _rule(min_impact="3", enabled=False)
        rule.evaluate(_trade("0.40"), engine=object())
        rule.evaluate(_trade("0.50"), engine=object())
        # Re-enable and check that the stored prior is 0.50 (last trade while disabled)
        rule._enabled = True
        result = rule.evaluate(_trade("0.65", capital="5000"), engine=object())
        assert result is not None
        assert result.evidence["prior_price"] == "0.50"

    def test_state_is_per_outcome_key(self):
        """Prior state is keyed by venue:market:outcome — different outcomes are independent."""
        rule = _rule(min_impact="3")
        rule.evaluate(_trade("0.50", outcome_key="yes"), engine=object())
        rule.evaluate(_trade("0.50", outcome_key="no"), engine=object())
        # "yes" already seeded; "no" also seeded; feed large move only on "yes"
        result_yes = rule.evaluate(_trade("0.65", outcome_key="yes", capital="5000"), engine=object())
        result_no = rule.evaluate(_trade("0.51", outcome_key="no", capital="5000"), engine=object())
        assert result_yes is not None
        assert result_no is None

    def test_degraded_data_quality_caps_confidence(self):
        """directional_side='unknown' degrades data quality and caps confidence to medium."""
        rule = _rule(min_impact="3", min_capital="1000")
        rule.evaluate(_trade("0.50", directional_side="unknown"), engine=object())
        result = rule.evaluate(
            _trade("0.60", capital="5000", directional_side="unknown"), engine=object()
        )
        assert result is not None
        assert result.confidence == "medium"
        assert result.data_quality == "degraded"
        assert "direction_unknown" in result.evidence["degraded_reasons"]

    def test_exact_threshold_fires(self):
        """Move exactly equal to min_price_impact_cents must fire (>= boundary)."""
        rule = _rule(min_impact="3", min_capital="1000")
        rule.evaluate(_trade("0.50"), engine=object())
        # 3 cents exactly
        result = rule.evaluate(_trade("0.53", capital="2000"), engine=object())
        assert result is not None

    def test_rule_id_attribute(self):
        rule = _rule()
        assert rule.rule_id == "price_impact_confirmation_v1"

    def test_sequence_updates_prior_each_step(self):
        """After each evaluate, prior_price is the trade just processed."""
        rule = _rule(min_impact="3", min_capital="1000")
        engine = object()
        rule.evaluate(_trade("0.50"), engine=engine)   # sets prior=0.50
        rule.evaluate(_trade("0.51"), engine=engine)   # small move, sets prior=0.51
        # Now prior is 0.51; a 5-cent move from 0.51 → 0.56 should fire
        result = rule.evaluate(_trade("0.56", capital="5000"), engine=engine)
        assert result is not None
        assert result.evidence["prior_price"] == "0.51"
        assert result.evidence["new_price"] == "0.56"
