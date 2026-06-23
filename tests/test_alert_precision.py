from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest


def _ts(offset_seconds: int) -> datetime:
    return datetime(2026, 6, 22, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def test_cli_parses_alert_eval_command(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()
    ns = parser.parse_args([
        "alert-eval",
        "--manifest",
        str(tmp_path / "manifest.yaml"),
        "--limit",
        "25",
        "--format",
        "json",
    ])

    assert ns.command == "alert-eval"
    assert ns.manifest == str(tmp_path / "manifest.yaml")
    assert ns.limit == 25
    assert ns.format == "json"


def test_precision_proxy_grid_scores_hits_misses_and_insufficient_rows() -> None:
    from pmfi.qualification.alert_precision import score_alert_precision

    alerts = [
        {"alert_id": "a1", "market_id": "m1", "rule_key": "volume_spike_v1", "fired_at": _ts(0)},
        {"alert_id": "a2", "market_id": "m1", "rule_key": "volume_spike_v1", "fired_at": _ts(20)},
        {"alert_id": "a3", "market_id": "m2", "rule_key": "large_trade_absolute_v1", "fired_at": _ts(0)},
    ]
    prices = [
        {"market_id": "m1", "ts": _ts(0), "price": Decimal("0.40")},
        {"market_id": "m1", "ts": _ts(60), "price": Decimal("0.47")},
        {"market_id": "m1", "ts": _ts(80), "price": Decimal("0.43")},
        {"market_id": "m2", "ts": _ts(0), "price": Decimal("0.51")},
    ]

    measurements = score_alert_precision(
        alerts,
        prices,
        windows_seconds=[60],
        thresholds=[Decimal("0.05")],
    )

    by_rule = {row["rule_key"]: row for row in measurements["per_rule_grid"]}
    assert measurements["overall_precision_at_proxy_pooled_over_grid"] == 0.5
    assert "overall_precision_at_proxy" not in measurements
    assert by_rule["volume_spike_v1"]["alerts"] == 2
    assert by_rule["volume_spike_v1"]["scorable_alerts"] == 2
    assert by_rule["volume_spike_v1"]["proxy_hits"] == 1
    assert by_rule["volume_spike_v1"]["proxy_misses"] == 1
    assert by_rule["volume_spike_v1"]["precision_at_proxy"] == 0.5
    assert by_rule["large_trade_absolute_v1"]["insufficient_alerts"] == 1
    assert by_rule["large_trade_absolute_v1"]["precision_at_proxy"] is None


def test_precision_proxy_excludes_insufficient_from_denominator() -> None:
    from pmfi.qualification.alert_precision import score_alert_precision

    measurements = score_alert_precision(
        [{"alert_id": "a1", "market_id": "m1", "rule_key": "r1", "fired_at": _ts(0)}],
        [{"market_id": "m1", "ts": _ts(0), "price": Decimal("0.40")}],
        windows_seconds=[60],
        thresholds=[Decimal("0.01")],
    )

    row = measurements["per_rule_grid"][0]
    assert row["alerts"] == 1
    assert row["scorable_alerts"] == 0
    assert row["insufficient_alerts"] == 1
    assert row["precision_at_proxy"] is None
    assert measurements["proxy_thresholds_positive"] is True


def test_alert_precision_rejects_non_positive_proxy_thresholds() -> None:
    from pmfi.qualification.alert_precision import _grid_from_manifest

    with pytest.raises(ValueError, match="grid.thresholds must be > 0"):
        _grid_from_manifest({"grid": {"windows_seconds": [60], "thresholds": [0]}})

    with pytest.raises(ValueError, match="grid.thresholds must be > 0"):
        _grid_from_manifest({"grid": {"windows_seconds": [60], "thresholds": [-0.01]}})


def test_alert_precision_evidence_is_proxy_labeled_and_recommend_only(tmp_path: Path) -> None:
    import yaml

    from pmfi.qualification.alert_precision import build_alert_precision_evidence

    manifest = {
        "version": "pmfi-alert-precision-manifest.v1",
        "scenario_id": "M-TRUTH-v2-MEASURE",
        "scenario_version": "v1",
        "profile": "alert_precision_proxy_measure",
        "price_source": "normalized_trades",
        "grid": {"windows_seconds": [60], "thresholds": [0.05]},
        "required_facets": ["OFFLINE", "POSTGRES_INTEGRATION", "READ_ONLY_PRIMARY"],
        "manual_deferred_facets": [{"facet": "OPERATOR_LABELED_GROUND_TRUTH", "reason": "labels deferred"}],
    }
    manifest_path = tmp_path / "alert_precision_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    evidence = build_alert_precision_evidence(
        manifest=manifest,
        manifest_path=manifest_path,
        measurements={
            "metric_name": "precision_at_proxy",
            "proxy_thresholds_positive": True,
            "alert_count": 1,
            "scorable_alerts": 1,
            "grid_cell_count": 1,
            "classified_alert_grid_rows": 1,
            "overall_precision_at_proxy_pooled_over_grid": 1.0,
            "per_rule_grid": [
                {
                    "rule_key": "volume_spike_v1",
                    "window_seconds": 60,
                    "threshold": 0.05,
                    "alerts": 1,
                    "scorable_alerts": 1,
                    "proxy_hits": 1,
                    "proxy_misses": 0,
                    "insufficient_alerts": 0,
                    "precision_at_proxy": 1.0,
                }
            ],
            "no_secrets_in_fixtures_logs_or_evidence": False,
        },
        actual_facets=["OFFLINE", "POSTGRES_INTEGRATION", "READ_ONLY_PRIMARY"],
        commands=["pmfi alert-eval --format json"],
    )

    assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
    assert evidence["scenario_id"] == "M-TRUTH-v2-MEASURE"
    assert evidence["outcome"] == "PASS"
    assert evidence["completeness_classifications"]["precision"] == "PROXY_BACKTEST_LOCAL"
    assert evidence["measurements"]["metric_name"] == "precision_at_proxy"
    assert evidence["measurements"]["overall_precision_at_proxy_pooled_over_grid"] == 1.0
    assert "overall_precision_at_proxy" not in evidence["measurements"]
    assert evidence["recommended_actions"]["mode"] == "recommend_only"
    assert evidence["recommended_actions"]["mutates_rules"] is False
    assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]


def test_alert_precision_invariants_fail_without_scorable_proxy_cells() -> None:
    from pmfi.qualification.alert_precision import evaluate_alert_precision_pass_invariants

    invariants = evaluate_alert_precision_pass_invariants({
        "metric_name": "precision_at_proxy",
        "proxy_thresholds_positive": False,
        "alert_count": 1,
        "scorable_alerts": 0,
        "grid_cell_count": 1,
        "classified_alert_grid_rows": 1,
        "per_rule_grid": [
            {
                "rule_key": "r1",
                "window_seconds": 60,
                "threshold": 0.05,
                "alerts": 1,
                "scorable_alerts": 0,
                "proxy_hits": 0,
                "proxy_misses": 0,
                "insufficient_alerts": 1,
                "precision_at_proxy": None,
            }
        ],
        "no_secrets_in_fixtures_logs_or_evidence": True,
    })

    assert invariants["proxy_denominator_has_scorable_alerts"] is False
    assert invariants["proxy_thresholds_are_positive"] is False
    assert "insufficient_excluded_from_denominator" not in invariants
    assert invariants["counts_balance_alerts_equals_scorable_plus_insufficient"] is True
