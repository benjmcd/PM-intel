from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_soak_baseline_manifest_is_separate_large_and_bounded() -> None:
    import yaml

    small_manifest_path = ROOT / "tests" / "qualification" / "soak_manifest.yaml"
    baseline_manifest_path = ROOT / "tests" / "qualification" / "soak_baseline_manifest.yaml"

    small_manifest = yaml.safe_load(small_manifest_path.read_text(encoding="utf-8"))
    baseline_manifest = yaml.safe_load(baseline_manifest_path.read_text(encoding="utf-8"))

    assert small_manifest["workload"]["events"] == 30
    assert baseline_manifest_path != small_manifest_path
    assert baseline_manifest["scenario_id"] == "M2-SOAK-BASELINE"
    assert baseline_manifest["workload"]["events"] >= 1000
    assert baseline_manifest["workload"]["max_duration_seconds"] >= 120
    assert baseline_manifest["workload"]["sample_every_events"] < baseline_manifest["workload"]["events"]


def test_soak_stability_rate_metrics_are_per_1000_events() -> None:
    from pmfi.qualification.soak_stability import add_soak_baseline_rate_metrics

    measurements = add_soak_baseline_rate_metrics(
        {
            "events_processed": 2500,
            "memory_growth_mb": 12.5,
            "dead_letters_created": 5,
        }
    )

    assert measurements["memory_growth_per_1000_events_mb"] == 5.0
    assert measurements["dead_letters_per_1000_events"] == 2.0


def test_soak_stability_duration_bound_can_stop_before_event_count() -> None:
    from pmfi.qualification.soak_stability import workload_stop_reason

    assert workload_stop_reason(
        events_processed=999,
        requested_events=1000,
        started_perf=10.0,
        now_perf=131.0,
        max_duration_seconds=120.0,
    ) == "duration_limit_reached"

    assert workload_stop_reason(
        events_processed=1000,
        requested_events=1000,
        started_perf=10.0,
        now_perf=11.0,
        max_duration_seconds=120.0,
    ) == "event_count_reached"


def test_soak_deep_manifests_are_separate_bounded_profiles() -> None:
    import yaml

    fast_manifest_path = ROOT / "tests" / "qualification" / "soak_manifest.yaml"
    baseline_manifest_path = ROOT / "tests" / "qualification" / "soak_baseline_manifest.yaml"
    leakslope_manifest_path = ROOT / "tests" / "qualification" / "soak_leakslope_manifest.yaml"
    contention_manifest_path = ROOT / "tests" / "qualification" / "soak_contention_manifest.yaml"

    fast_manifest = yaml.safe_load(fast_manifest_path.read_text(encoding="utf-8"))
    baseline_manifest = yaml.safe_load(baseline_manifest_path.read_text(encoding="utf-8"))
    leakslope_manifest = yaml.safe_load(leakslope_manifest_path.read_text(encoding="utf-8"))
    contention_manifest = yaml.safe_load(contention_manifest_path.read_text(encoding="utf-8"))

    assert fast_manifest["workload"]["events"] == 30
    assert baseline_manifest["workload"]["events"] == 3000
    assert leakslope_manifest["scenario_id"] == "M2-SOAK-DEEP-LEAKSLOPE"
    assert leakslope_manifest["workload"]["events"] >= 30000
    assert leakslope_manifest["workload"]["max_duration_seconds"] >= 600
    assert leakslope_manifest["workload"]["concurrency"] == 1
    assert contention_manifest["scenario_id"] == "M2-SOAK-DEEP-CONTENTION"
    assert contention_manifest["workload"]["concurrency"] > contention_manifest["workload"]["pool_size"]
    assert contention_manifest["workload"]["max_duration_seconds"] > 0


def test_windowed_memory_trend_detects_plateau_and_linear_leak() -> None:
    from pmfi.qualification.soak_stability import compute_windowed_memory_trend

    flattening_samples = [
        {"events_processed": 100, "memory_current_mb": 1.0},
        {"events_processed": 200, "memory_current_mb": 4.0},
        {"events_processed": 300, "memory_current_mb": 6.0},
        {"events_processed": 800, "memory_current_mb": 6.4},
        {"events_processed": 900, "memory_current_mb": 6.45},
        {"events_processed": 1000, "memory_current_mb": 6.5},
    ]
    linear_samples = [
        {"events_processed": 100, "memory_current_mb": 1.0},
        {"events_processed": 200, "memory_current_mb": 2.0},
        {"events_processed": 300, "memory_current_mb": 3.0},
        {"events_processed": 800, "memory_current_mb": 8.0},
        {"events_processed": 900, "memory_current_mb": 9.0},
        {"events_processed": 1000, "memory_current_mb": 10.0},
    ]

    plateau = compute_windowed_memory_trend(flattening_samples, window_sample_count=3)
    leak = compute_windowed_memory_trend(linear_samples, window_sample_count=3)

    assert plateau["early_window"]["growth_per_1000_events_mb"] == 25.0
    assert plateau["late_window"]["growth_per_1000_events_mb"] == 0.5
    assert plateau["late_to_early_rate_ratio"] < 0.1
    assert plateau["verdict"] == "warmup_plateau"
    assert plateau["sustained_growth"] is False

    assert leak["early_window"]["growth_per_1000_events_mb"] == 10.0
    assert leak["late_window"]["growth_per_1000_events_mb"] == 10.0
    assert leak["late_to_early_rate_ratio"] == 1.0
    assert leak["verdict"] == "sustained_linear_growth"
    assert leak["sustained_growth"] is True


def test_pool_contention_evidence_requires_material_waits() -> None:
    from pmfi.qualification.soak_stability import summarize_pool_contention

    saturated = summarize_pool_contention(
        {
            "pool_acquire_p95_ms": 1.2,
            "pool_acquire_sample_count": 64,
            "workload_concurrency": 16,
            "pool_size": 4,
            "idle_pool_acquire_reference_p95_ms": 0.03,
        }
    )
    idle = summarize_pool_contention(
        {
            "pool_acquire_p95_ms": 0.03,
            "pool_acquire_sample_count": 64,
            "workload_concurrency": 16,
            "pool_size": 4,
            "idle_pool_acquire_reference_p95_ms": 0.03,
        }
    )

    assert saturated["concurrency_exceeds_pool_size"] is True
    assert saturated["pool_acquire_p95_materially_contended"] is True
    assert saturated["p95_to_idle_ratio"] == 40.0
    assert idle["pool_acquire_p95_materially_contended"] is False


def test_soak_stability_invariants_require_recovery_and_samples() -> None:
    from pmfi.qualification.soak_stability import evaluate_soak_stability_pass_invariants

    invariants = evaluate_soak_stability_pass_invariants(
        {
            "events_processed": 10,
            "throughput_events_per_second": 5.0,
            "sample_count": 3,
            "min_required_samples": 4,
            "pool_acquire_p95_ms": 1.0,
            "pool_acquire_sample_count": 19,
            "memory_peak_mb": 2.0,
            "memory_growth_mb": 0.1,
            "memory_growth_tolerance_mb": 1.0,
            "dead_letters_created": 0,
            "max_allowed_dead_letters": 0,
            "recovery_induced": True,
            "recovery_successful": False,
            "no_secrets_in_fixtures_logs_or_evidence": True,
        }
    )

    assert invariants["sample_count_at_least_minimum"] is False
    assert invariants["recovered_after_induced_pool_recreation"] is False
    assert invariants["pool_acquire_p95_has_minimum_samples"] is False


def test_soak_stability_memory_growth_detector_fails_unbounded_series_and_passes_bounded() -> None:
    from pmfi.qualification.soak_stability import evaluate_soak_stability_pass_invariants

    base = {
        "events_processed": 20,
        "throughput_events_per_second": 5.0,
        "sample_count": 4,
        "min_required_samples": 4,
        "pool_acquire_p95_ms": 1.0,
        "pool_acquire_sample_count": 20,
        "memory_start_mb": 0.0,
        "memory_peak_mb": 10.0,
        "dead_letters_created": 0,
        "max_allowed_dead_letters": 0,
        "recovery_induced": True,
        "recovery_successful": True,
        "memory_growth_tolerance_mb": 1.0,
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }
    leaking = {
        **base,
        "samples": [
            {"events_processed": 0, "memory_current_mb": 0.01},
            {"events_processed": 5, "memory_current_mb": 0.10},
            {"events_processed": 20, "memory_current_mb": 3.50},
        ],
    }
    bounded = {
        **base,
        "samples": [
            {"events_processed": 0, "memory_current_mb": 0.01},
            {"events_processed": 5, "memory_current_mb": 0.10},
            {"events_processed": 20, "memory_current_mb": 0.75},
        ],
    }

    leaking_invariants = evaluate_soak_stability_pass_invariants(leaking)
    bounded_invariants = evaluate_soak_stability_pass_invariants(bounded)

    assert leaking_invariants["memory_growth_within_tolerance"] is False
    assert bounded_invariants["memory_growth_within_tolerance"] is True


def test_recommend_soak_thresholds_marks_degenerate_zero_and_records_basis() -> None:
    from pmfi.qualification.soak_stability import recommend_soak_thresholds

    recommendations = recommend_soak_thresholds(
        {
            "pool_acquire_p95_ms": 0.055,
            "pool_acquire_sample_count": 64,
            "workload_concurrency": 4,
            "pool_size": 32,
            "pool_contention": {"pool_acquire_p95_materially_contended": False},
            "memory_peak_mb": 56.5,
            "memory_growth_mb": 0.0,
            "memory_growth_per_1000_events_mb": 0.0,
            "memory_late_window_growth_per_1000_events_mb": 0.0,
            "throughput_events_per_second": 10.0,
            "dead_letters_created": 0,
            "sample_count": 12,
        }
    )

    recommended = recommendations["recommended"]
    pool = recommended["pool_acquire_wait_p95_alarm_ms"]
    assert pool["recommendation"] == 1
    assert pool["basis"]["measurement_value"] == 0.055
    assert pool["basis"]["workload_concurrency"] == 4
    assert pool["basis"]["contention_state"] == "uncontended"
    assert all("basis" in item for item in recommended.values())

    memory_growth = recommended["memory_growth_alarm_mb"]
    late_growth = recommended["memory_late_window_growth_per_1000_events_alarm_mb"]
    assert memory_growth["recommendation"] is None
    assert memory_growth["reason"] == "degenerate_zero_measurement"
    assert late_growth["recommendation"] is None
    assert late_growth["reason"] == "degenerate_zero_measurement"
    assert any(
        warning["code"] == "uncontended_pool_basis_do_not_apply_over_live_guard"
        for warning in recommendations["warnings"]
    )


def test_soak_stability_text_renders_uncontended_recommendation_warning() -> None:
    from pmfi.commands.soak import render_stability_text
    from pmfi.qualification.soak_stability import recommend_soak_thresholds

    measurements = {
        "events_processed": 100,
        "throughput_events_per_second": 10.0,
        "sample_count": 12,
        "pool_acquire_p95_ms": 0.055,
        "pool_acquire_max_ms": 0.2,
        "pool_acquire_sample_count": 64,
        "workload_concurrency": 4,
        "pool_size": 32,
        "pool_contention": {"pool_acquire_p95_materially_contended": False},
        "memory_start_mb": 50.0,
        "memory_peak_mb": 56.5,
        "memory_growth_mb": 0.0,
        "memory_growth_per_1000_events_mb": 0.0,
        "memory_growth_tolerance_mb": 1.0,
        "memory_trend": {
            "verdict": "warmup_plateau",
            "early_window": {"growth_per_1000_events_mb": 0.1},
            "late_window": {"growth_per_1000_events_mb": 0.0},
            "late_to_early_rate_ratio": 0.0,
        },
        "requested_events": 100,
        "max_duration_seconds": 3600,
        "stop_reason": "max_events",
        "recovery_induced": True,
        "recovery_successful": True,
        "dead_letters_created": 0,
        "dead_letters_per_1000_events": 0.0,
    }
    evidence = {
        "outcome": "PASS",
        "measurements": measurements,
        "recommended_thresholds": recommend_soak_thresholds(measurements),
        "fail_conditions": [],
    }

    text = render_stability_text(evidence)

    assert "pool_acquire_wait_p95_alarm_ms=recommendation=1" in text
    assert "memory_growth_alarm_mb=recommendation=null reason=degenerate_zero_measurement" in text
    assert "WARNING: pool_acquire_wait_p95_alarm_ms basis is uncontended" in text
    assert "do not apply over the live guard" in text


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
        "pool_acquire_sample_count": 20,
        "memory_start_mb": 1.0,
        "memory_peak_mb": 2.0,
        "memory_growth_mb": 0.2,
        "memory_growth_tolerance_mb": 1.0,
        "dead_letters_created": 0,
        "max_allowed_dead_letters": 0,
        "recovery_induced": True,
        "recovery_successful": True,
        "samples": [
            {"events_processed": 0, "memory_current_mb": 0.1},
            {"events_processed": 4, "memory_current_mb": 0.3},
        ],
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
