"""Transparent alert scoring primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pmfi.domain import AlertDecision, NormalizedTrade


@dataclass(frozen=True)
class LargeTradeRule:
    rule_id: str = "large_trade_absolute_v1"
    rule_version: str = "alert_rules.v1"
    min_capital_at_risk_usd: Decimal = Decimal("25000")
    min_payout_notional_usd: Decimal = Decimal("100000")


def score_large_trade(trade: NormalizedTrade, rule: LargeTradeRule | None = None) -> AlertDecision:
    rule = rule or LargeTradeRule()
    reasons: list[str] = []

    if trade.capital_at_risk_usd >= rule.min_capital_at_risk_usd:
        reasons.append("capital_at_risk_threshold")
    if trade.payout_notional_usd >= rule.min_payout_notional_usd:
        reasons.append("payout_notional_threshold")

    emit_alert = bool(reasons)
    if trade.capital_at_risk_usd >= rule.min_capital_at_risk_usd * Decimal("2"):
        severity = "high"
    elif emit_alert:
        severity = "medium"
    else:
        severity = "low"

    score = Decimal("1.0") if len(reasons) == 2 else Decimal("0.6") if reasons else Decimal("0.0")

    data_quality = "partial" if trade.warnings else "complete"

    return AlertDecision(
        emit_alert=emit_alert,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        severity=severity,
        confidence="medium" if emit_alert else "low",
        score=score,
        reason_codes=tuple(reasons),
        data_quality=data_quality,
        evidence={
            "venue_code": trade.venue_code,
            "venue_market_id": trade.venue_market_id,
            "outcome_key": trade.outcome_key,
            "price": str(trade.price),
            "contracts": str(trade.contracts),
            "capital_at_risk_usd": str(trade.capital_at_risk_usd),
            "payout_notional_usd": str(trade.payout_notional_usd),
        },
    )
