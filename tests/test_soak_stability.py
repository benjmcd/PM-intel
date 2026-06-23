from __future__ import annotations

from pathlib import Path


def test_cli_parses_soak_measure_stability_without_breaking_analyzer(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()
    ns = parser.parse_args([
        "soak",
        "--measure-stability",
        "--manifest",
        str(tmp_path / "soak.yaml"),
        "--format",
        "json",
    ])

    assert ns.command == "soak"
    assert ns.measure_stability is True
    assert ns.manifest == str(tmp_path / "soak.yaml")
    assert ns.format == "json"


def test_soak_stability_invariants_require_recovery_and_samples() -> None:
    from pmfi.qualification.soak_stability import evaluate_soak_stability_pass_invariants

    invariants = evaluate_soak_stability_pass_invariants(
        {
            "events_processed": 10,
            "throughput_events_per_second": 5.0,
            "sample_count": 3,
            "min_required_samples": 4,
            "pool_acquire_p95_ms": 1.0,
            "memory_peak_mb": 2.0,
            "dead_letters_created": 0,
            "max_allowed_dead_letters": 0,
            "recovery_induced": True,
            "recovery_successful": False,
            "no_secrets_in_fixtures_logs_or_evidence": True,
        }
    )

    assert invariants["sample_count_at_least_minimum"] is False
    assert invariants["recovered_after_induced_pool_recreation"] is False


def test_soak_stability_evidence_is_bounded_and_recommend_only(tmp_path: Path) -> None:
    import yaml

    from pmfi.qualification.soak_stability import build_soak_stability_evidence

    manifest = {
        "version": "pmfi-soak-stability-manifest.v1",
        "scenario_id": "M2-SOAK",
        "scenario_version": "v1",
        "profile": "bounded_local_soak_stability",
        "run_key": "soak_stability_v1",
        "workload": {"events": 4, "min_samples": 2},
        "required_facets": ["OFFLINE", "POSTGRES_INTEGRATION", "SCRATCH_DB"],
        "manual_deferred_facets": [{"facet": "MULTI_DAY_SOAK", "reason": "deferred"}],
    }
    manifest_path = tmp_path / "soak_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    measurements = {
        "events_processed": 4,
        "throughput_events_per_second": 2.0,
        "sample_count": 2,
        "min_required_samples": 2,
        "pool_acquire_p95_ms": 0.5,
        "pool_acquire_max_ms": 0.7,
        "memory_start_mb": 1.0,
        "memory_peak_mb": 2.0,
        "dead_letters_created": 0,
        "max_allowed_dead_letters": 0,
        "recovery_induced": True,
        "recovery_successful": True,
        "samples": [{"events_processed": 0}, {"events_processed": 4}],
        "no_secrets_in_fixtures_logs_or_evidence": False,
    }

    evidence = build_soak_stability_evidence(
        manifest=manifest,
        manifest_path=manifest_path,
        measurements=measurements,
        actual_facets=["OFFLINE", "POSTGRES_INTEGRATION", "SCRATCH_DB"],
        commands=["pmfi soak --measure-stability --format json"],
        scratch_databases={"source": "pmfi_soak_example"},
    )

    assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
    assert evidence["scenario_id"] == "M2-SOAK"
    assert evidence["outcome"] == "PASS"
    assert evidence["completeness_classifications"]["soak"] == "MEASURED_BOUNDED_LOCAL"
    assert evidence["recommended_thresholds"]["mode"] == "recommend_only"
    assert evidence["recommended_thresholds"]["mutates_config"] is False
    assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
