from __future__ import annotations
from decimal import Decimal
from pathlib import Path
from pmfi.domain import NormalizedTrade, AlertDecision
from pmfi.replay import ReplayResult
from pmfi.reporting import build_report, write_report


def _make_result(rule_id: str, venue: str = "polymarket") -> ReplayResult:
    trade = NormalizedTrade(
        venue_code=venue,
        venue_market_id="test-mkt",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("10000"),
        capital_at_risk_usd=Decimal("5000"),
        payout_notional_usd=Decimal("10000"),
    )
    decision = AlertDecision(
        emit_alert=True,
        rule_id=rule_id,
        rule_version="v1",
        severity="medium",
        confidence="low",
        score=Decimal("0.5"),
        reason_codes=("test",),
        evidence={"baseline_status": "baseline_missing", "baseline_state": "baseline_missing"},
        data_quality="baseline_pending",
    )
    return ReplayResult(fixture_path="test.json", trade=trade, alerts=[decision])


def test_build_report_empty():
    summary = build_report([])
    assert summary.fixture_count == 0
    assert summary.alert_count == 0
    assert any("Fixtures processed" in l for l in summary.lines)


def test_build_report_counts():
    results = [
        _make_result("large_trade_absolute_v1"),
        _make_result("large_trade_absolute_v1"),
        _make_result("market_relative_large_trade_v1", venue="kalshi"),
    ]
    summary = build_report(results)
    assert summary.alert_count == 3
    assert summary.alerts_by_rule["large_trade_absolute_v1"] == 2
    assert summary.alerts_by_rule["market_relative_large_trade_v1"] == 1
    assert summary.alerts_by_venue["polymarket"] == 2
    assert summary.alerts_by_venue["kalshi"] == 1


def test_build_report_cluster_events():
    trade = NormalizedTrade(
        venue_code="polymarket", venue_market_id="cluster-mkt",
        outcome_key="yes", price=Decimal("0.6"),
        contracts=Decimal("20000"), capital_at_risk_usd=Decimal("12000"),
        payout_notional_usd=Decimal("20000"),
    )
    decision = AlertDecision(
        emit_alert=True, rule_id="directional_cluster_v1", rule_version="v1",
        severity="high", confidence="medium", score=Decimal("0.75"),
        reason_codes=("directional_cluster_detected",),
        evidence={"dominant_side": "yes", "cluster_trade_count": "3",
                   "net_capital_usd": "30000", "price_impact_cents": "6"},
        data_quality="in_window",
    )
    results = [ReplayResult(fixture_path="f.json", trade=trade, alerts=[decision])]
    summary = build_report(results)
    assert len(summary.cluster_events) == 1
    assert summary.cluster_events[0]["dominant_side"] == "yes"


def test_write_report(tmp_path):
    results = [_make_result("large_trade_absolute_v1")]
    summary = build_report(results)
    out = write_report(summary, tmp_path)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Fixtures processed" in content
    assert "large_trade_absolute_v1" in content
