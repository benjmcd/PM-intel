from decimal import Decimal
from pathlib import Path

from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture
from pmfi.scoring import score_large_trade, assess_data_quality, _cap_confidence

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_large_trade_rule_alerts_on_capital_at_risk():
    raw = load_raw_event(FIXTURES / "polymarket_last_trade_price.json")
    trade = normalize_polymarket_fixture(raw)
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert "capital_at_risk_threshold" in decision.reason_codes
    assert decision.evidence["capital_at_risk_usd"] == "33600.00"


# ---------------------------------------------------------------------------
# Alert-safety: confidence gating and evidence completeness in scoring.py
# ---------------------------------------------------------------------------

def test_large_trade_unknown_direction_caps_confidence():
    """large_trade_absolute_v1 with directional_side='unknown' must not exceed 'medium'."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-degraded",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="unknown",
    )
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert decision.confidence in ("low", "medium"), (
        f"degraded data must not emit confidence above 'medium', got {decision.confidence!r}"
    )
    assert decision.data_quality == "degraded"
    assert "direction_unknown" in decision.evidence.get("degraded_reasons", [])


def test_large_trade_unknown_outcome_caps_confidence():
    """large_trade_absolute_v1 with outcome_key='' must not exceed 'medium'."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-degraded-outcome",
        outcome_key="unknown",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="yes",
    )
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert decision.confidence in ("low", "medium")
    assert decision.data_quality == "degraded"
    assert "outcome_unknown" in decision.evidence.get("degraded_reasons", [])


def test_large_trade_clean_trade_confidence_unchanged():
    """large_trade_absolute_v1 with clean data retains 'medium' confidence."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-clean",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="yes",
    )
    decision = score_large_trade(trade)
    assert decision.emit_alert is True
    assert decision.confidence == "medium"
    assert decision.data_quality == "verified"
    assert decision.evidence.get("degraded_reasons") == []


def test_large_trade_evidence_includes_thresholds():
    """large_trade_absolute_v1 evidence must include configured threshold keys."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-thresholds",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="yes",
    )
    decision = score_large_trade(trade)
    ev = decision.evidence
    assert "min_capital_at_risk_usd" in ev
    assert "min_payout_notional_usd" in ev
    assert "outcome_key" in ev
    assert "directional_side" in ev
    assert "degraded_reasons" in ev


def test_large_trade_capital_only_margin_uses_satisfied_or_gate():
    """OR-gated margin follows the satisfied capital threshold."""
    from pmfi.domain import NormalizedTrade

    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="capital-only-margin",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("100000"),
        capital_at_risk_usd=Decimal("50000"),
        payout_notional_usd=Decimal("90000"),
        directional_side="yes",
    )

    decision = score_large_trade(trade)

    assert decision.emit_alert is True
    assert decision.reason_codes == ("capital_at_risk_threshold",)
    assert decision.evidence["margin_to_threshold"] == 1.0
    assert decision.evidence["margin_to_threshold_unit"] == "relative_ratio"


def test_large_trade_warnings_degrade_quality():
    """Trades with non-empty warnings are flagged as degraded."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="test-warnings",
        outcome_key="yes",
        price=Decimal("0.8"),
        contracts=Decimal("500000"),
        capital_at_risk_usd=Decimal("400000"),
        payout_notional_usd=Decimal("500000"),
        directional_side="yes",
        warnings=("stale_price_feed",),
    )
    decision = score_large_trade(trade)
    assert decision.data_quality == "degraded"
    assert "stale_price_feed" in decision.evidence.get("degraded_reasons", [])


def test_cap_confidence_helper():
    """_cap_confidence returns the lower of two ordinal confidence levels."""
    assert _cap_confidence("high", "medium") == "medium"
    assert _cap_confidence("medium", "medium") == "medium"
    assert _cap_confidence("low", "medium") == "low"
    assert _cap_confidence("high", "high") == "high"
    assert _cap_confidence("low", "high") == "low"


def test_assess_data_quality_clean():
    """assess_data_quality returns 'ok' for a fully-resolved trade."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="q-clean",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("1000"),
        capital_at_risk_usd=Decimal("500"),
        payout_notional_usd=Decimal("1000"),
        directional_side="yes",
    )
    dq, reasons = assess_data_quality(trade)
    assert dq == "ok"
    assert reasons == []


def test_assess_data_quality_degraded_direction():
    """assess_data_quality returns 'degraded' when directional_side is 'unknown'."""
    from pmfi.domain import NormalizedTrade
    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="q-degraded",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("1000"),
        capital_at_risk_usd=Decimal("500"),
        payout_notional_usd=Decimal("1000"),
        directional_side="unknown",
    )
    dq, reasons = assess_data_quality(trade)
    assert dq == "degraded"
    assert "direction_unknown" in reasons
