"""Transparent alert scoring primitives."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pmfi.domain import AlertDecision, NormalizedTrade

# Ordinal confidence ordering (lowest → highest index)
_CONFIDENCE_ORDER = ["low", "medium", "high"]


def _cap_confidence(level: str, ceiling: str) -> str:
    """Return the lower of level and ceiling using the low<medium<high ordering."""
    try:
        level_idx = _CONFIDENCE_ORDER.index(level)
    except ValueError:
        return ceiling
    try:
        ceiling_idx = _CONFIDENCE_ORDER.index(ceiling)
    except ValueError:
        return level
    return _CONFIDENCE_ORDER[min(level_idx, ceiling_idx)]


def _relative_margin(observed: Decimal, threshold: Decimal) -> Decimal:
    if threshold <= 0:
        return Decimal("0")
    return observed / threshold - Decimal("1")


def _margin_float(value: Decimal) -> float:
    return round(float(value), 6)


def assess_data_quality(trade: NormalizedTrade) -> tuple[str, list[str]]:
    """Return (data_quality, reasons). reasons is a list of degraded markers.

    data_quality is "degraded" when any marker is found, otherwise "ok".
    """
    reasons: list[str] = []
    outcome = getattr(trade, "outcome_key", None)
    if outcome in (None, "", "unknown"):
        reasons.append("outcome_unknown")
    direction = getattr(trade, "directional_side", None)
    if direction in (None, "", "unknown"):
        reasons.append("direction_unknown")
    warnings = getattr(trade, "warnings", None)
    if warnings:
        reasons.extend(str(w) for w in warnings)
    return ("degraded" if reasons else "ok", reasons)


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

    base_confidence = "medium" if emit_alert else "low"
    _dq, _dq_reasons = assess_data_quality(trade)
    confidence = _cap_confidence(base_confidence, "medium") if _dq == "degraded" else base_confidence
    data_quality = "degraded" if _dq == "degraded" else "verified"
    capital_margin = _relative_margin(
        trade.capital_at_risk_usd,
        rule.min_capital_at_risk_usd,
    )
    payout_margin = _relative_margin(
        trade.payout_notional_usd,
        rule.min_payout_notional_usd,
    )

    return AlertDecision(
        emit_alert=emit_alert,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        severity=severity,
        confidence=confidence,
        score=score,
        reason_codes=tuple(reasons),
        data_quality=data_quality,
        evidence={
            "venue_code": trade.venue_code,
            "venue_market_id": trade.venue_market_id,
            "outcome_key": trade.outcome_key,
            "directional_side": trade.directional_side,
            "side_confidence": trade.side_confidence,
            "price": str(trade.price),
            "contracts": str(trade.contracts),
            "capital_at_risk_usd": str(trade.capital_at_risk_usd),
            "payout_notional_usd": str(trade.payout_notional_usd),
            "min_capital_at_risk_usd": str(rule.min_capital_at_risk_usd),
            "min_payout_notional_usd": str(rule.min_payout_notional_usd),
            "margin_to_threshold": _margin_float(max(capital_margin, payout_margin)),
            "margin_to_threshold_unit": "relative_ratio",
            "baseline_sample_quality": "configured_threshold_no_baseline",
            "degraded_reasons": _dq_reasons,
        },
    )
