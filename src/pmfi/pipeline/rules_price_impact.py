"""Price-impact confirmation rule (price_impact_confirmation_v1).

Fires when a single trade moves the observed price for a venue:market:outcome
pair by at least ``min_price_impact_cents`` relative to the previously seen
price for that key, AND ``capital_at_risk_usd >= min_capital_at_risk_usd``.

Prior-price state is kept entirely inside the rule instance — no engine
attributes are read or written, so the rule integrates without touching
engine.py beyond a single registry append.
"""
from __future__ import annotations

from decimal import Decimal

from pmfi.domain import AlertDecision, NormalizedTrade
from pmfi.scoring import assess_data_quality, _cap_confidence


class PriceImpactConfirmationRule:
    """price_impact_confirmation_v1 — per-outcome price-move gate."""

    rule_id = "price_impact_confirmation_v1"

    def __init__(
        self,
        *,
        min_price_impact_cents: Decimal,
        min_capital_at_risk_usd: Decimal,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._min_impact = min_price_impact_cents
        self._min_capital = min_capital_at_risk_usd
        self._severity = severity
        # keyed by "venue_code:venue_market_id:outcome_key"
        self._prior_prices: dict[str, Decimal] = {}

    def _key(self, venue_code: str, venue_market_id: str, outcome_key: str) -> str:
        return f"{venue_code}:{venue_market_id}:{outcome_key}"

    def seed_prior_price(
        self,
        *,
        venue_code: str,
        venue_market_id: str,
        outcome_key: str,
        price: Decimal,
    ) -> None:
        """Seed prior-price state without evaluating or emitting an alert."""
        self._prior_prices[self._key(venue_code, venue_market_id, outcome_key)] = price

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:
        key = self._key(trade.venue_code, trade.venue_market_id, trade.outcome_key)
        prior = self._prior_prices.get(key)

        # Always update state so the next trade has a valid prior
        self._prior_prices[key] = trade.price

        if not self._enabled:
            return None

        # No prior observation — cannot compute an impact yet
        if prior is None:
            return None

        # Price is stored as a fraction in [0,1] for Polymarket/Kalshi.
        # Convert to cents (×100) for comparison against the configured threshold.
        price_impact_cents = abs(trade.price - prior) * Decimal("100")

        if price_impact_cents < self._min_impact:
            return None

        if trade.capital_at_risk_usd < self._min_capital:
            return None

        _dq, _dq_reasons = assess_data_quality(trade)
        _confidence = _cap_confidence("high", "medium") if _dq == "degraded" else "high"
        _data_quality = "degraded" if _dq == "degraded" else "price_move_confirmed"

        return AlertDecision(
            emit_alert=True,
            rule_id="price_impact_confirmation_v1",
            rule_version="alert_rules.v1",
            severity=self._severity,
            confidence=_confidence,
            score=Decimal("0.80"),
            reason_codes=("price_impact_confirmed",),
            evidence={
                "venue_code": trade.venue_code,
                "venue_market_id": trade.venue_market_id,
                "outcome_key": trade.outcome_key,
                "prior_price": str(prior),
                "new_price": str(trade.price),
                "price_impact_cents": str(price_impact_cents),
                "capital_at_risk_usd": str(trade.capital_at_risk_usd),
                "min_price_impact_cents": str(self._min_impact),
                "min_capital_at_risk_usd": str(self._min_capital),
                "degraded_reasons": _dq_reasons,
            },
            data_quality=_data_quality,
        )
