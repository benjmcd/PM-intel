from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path
from typing import Any

from pmfi.commands._shared import ROOT
from pmfi.config import load_config
from pmfi.qualification.soak_runner import (
    DEFAULT_DB_SIZE_CAP_BYTES,
    DEFAULT_DISK_MIN_BYTES,
    DEFAULT_DISK_MIN_FRACTION,
    DEFAULT_DURATION_SECONDS,
    DEFAULT_EVENTS_PER_SECOND,
    DEFAULT_POOL_SIZE,
    DEFAULT_RECOVERY_INTERVAL_SECONDS,
    DEFAULT_RETENTION_WINDOW_SECONDS,
    DEFAULT_RUN_ROOT,
    DEFAULT_SAMPLE_INTERVAL_SECONDS,
    SoakRunPaths,
    analyze_soak_run,
    build_worker_command,
    dedicated_soak_database_name,
    default_run_id,
    detached_process_kwargs,
    parse_duration_seconds,
    read_status,
    request_stop,
    run_soak_worker_sync,
    terminate_pid,
)


def _json_default(value: object) -> str:
    return str(value)


def _run_root(args: Namespace) -> Path:
    return Path(getattr(args, "run_root", None) or DEFAULT_RUN_ROOT)


def _paths_from_args(args: Namespace) -> SoakRunPaths:
    run_dir = getattr(args, "run_dir", None)
    if run_dir:
        return SoakRunPaths.from_run_dir(Path(run_dir))
    run_root = _run_root(args)
    run_id = getattr(args, "run_id", None)
    if not run_id:
        candidates = [path for path in run_root.glob("*") if path.is_dir()] if run_root.exists() else []
        if not candidates:
            raise ValueError("no soak-run run_id was supplied and no runs exist under the run root")
        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        run_id = latest.name
    return SoakRunPaths.from_root(run_root, str(run_id))


def _render_status(status: dict[str, Any]) -> str:
    latest = status.get("latest_sample") or {}
    pool = latest.get("pool_acquire") or {}
    return "\n".join(
        [
            f"run_id={status['run_id']}",
            f"alive={status['alive']} pid={status.get('pid')} phase={status.get('phase')}",
            f"stop_requested={status['stop_requested']} samples={status['sample_count']}",
            (
                "latest: "
                f"events={latest.get('events_processed')} "
                f"elapsed_s={latest.get('elapsed_seconds')} "
                f"rss_mb={latest.get('rss_mb')} "
                f"db_size_mb={latest.get('db_size_mb')} "
                f"disk_free_bytes={latest.get('disk_free_bytes')} "
                f"pool_p95_ms={pool.get('p95_ms')} "
                f"dead_letters={latest.get('dead_letters_created')}"
            ),
            f"run_dir={status['run_dir']}",
        ]
    )


def _print_payload(payload: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_default))
    else:
        print(_render_status(payload) if "alive" in payload else json.dumps(payload, indent=2, default=_json_default))


def _start(args: Namespace) -> int:
    cfg = load_config()
    run_id = getattr(args, "run_id", None) or default_run_id()
    paths = SoakRunPaths.from_root(_run_root(args), run_id)
    paths.run_dir.mkdir(parents=True, exist_ok=False)
    duration_seconds = parse_duration_seconds(getattr(args, "duration", None) or DEFAULT_DURATION_SECONDS)
    events_per_second = float(getattr(args, "events_per_second", None) or DEFAULT_EVENTS_PER_SECOND)
    max_events = int(getattr(args, "max_events", None) or round(duration_seconds * events_per_second))
    run_config = {
        "duration_seconds": duration_seconds,
        "max_events": max_events,
        "events_per_second": events_per_second,
        "sample_interval_seconds": float(
            getattr(args, "sample_interval_seconds", None) or DEFAULT_SAMPLE_INTERVAL_SECONDS
        ),
        "pool_size": int(getattr(args, "pool_size", None) or DEFAULT_POOL_SIZE),
        "retention_window_seconds": float(
            getattr(args, "retention_window_seconds", None) or DEFAULT_RETENTION_WINDOW_SECONDS
        ),
        "recovery_interval_seconds": float(
            getattr(args, "recovery_interval_seconds", None) or DEFAULT_RECOVERY_INTERVAL_SECONDS
        ),
        "max_db_size_bytes": int(getattr(args, "max_db_size_bytes", None) or DEFAULT_DB_SIZE_CAP_BYTES),
        "disk_min_bytes": int(getattr(args, "disk_min_bytes", None) or DEFAULT_DISK_MIN_BYTES),
        "disk_min_fraction": float(getattr(args, "disk_min_fraction", None) or DEFAULT_DISK_MIN_FRACTION),
        "database_name": dedicated_soak_database_name(run_id),
    }
    paths.config_file.write_text(
        json.dumps(run_config, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    command = build_worker_command(
        python_executable=sys.executable,
        paths=paths,
        run_config=run_config,
    )
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    env["PMFI_SOAK_RUN_DB_URL"] = cfg.database.url
    stdout = paths.stdout_file.open("ab")
    stderr = paths.stderr_file.open("ab")
    try:
        proc = subprocess.Popen(  # noqa: S603
            command,
            cwd=ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            **detached_process_kwargs(),
        )
    finally:
        stdout.close()
        stderr.close()
    paths.pid_file.write_text(
        json.dumps({"pid": proc.pid, "run_id": run_id, "started_by": "pmfi soak-run start"}),
        encoding="utf-8",
    )
    payload = {
        "run_id": run_id,
        "run_dir": str(paths.run_dir),
        "pid": proc.pid,
        "database_name": run_config["database_name"],
        "detached": True,
        "duration_seconds": duration_seconds,
        "max_events": max_events,
        "events_per_second": events_per_second,
    }
    _print_payload(payload, getattr(args, "format", "text"))
    return 0


def _status(args: Namespace) -> int:
    try:
        paths = _paths_from_args(args)
    except ValueError as exc:
        print(f"[soak-run] {exc}", file=sys.stderr)
        return 1
    payload = read_status(paths)
    _print_payload(payload, getattr(args, "format", "text"))
    return 0


def _stop(args: Namespace) -> int:
    try:
        paths = _paths_from_args(args)
    except ValueError as exc:
        print(f"[soak-run] {exc}", file=sys.stderr)
        return 1
    request_stop(paths)
    wait_seconds = max(0.0, float(getattr(args, "wait_seconds", 30.0)))
    deadline = time.time() + wait_seconds
    payload = read_status(paths)
    while payload.get("alive") and time.time() < deadline:
        time.sleep(0.5)
        payload = read_status(paths)
    if payload.get("alive") and bool(getattr(args, "force_kill", False)):
        pid = int(payload.get("pid") or 0)
        terminate_pid(pid)
        time.sleep(0.5)
        payload = read_status(paths)
    _print_payload(payload, getattr(args, "format", "text"))
    return 0 if not payload.get("alive") else 2


def _analyze(args: Namespace) -> int:
    try:
        paths = _paths_from_args(args)
    except ValueError as exc:
        print(f"[soak-run] {exc}", file=sys.stderr)
        return 1
    status = read_status(paths)
    evidence = analyze_soak_run(paths, crashed=not bool(status.get("alive")) and not paths.final_evidence_file.exists())
    if getattr(args, "write", False):
        paths.final_evidence_file.write_text(
            json.dumps(evidence, indent=2, sort_keys=True, default=_json_default),
            encoding="utf-8",
        )
    _print_payload(evidence, getattr(args, "format", "json"))
    return 0 if evidence["outcome"] == "PASS" else 1


def _worker(args: Namespace) -> int:
    try:
        paths = SoakRunPaths.from_run_dir(Path(args.run_dir))
        run_config = json.loads(args.run_config_json)
        db_url = getattr(args, "db_url", None) or os.environ.get(args.db_url_env)
        if not db_url:
            raise RuntimeError(f"{args.db_url_env} is required for soak-run worker")
        evidence = run_soak_worker_sync(paths=paths, base_db_url=db_url, run_config=run_config)
        return 0 if evidence["outcome"] == "PASS" else 1
    except Exception as exc:
        run_dir = Path(getattr(args, "run_dir", "."))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "phase": "failed",
                    "pid": os.getpid(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"[soak-run] worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def cmd_soak_run(args: Namespace) -> int:
    subcommand = getattr(args, "soak_run_cmd", None)
    if subcommand == "start":
        return _start(args)
    if subcommand == "status":
        return _status(args)
    if subcommand == "stop":
        return _stop(args)
    if subcommand == "analyze":
        return _analyze(args)
    if subcommand == "_worker":
        return _worker(args)
    print("Usage: pmfi soak-run {start|status|stop|analyze}")
    return 1
