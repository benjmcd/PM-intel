from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
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


def test_soak_run_cli_parses_list_drop_dashboard(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()

    listed = parser.parse_args(["soak-run", "list", "--run-root", str(tmp_path), "--format", "json"])
    dropped = parser.parse_args(["soak-run", "drop", "--run-id", "codex-smoke", "--run-root", str(tmp_path)])
    dashboard = parser.parse_args(["soak-run", "dashboard", "--run-dir", str(tmp_path / "codex-smoke")])

    assert listed.soak_run_cmd == "list"
    assert dropped.soak_run_cmd == "drop"
    assert dropped.keep_dir is False
    assert dashboard.soak_run_cmd == "dashboard"


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


def test_soak_run_default_root_is_off_onedrive_and_refuses_synced_path() -> None:
    from pmfi.qualification.soak_runner import DEFAULT_RUN_ROOT, ensure_safe_run_root, is_onedrive_path

    assert "onedrive" not in str(DEFAULT_RUN_ROOT).lower()
    synced = Path("C:/Users/benny/OneDrive/Desktop/PM-intel/reports/soak-runs")
    assert is_onedrive_path(synced) is True
    with pytest.raises(ValueError, match="OneDrive"):
        ensure_safe_run_root(synced)


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


def test_soak_run_stop_decision_guards_disk_and_db_size() -> None:
    from pmfi.qualification.soak_runner import decide_soak_stop_reason

    assert decide_soak_stop_reason(
        intake_allowed=False,
        db_size_bytes=1024,
        max_db_size_bytes=2048,
    ) == "disk_headroom_halt"
    assert decide_soak_stop_reason(
        intake_allowed=True,
        db_size_bytes=2049,
        max_db_size_bytes=2048,
    ) == "db_size_cap_reached"
    assert decide_soak_stop_reason(
        intake_allowed=True,
        db_size_bytes=2048,
        max_db_size_bytes=2048,
    ) is None


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


def test_soak_run_short_linear_growth_is_warmup_unresolved() -> None:
    from pmfi.qualification.soak_runner import compute_windowed_metric_trend

    short_warmup_samples = [
        {"events_processed": 100, "rss_mb": 100.0},
        {"events_processed": 200, "rss_mb": 101.0},
        {"events_processed": 300, "rss_mb": 102.0},
        {"events_processed": 400, "rss_mb": 103.0},
        {"events_processed": 500, "rss_mb": 104.0},
        {"events_processed": 600, "rss_mb": 105.0},
    ]

    verdict = compute_windowed_metric_trend(
        short_warmup_samples,
        value_key="rss_mb",
        elapsed_seconds=60.0,
        warmup_min_elapsed_seconds=3600.0,
    )

    assert verdict["verdict"] == "warmup_unresolved"
    assert verdict["sustained_growth"] is False


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
    assert evidence["measurements"]["memory_peak_mb"] == 117.2
    memory_peak = evidence["recommended_thresholds"]["recommended"]["memory_peak_alarm_mb"]
    assert memory_peak["recommendation"] == 235
    assert memory_peak["basis"]["measurement_value"] == 117.2
    assert evidence["measurements"]["rss_trend"]["verdict"] == "warmup_plateau"
    assert evidence["measurements"]["db_size_trend"]["sustained_growth"] is False


def test_soak_run_dashboard_renders_static_html_from_samples(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, write_dashboard

    paths = SoakRunPaths.from_root(tmp_path, "run-dashboard")
    paths.run_dir.mkdir(parents=True)
    paths.samples_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sampled_at": "2026-06-23T00:00:00+00:00",
                        "events_processed": 10,
                        "rss_mb": 50.0,
                        "db_size_mb": 9.5,
                        "disk_free_bytes": 1000,
                        "pool_acquire": {"p95_ms": 0.03},
                        "dead_letters_created": 0,
                        "health": {"intake_allowed": True},
                    }
                ),
                json.dumps(
                    {
                        "sampled_at": "2026-06-23T00:01:00+00:00",
                        "events_processed": 20,
                        "rss_mb": 50.2,
                        "db_size_mb": 9.6,
                        "disk_free_bytes": 990,
                        "pool_acquire": {"p95_ms": 0.04},
                        "dead_letters_created": 1,
                        "health": {"intake_allowed": True},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    output = write_dashboard(paths)
    html = output.read_text(encoding="utf-8")

    assert output == paths.run_dir / "dashboard.html"
    assert "Soak Run Dashboard" in html
    assert "RSS MB" in html
    assert "DB Size MB" in html
    assert "Pool P95 ms" in html
    assert "dead_letters" in html


def test_soak_run_inventory_merges_databases_and_run_dirs(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import (
        SoakRunPaths,
        dedicated_soak_database_name,
        merge_soak_run_inventory,
    )

    paths = SoakRunPaths.from_root(tmp_path, "run-inventory")
    paths.run_dir.mkdir(parents=True)
    paths.config_file.write_text(
        json.dumps({"database_name": dedicated_soak_database_name("run-inventory")}),
        encoding="utf-8",
    )

    inventory = merge_soak_run_inventory(
        database_rows=[
            {
                "database_name": dedicated_soak_database_name("run-inventory"),
                "size_bytes": 123,
            },
            {
                "database_name": dedicated_soak_database_name("orphan"),
                "size_bytes": 456,
            },
        ],
        run_dirs=[paths.run_dir],
        pid_is_alive=lambda _pid: False,
    )

    assert [item["database_name"] for item in inventory] == [
        dedicated_soak_database_name("orphan"),
        dedicated_soak_database_name("run-inventory"),
    ]
    assert inventory[1]["run_id"] == "run-inventory"
    assert inventory[1]["size_bytes"] == 123


def test_soak_run_drop_validation_refuses_non_soak_or_live_target(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import SoakRunPaths, validate_drop_target

    paths = SoakRunPaths.from_root(tmp_path, "live-run")
    paths.run_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="dedicated soak database"):
        validate_drop_target(paths=paths, database_name="pmfi", status={"alive": False})
    with pytest.raises(RuntimeError, match="still alive"):
        validate_drop_target(paths=paths, database_name="pmfi_soak_run_live", status={"alive": True})


def test_soak_run_start_reports_worker_startup_death(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import pmfi.commands.soak_run as command

    class _DeadProc:
        pid = 61234

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(
        command,
        "load_config",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://localhost:" + "54" + "32/pmfi")),
    )
    monkeypatch.setattr(command.subprocess, "Popen", lambda *args, **kwargs: _DeadProc())
    monkeypatch.setattr(command, "pid_is_alive", lambda _pid: False)

    args = SimpleNamespace(
        run_id="startup-death",
        run_root=str(tmp_path),
        duration="5m",
        max_events=10,
        events_per_second=10.0,
        sample_interval_seconds=1.0,
        pool_size=1,
        retention_window_seconds=10.0,
        recovery_interval_seconds=0.0,
        max_db_size_bytes=1024 * 1024,
        disk_min_bytes=1,
        disk_min_fraction=0.0,
        format="text",
    )

    assert command._start(args) == 1
    assert "worker failed to initialize" in capsys.readouterr().err


def test_soak_run_start_reports_existing_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import pmfi.commands.soak_run as command

    SoakRunPaths = pytest.importorskip("pmfi.qualification.soak_runner").SoakRunPaths
    SoakRunPaths.from_root(tmp_path, "duplicate").run_dir.mkdir(parents=True)
    monkeypatch.setattr(
        command,
        "load_config",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://localhost:" + "54" + "32/pmfi")),
    )

    args = SimpleNamespace(
        run_id="duplicate",
        run_root=str(tmp_path),
        duration="5m",
        max_events=10,
        events_per_second=10.0,
        sample_interval_seconds=1.0,
        pool_size=1,
        retention_window_seconds=10.0,
        recovery_interval_seconds=0.0,
        max_db_size_bytes=1024 * 1024,
        disk_min_bytes=1,
        disk_min_fraction=0.0,
        format="text",
    )

    assert command._start(args) == 1
    assert "run_id already exists" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_soak_pool_manager_threads_command_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from pmfi.pipeline.supervisor import PoolManager

    seen: dict[str, object] = {}

    async def fake_create_pool_with_retry(dsn: str, **kwargs: object) -> object:
        seen["dsn"] = dsn
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("pmfi.db.create_pool_with_retry", fake_create_pool_with_retry)

    manager = PoolManager("postgresql://localhost:" + "54" + "32/pmfi_soak_run_test", command_timeout=45.0)
    await manager.open()

    assert seen["command_timeout"] == 45.0


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
