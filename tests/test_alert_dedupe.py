from __future__ import annotations
from decimal import Decimal
from pmfi.db.repos.alerts import _dedupe_key
from pmfi.domain import AlertDecision


def _decision(rule_id: str = "large_trade_absolute_v1", version: str = "alert_rules.v1") -> AlertDecision:
    return AlertDecision(
        emit_alert=True,
        rule_id=rule_id,
        rule_version=version,
        severity="medium",
        confidence="medium",
        score=Decimal("0.6"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={},
        data_quality="unverified",
    )


_FIXED_BUCKET = "2026-06-06-00"


def test_dedupe_key_is_deterministic():
    d = _decision()
    key1 = _dedupe_key(d, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    key2 = _dedupe_key(d, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    assert key1 == key2


def test_dedupe_key_differs_by_venue():
    d = _decision()
    k1 = _dedupe_key(d, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    k2 = _dedupe_key(d, venue_code="kalshi", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    assert k1 != k2


def test_dedupe_key_differs_by_market():
    d = _decision()
    k1 = _dedupe_key(d, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    k2 = _dedupe_key(d, venue_code="polymarket", market_id="uuid-xyz", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    assert k1 != k2


def test_dedupe_key_differs_by_rule():
    d1 = _decision("large_trade_absolute_v1")
    d2 = _decision("market_relative_large_trade_v1")
    k1 = _dedupe_key(d1, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    k2 = _dedupe_key(d2, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    assert k1 != k2


def test_dedupe_key_length_is_32():
    d = _decision()
    key = _dedupe_key(d, venue_code="polymarket", market_id="uuid-abc", outcome_key="yes", hour_bucket=_FIXED_BUCKET)
    assert len(key) == 32


def test_dedupe_key_none_market_id_stable():
    d = _decision()
    k1 = _dedupe_key(d, venue_code="polymarket", market_id=None, outcome_key=None, hour_bucket=_FIXED_BUCKET)
    k2 = _dedupe_key(d, venue_code="polymarket", market_id=None, outcome_key=None, hour_bucket=_FIXED_BUCKET)
    assert k1 == k2
