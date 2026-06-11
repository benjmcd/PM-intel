"""Unit tests for the transparent composite (corroboration) annotation."""
from __future__ import annotations

from decimal import Decimal

from pmfi.domain import AlertDecision
from pmfi.scoring import apply_corroboration


def _decision(rule_id: str) -> AlertDecision:
    return AlertDecision(
        emit_alert=True, rule_id=rule_id, rule_version="alert_rules.v1",
        severity="high", confidence="high", score=Decimal("0.8"),
        reason_codes=("x",), evidence={"k": "v"}, data_quality="verified",
    )


def test_single_rule_not_annotated():
    ds = [_decision("rule_a")]
    apply_corroboration(ds)
    assert "corroborating_rules" not in ds[0].evidence


def test_two_rules_annotated():
    ds = [_decision("rule_a"), _decision("rule_b")]
    apply_corroboration(ds)
    assert ds[0].evidence["corroboration_count"] == 2
    assert ds[0].evidence["corroborating_rules"] == ["rule_b"]
    assert ds[1].evidence["corroborating_rules"] == ["rule_a"]


def test_additive_only_does_not_change_core_fields():
    ds = [_decision("rule_a"), _decision("rule_b")]
    sev_before = [d.severity for d in ds]
    conf_before = [d.confidence for d in ds]
    score_before = [d.score for d in ds]
    apply_corroboration(ds)
    assert [d.severity for d in ds] == sev_before
    assert [d.confidence for d in ds] == conf_before
    assert [d.score for d in ds] == score_before
