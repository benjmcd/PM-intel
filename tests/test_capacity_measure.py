from __future__ import annotations

import json
from pathlib import Path


def test_cli_parses_capacity_measure_command(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()
    ns = parser.parse_args([
        "capacity-measure",
        "--manifest",
        str(tmp_path / "manifest.yaml"),
        "--format",
        "json",
    ])

    assert ns.command == "capacity-measure"
    assert ns.manifest == str(tmp_path / "manifest.yaml")
    assert ns.format == "json"


def test_capacity_projection_uses_measured_growth() -> None:
    from pmfi.qualification.capacity import compute_growth_projection

    projection = compute_growth_projection(
        db_size_before_bytes=1_000,
        db_size_after_bytes=1_900,
        workload_events=9,
        free_disk_bytes=9_000,
    )

    assert projection["db_growth_bytes"] == 900
    assert projection["est_bytes_per_event"] == 100.0
    assert projection["projected_runway_events_or_days"] == 90


def test_capacity_projection_marks_degenerate_event_count() -> None:
    from pmfi.qualification.capacity import compute_growth_projection, evaluate_capacity_pass_invariants

    measurements = {
        "pool_acquire_p95_ms": 2.0,
        "sample_count": 4,
        "rto_restart_seconds": 0.1,
        "rto_restore_seconds": 0.2,
        "workload_events": 0,
        "free_disk_bytes": 100,
        "db_size_bytes": 1_000,
        "no_secrets_in_fixtures_logs_or_evidence": True,
        **compute_growth_projection(
            db_size_before_bytes=1_000,
            db_size_after_bytes=1_000,
            workload_events=0,
            free_disk_bytes=100,
        ),
    }

    invariants = evaluate_capacity_pass_invariants(measurements, min_pool_samples=4)

    assert invariants["disk_growth_projection_is_computed"] is False
    assert invariants["bounded_workload_executed"] is False


def test_capacity_recommendations_are_recommend_only_and_derived_from_measurements() -> None:
    from pmfi.qualification.capacity import CURRENT_CAPACITY_DEFAULTS, recommend_capacity_thresholds

    recommendations = recommend_capacity_thresholds(
        {
            "pool_acquire_p95_ms": 80.0,
            "est_bytes_per_event": 2_000.0,
            "rto_restart_seconds": 20.0,
            "rto_restore_seconds": 90.0,
        },
        current_defaults=CURRENT_CAPACITY_DEFAULTS,
    )

    assert recommendations["mode"] == "recommend_only"
    assert recommendations["current"]["pool_acquire_wait_p95_alarm_ms"] == 100
    assert recommendations["recommended"]["pool_acquire_wait_p95_alarm_ms"] == 160
    assert recommendations["recommended"]["rto_restart_seconds"] == 300
    assert recommendations["recommended"]["rto_restore_seconds"] == 1800
    assert "config defaults are not changed" in recommendations["rationale"]


def test_capacity_evidence_honesty_does_not_claim_db_facets_offline(tmp_path: Path) -> None:
    from pmfi.qualification.capacity import build_capacity_evidence

    manifest = {
        "scenario_id": "M-CAPACITY",
        "scenario_version": "v1",
        "profile": "bounded_local_capacity_measure",
        "required_facets": ["OFFLINE", "POSTGRES_INTEGRATION"],
        "manual_deferred_facets": [{"facet": "LONG_HORIZON_SOAK", "reason": "operator horizon"}],
    }
    manifest_path = tmp_path / "capacity_manifest.yaml"
    manifest_path.write_text("scenario_id: M-CAPACITY\n", encoding="utf-8")
    measurements = {
        "pool_acquire_p95_ms": None,
        "sample_count": 0,
        "free_disk_bytes": 100,
        "db_size_bytes": 1_000,
        "db_growth_bytes": 0,
        "est_bytes_per_event": None,
        "projected_runway_events_or_days": None,
        "rto_restart_seconds": None,
        "rto_restore_seconds": None,
        "workload_events": 0,
        "concurrency": 0,
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }

    evidence = build_capacity_evidence(
        manifest=manifest,
        manifest_path=manifest_path,
        measurements=measurements,
        postgres_version="not_measured",
        actual_facets=["OFFLINE"],
        commands=["offline-unit"],
        scratch_databases={},
        measurement_error="Postgres unavailable",
    )

    assert evidence["outcome"] == "INCONCLUSIVE"
    assert evidence["evidence"]["actual_facets"] == ["OFFLINE"]
    assert evidence["evidence"]["deferred_facets"] == ["LONG_HORIZON_SOAK"]
    assert evidence["blocker_or_inconclusive_reason"] == "Postgres unavailable"
    assert evidence["recommended_thresholds"]["mode"] == "recommend_only"
    assert json.dumps(evidence)


def test_task_capacity_measure_forwards_supported_cli_flags(monkeypatch, tmp_path: Path) -> None:
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "capacity-measure",
        "--manifest",
        str(tmp_path / "capacity.yaml"),
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "capacity-measure",
        "--manifest",
        str(tmp_path / "capacity.yaml"),
        "--format",
        "json",
    )]
