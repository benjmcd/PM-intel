from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace


def _decision(
    *,
    rule_id: str = "large_trade_absolute_v1",
    severity: str = "medium",
    market: str = "market-a",
    confidence: str = "high",
) -> dict:
    return {
        "rule_id": rule_id,
        "rule_version": "alert_rules.v1",
        "severity": severity,
        "confidence": confidence,
        "score": Decimal("0.75"),
        "market": market,
        "evidence": {"reason": "test"},
    }


def test_build_backtest_summary_groups_counts_and_samples():
    from pmfi.reporting import build_backtest_summary

    summary = build_backtest_summary([
        _decision(rule_id="rule_a", severity="high", market="m1"),
        _decision(rule_id="rule_a", severity="medium", market="m1"),
        _decision(rule_id="rule_b", severity="low", market="m2"),
    ])

    assert summary["total_alerts"] == 3
    assert summary["by_rule"] == {"rule_a": 2, "rule_b": 1}
    assert summary["by_severity"] == {"high": 1, "medium": 1, "low": 1}
    assert summary["by_market"] == {"m1": 2, "m2": 1}
    assert len(summary["samples_by_rule"]["rule_a"]) == 2


def test_build_backtest_summary_caps_samples_per_rule():
    from pmfi.reporting import build_backtest_summary

    summary = build_backtest_summary([_decision(rule_id="rule_a") for _ in range(5)], sample_per_rule=2)

    assert summary["total_alerts"] == 5
    assert len(summary["samples_by_rule"]["rule_a"]) == 2


def test_backtest_parser_accepts_read_only_filters():
    from pmfi.cli import _build_parser

    ns = _build_parser().parse_args([
        "backtest",
        "--from",
        "2026-06-20T00:00:00+00:00",
        "--to",
        "2026-06-21T00:00:00+00:00",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD",
        "--cold-start",
        "--format",
        "json",
    ])

    assert ns.command == "backtest"
    assert ns.backtest_from == "2026-06-20T00:00:00+00:00"
    assert ns.backtest_to == "2026-06-21T00:00:00+00:00"
    assert ns.limit == 0
    assert ns.backtest_venue == "kalshi"
    assert ns.backtest_market == "KXBTCD"
    assert ns.cold_start is True
    assert ns.format == "json"


def test_cmd_backtest_replays_without_persisting(monkeypatch, capsys):
    from pmfi.commands import backtest as backtest_cmd
    from pmfi.domain import AlertDecision, NormalizedTrade
    from pmfi.replay import ReplayResult

    calls = {}

    class FakePool:
        pass

    async def fake_create_pool(url):
        calls["db_url"] = url
        return FakePool()

    async def fake_close_pool(pool):
        calls["closed"] = pool

    async def fake_replay_from_db(pool, **kwargs):
        calls["replay"] = kwargs
        trade = NormalizedTrade(
            venue_code="kalshi",
            venue_market_id="KXBTCD",
            outcome_key="yes",
            price=Decimal("0.50"),
            contracts=Decimal("10"),
            capital_at_risk_usd=Decimal("5"),
            payout_notional_usd=Decimal("10"),
        )
        alert = AlertDecision(
            emit_alert=True,
            rule_id="large_trade_absolute_v1",
            rule_version="alert_rules.v1",
            severity="medium",
            confidence="high",
            score=Decimal("0.75"),
            reason_codes=("test",),
            evidence={},
            data_quality="verified",
        )
        return [ReplayResult(fixture_path="db:KXBTCD", trade=trade, alerts=[alert])]

    monkeypatch.setattr("pmfi.db.create_pool", fake_create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", fake_close_pool)
    monkeypatch.setattr("pmfi.replay.replay_from_db", fake_replay_from_db)
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="db-url")))

    rc = backtest_cmd.cmd_backtest(SimpleNamespace(
        backtest_from=None,
        backtest_to=None,
        limit=10,
        backtest_venue="kalshi",
        backtest_market="KXBTCD",
        cold_start=True,
        format="json",
    ))

    assert rc == 0
    assert calls["replay"]["persist"] is False
    assert calls["replay"]["seed"] is False
    assert calls["replay"]["normalized_only"] is True
    assert calls["replay"]["limit"] == 10
    assert calls["replay"]["venue"] == "kalshi"
    assert calls["replay"]["market"] == "KXBTCD"
    out = capsys.readouterr().out
    assert '"persisted": false' in out
    assert '"total_alerts": 1' in out
