from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_soak_run_cli_parses_start_status_stop(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()

    start = parser.parse_args(
        [
            "soak-run",
            "start",
            "--run-id",
            "codex-smoke",
            "--run-root",
            str(tmp_path),
            "--duration",
            "2h",
            "--max-events",
            "72000",
            "--events-per-second",
            "10",
            "--sample-interval-seconds",
            "60",
        ]
    )
    status = parser.parse_args(["soak-run", "status", "--run-id", "codex-smoke", "--run-root", str(tmp_path)])
    stop = parser.parse_args(["soak-run", "stop", "--run-id", "codex-smoke", "--run-root", str(tmp_path)])

    assert start.command == "soak-run"
    assert start.soak_run_cmd == "start"
    assert start.duration == "2h"
    assert start.events_per_second == 10.0
    assert status.soak_run_cmd == "status"
    assert stop.soak_run_cmd == "stop"


def test_soak_run_duration_and_pacing_are_bounded() -> None:
    from pmfi.qualification.soak_runner import parse_duration_seconds, pace_interval_seconds

    assert parse_duration_seconds("90s") == 90
    assert parse_duration_seconds("5m") == 300
    assert parse_duration_seconds("2h") == 7200
    assert parse_duration_seconds("1d") == 86400
    assert pace_interval_seconds(10.0) == 0.1
    with pytest.raises(ValueError, match="positive"):
        parse_duration_seconds("0s")
    with pytest.raises(ValueError, match="positive"):
        pace_interval_seconds(0.0)


def test_soak_run_windows_detach_flags_are_explicit() -> None:
    from pmfi.qualification.soak_runner import detached_process_kwargs

    kwargs = detached_process_kwargs(platform_name="win32")

    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] & subprocess.DETACHED_PROCESS
    assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP


def test_soak_run_uses_dedicated_soak_db_name() -> None:
    from pmfi.qualification.soak_runner import dedicated_soak_database_name, ensure_dedicated_soak_database

    name = dedicated_soak_database_name("run 2026/06/23")

    assert name.startswith("pmfi_soak_run_")
    assert len(name) <= 63
    ensure_dedicated_soak_database(name)
    with pytest.raises(ValueError, match="dedicated soak database"):
        ensure_dedicated_soak_database("pmfi")


def test_soak_run_status_reads_pid_liveness_and_latest_sample(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, read_status

    paths = SoakRunPaths.from_root(tmp_path, "run-a")
    paths.run_dir.mkdir(parents=True)
    paths.pid_file.write_text(json.dumps({"pid": 12345}), encoding="utf-8")
    paths.samples_file.write_text(
        "\n".join(
            [
                json.dumps({"events_processed": 10, "rss_mb": 20.0}),
                json.dumps({"events_processed": 20, "rss_mb": 21.0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    status = read_status(paths, pid_is_alive=lambda pid: pid == 12345)

    assert status["alive"] is True
    assert status["pid"] == 12345
    assert status["latest_sample"]["events_processed"] == 20
    assert status["sample_count"] == 2


def test_soak_run_stop_flag_round_trip(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, request_stop, stop_requested

    paths = SoakRunPaths.from_root(tmp_path, "run-b")
    paths.run_dir.mkdir(parents=True)

    assert stop_requested(paths) is False
    request_stop(paths)
    assert stop_requested(paths) is True
    assert "requested_at" in json.loads(paths.stop_file.read_text(encoding="utf-8"))


def test_soak_run_windowed_verdict_catches_plateau_and_leak() -> None:
    from pmfi.qualification.soak_runner import compute_windowed_metric_trend

    plateau_samples = [
        {"events_processed": 100, "rss_mb": 100.0},
        {"events_processed": 200, "rss_mb": 110.0},
        {"events_processed": 300, "rss_mb": 116.0},
        {"events_processed": 800, "rss_mb": 117.0},
        {"events_processed": 900, "rss_mb": 117.1},
        {"events_processed": 1000, "rss_mb": 117.2},
    ]
    leaking_samples = [
        {"events_processed": 100, "db_size_mb": 1.0},
        {"events_processed": 200, "db_size_mb": 2.0},
        {"events_processed": 300, "db_size_mb": 3.0},
        {"events_processed": 800, "db_size_mb": 8.0},
        {"events_processed": 900, "db_size_mb": 9.0},
        {"events_processed": 1000, "db_size_mb": 10.0},
    ]

    plateau = compute_windowed_metric_trend(plateau_samples, value_key="rss_mb", window_sample_count=3)
    leak = compute_windowed_metric_trend(leaking_samples, value_key="db_size_mb", window_sample_count=3)

    assert plateau["verdict"] == "warmup_plateau"
    assert plateau["sustained_growth"] is False
    assert leak["verdict"] == "sustained_linear_growth"
    assert leak["sustained_growth"] is True


def test_soak_run_evidence_is_recommend_only_and_multiday_scoped(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, analyze_soak_run

    paths = SoakRunPaths.from_root(tmp_path, "run-c")
    paths.run_dir.mkdir(parents=True)
    for sample in [
        {"events_processed": 100, "rss_mb": 100.0, "db_size_mb": 1.0, "pool_acquire": {"p95_ms": 0.1, "sample_count": 20}},
        {"events_processed": 200, "rss_mb": 110.0, "db_size_mb": 2.0, "pool_acquire": {"p95_ms": 0.2, "sample_count": 40}},
        {"events_processed": 300, "rss_mb": 116.0, "db_size_mb": 3.0, "pool_acquire": {"p95_ms": 0.3, "sample_count": 60}},
        {"events_processed": 800, "rss_mb": 117.0, "db_size_mb": 4.0, "pool_acquire": {"p95_ms": 0.4, "sample_count": 80}},
        {"events_processed": 900, "rss_mb": 117.1, "db_size_mb": 4.1, "pool_acquire": {"p95_ms": 0.5, "sample_count": 100}},
        {"events_processed": 1000, "rss_mb": 117.2, "db_size_mb": 4.2, "pool_acquire": {"p95_ms": 0.6, "sample_count": 120}},
    ]:
        with paths.samples_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(sample) + "\n")

    evidence = analyze_soak_run(paths, run_config={"duration_seconds": 172800, "max_events": 1728000})

    assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
    assert evidence["recommended_thresholds"]["mode"] == "recommend_only"
    assert evidence["completeness_classifications"]["soak"] == "MEASURED_BOUNDED_LOCAL_SHORT_PROOF"
    assert evidence["completeness_classifications"]["multi_host_reproducibility"] == "ACCEPTED_DEBT"
    assert evidence["measurements"]["rss_trend"]["verdict"] == "warmup_plateau"
    assert evidence["measurements"]["db_size_trend"]["sustained_growth"] is False


def test_soak_run_worker_command_is_file_lifecycle_only(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, build_worker_command

    paths = SoakRunPaths.from_root(tmp_path, "run-d")
    command = build_worker_command(
        python_executable=sys.executable,
        paths=paths,
        run_config={"duration_seconds": 60, "max_events": 600},
    )

    assert command[:3] == [sys.executable, "-m", "pmfi.cli"]
    assert "soak-run" in command
    assert "_worker" in command
    assert "--run-dir" in command
    assert "--db-url-env" in command
    assert "--db-url" not in command
