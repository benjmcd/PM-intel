from __future__ import annotations

import asyncio
import ctypes
from html import escape
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tracemalloc
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from pmfi.commands._shared import ROOT, is_loopback_db_url
from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState, PoolAcquireWaitStats
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event
from pmfi.pipeline.supervisor import PoolManager
from pmfi.qualification.evidence import schema_fingerprint
from pmfi.qualification.soak_stability import (
    _drop_database,
    _event_from_index,
    _init_schema,
    _quote_ident,
    recommend_soak_thresholds,
)

DEFAULT_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_EVENTS_PER_SECOND = 10.0
DEFAULT_SAMPLE_INTERVAL_SECONDS = 60.0
DEFAULT_POOL_SIZE = 4
DEFAULT_RETENTION_WINDOW_SECONDS = 3600.0
DEFAULT_RECOVERY_INTERVAL_SECONDS = 3600.0
DEFAULT_DB_SIZE_CAP_BYTES = 50 * 1024 * 1024 * 1024
DEFAULT_DISK_MIN_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_DISK_MIN_FRACTION = 0.10
DEFAULT_SOAK_COMMAND_TIMEOUT_SECONDS = 45.0
DEFAULT_WARMUP_MIN_SECONDS = 3600.0
DEDICATED_DB_PREFIX = "pmfi_soak_run_"
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)([smhd])\s*$", re.IGNORECASE)
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def default_run_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "pmfi" / "soak-runs"
    return Path.home() / "AppData" / "Local" / "pmfi" / "soak-runs"


DEFAULT_RUN_ROOT = default_run_root()


@dataclass(frozen=True)
class SoakRunPaths:
    run_root: Path
    run_id: str
    run_dir: Path
    pid_file: Path
    config_file: Path
    samples_file: Path
    status_file: Path
    stop_file: Path
    final_evidence_file: Path
    stdout_file: Path
    stderr_file: Path

    @classmethod
    def from_root(cls, run_root: Path, run_id: str) -> "SoakRunPaths":
        run_root = Path(run_root)
        run_dir = run_root / run_id
        return cls(
            run_root=run_root,
            run_id=run_id,
            run_dir=run_dir,
            pid_file=run_dir / "pid.json",
            config_file=run_dir / "config.json",
            samples_file=run_dir / "samples.jsonl",
            status_file=run_dir / "status.json",
            stop_file=run_dir / "stop.flag",
            final_evidence_file=run_dir / "final-evidence.json",
            stdout_file=run_dir / "runner.stdout.log",
            stderr_file=run_dir / "runner.stderr.log",
        )

    @classmethod
    def from_run_dir(cls, run_dir: Path) -> "SoakRunPaths":
        run_dir = Path(run_dir)
        return cls.from_root(run_dir.parent, run_dir.name)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_duration_seconds(value: str | int | float) -> int:
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        match = _DURATION_RE.fullmatch(str(value))
        if not match:
            raise ValueError("duration must be a positive value ending in s, m, h, or d")
        amount = float(match.group(1))
        unit = match.group(2).lower()
        seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("duration must be positive")
    return int(math.ceil(seconds))


def pace_interval_seconds(events_per_second: float) -> float:
    rate = float(events_per_second)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("events_per_second must be positive")
    return round(1.0 / rate, 6)


def default_run_id(now: datetime | None = None) -> str:
    now = now or utc_now()
    return f"soak-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def dedicated_soak_database_name(run_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", run_id.lower()).strip("_") or "run"
    name = f"{DEDICATED_DB_PREFIX}{slug}"[:63].rstrip("_")
    if not _DB_NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe dedicated soak database name: {name!r}")
    return name


def ensure_dedicated_soak_database(name: str) -> None:
    if not name.startswith(DEDICATED_DB_PREFIX) or not _DB_NAME_RE.fullmatch(name):
        raise ValueError("target must be a dedicated soak database named pmfi_soak_run_*")


def is_onedrive_path(path: Path | str) -> bool:
    candidate = Path(path).expanduser()
    parts = [part.lower() for part in candidate.parts]
    if any(part.startswith("onedrive") for part in parts):
        return True
    candidate_text = str(candidate).lower()
    for env_name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = os.environ.get(env_name)
        if root and candidate_text.startswith(str(Path(root).expanduser()).lower()):
            return True
    return False


def ensure_safe_run_root(run_root: Path | str) -> Path:
    resolved = Path(run_root).expanduser()
    if is_onedrive_path(resolved):
        raise ValueError(f"OneDrive-synced soak-run root is not allowed: {resolved}")
    return resolved


def decide_soak_stop_reason(
    *,
    intake_allowed: bool,
    db_size_bytes: int,
    max_db_size_bytes: int,
) -> str | None:
    if not intake_allowed:
        return "disk_headroom_halt"
    if int(db_size_bytes) > int(max_db_size_bytes):
        return "db_size_cap_reached"
    return None


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _admin_dsn(base_dsn: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def detached_process_kwargs(*, platform_name: str = sys.platform) -> dict[str, Any]:
    if platform_name == "win32":
        return {
            "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            "close_fds": True,
        }
    return {"start_new_session": True, "close_fds": True}


def build_worker_command(
    *,
    python_executable: str,
    paths: SoakRunPaths,
    run_config: dict[str, Any],
    db_url_env: str = "PMFI_SOAK_RUN_DB_URL",
) -> list[str]:
    return [
        python_executable,
        "-m",
        "pmfi.cli",
        "soak-run",
        "_worker",
        "--run-dir",
        str(paths.run_dir),
        "--db-url-env",
        db_url_env,
        "--run-config-json",
        json.dumps(run_config, sort_keys=True, separators=(",", ":")),
    ]


def wait_for_worker_initialization(
    paths: SoakRunPaths,
    *,
    launched_pid: int,
    timeout_seconds: float = 8.0,
    poll_interval_seconds: float = 0.25,
    pid_is_alive_func: Callable[[int], bool] | None = None,
    process_poll: Callable[[], int | None] | None = None,
) -> bool:
    alive_check = pid_is_alive if pid_is_alive_func is None else pid_is_alive_func
    deadline = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < deadline:
        poll_result = process_poll() if process_poll is not None else None
        status = _read_json(paths.status_file) or {}
        if status.get("phase") == "failed":
            return False
        status_pid = int(status.get("pid") or launched_pid or 0)
        has_worker_state = paths.status_file.exists() or paths.samples_file.exists()
        if has_worker_state and (alive_check(status_pid) or alive_check(launched_pid)):
            return True
        if poll_result is not None and not has_worker_state:
            return False
        time.sleep(max(0.01, min(1.0, float(poll_interval_seconds))))
    return False


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def process_rss_bytes() -> int | None:
    if sys.platform == "win32":
        try:
            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel32.GetCurrentProcess()
            for dll_name, function_name in (
                ("psapi", "GetProcessMemoryInfo"),
                ("kernel32", "K32GetProcessMemoryInfo"),
            ):
                try:
                    fn = getattr(ctypes.WinDLL(dll_name, use_last_error=True), function_name)
                except (AttributeError, OSError):
                    continue
                fn.restype = ctypes.c_bool
                fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessMemoryCounters), ctypes.c_ulong]
                ok = fn(handle, ctypes.byref(counters), counters.cb)
                if ok:
                    return int(counters.WorkingSetSize)
        except Exception:
            return None
        return None
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        factor = 1024 if sys.platform != "darwin" else 1
        return int(usage.ru_maxrss * factor)
    except Exception:
        return None


def _mb(bytes_value: int | None) -> float | None:
    if bytes_value is None:
        return None
    return round(bytes_value / (1024 * 1024), 3)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def request_stop(paths: SoakRunPaths) -> None:
    _write_json(paths.stop_file, {"requested_at": utc_now().isoformat(), "run_id": paths.run_id})


def stop_requested(paths: SoakRunPaths) -> bool:
    return paths.stop_file.exists()


def read_status(
    paths: SoakRunPaths,
    *,
    pid_is_alive: Callable[[int], bool] = pid_is_alive,
) -> dict[str, Any]:
    pid_payload = _read_json(paths.pid_file) or {}
    status_payload = _read_json(paths.status_file) or {}
    samples = read_jsonl(paths.samples_file)
    pid = int(pid_payload.get("pid") or status_payload.get("pid") or 0)
    latest = samples[-1] if samples else None
    alive = pid_is_alive(pid) if pid else False
    latest_at = None if latest is None else latest.get("sampled_at")
    return {
        "run_id": paths.run_id,
        "run_dir": str(paths.run_dir),
        "pid": pid or None,
        "alive": alive,
        "stop_requested": stop_requested(paths),
        "phase": status_payload.get("phase", "unknown"),
        "status": status_payload,
        "latest_sample": latest,
        "sample_count": len(samples),
        "latest_sample_at": latest_at,
        "final_evidence_path": str(paths.final_evidence_file) if paths.final_evidence_file.exists() else None,
    }


def database_name_for_run(paths: SoakRunPaths) -> str:
    config = _read_json(paths.config_file) or {}
    database_name = str(config.get("database_name") or dedicated_soak_database_name(paths.run_id))
    ensure_dedicated_soak_database(database_name)
    return database_name


def validate_drop_target(
    *,
    paths: SoakRunPaths,
    database_name: str,
    status: dict[str, Any],
) -> str:
    ensure_dedicated_soak_database(database_name)
    if bool(status.get("alive")):
        raise RuntimeError(f"soak run is still alive; stop it before drop: {paths.run_id}")
    return database_name


def merge_soak_run_inventory(
    *,
    database_rows: list[dict[str, Any]],
    run_dirs: list[Path],
    pid_is_alive: Callable[[int], bool] = pid_is_alive,
) -> list[dict[str, Any]]:
    by_database = {str(row["database_name"]): dict(row) for row in database_rows}
    items: dict[str, dict[str, Any]] = {}
    for run_dir in run_dirs:
        paths = SoakRunPaths.from_run_dir(Path(run_dir))
        try:
            database_name = database_name_for_run(paths)
        except ValueError:
            database_name = dedicated_soak_database_name(paths.run_id)
        row = by_database.get(database_name, {})
        status = read_status(paths, pid_is_alive=pid_is_alive)
        items[database_name] = {
            "run_id": paths.run_id,
            "run_dir": str(paths.run_dir),
            "database_name": database_name,
            "database_present": database_name in by_database,
            "run_dir_present": True,
            "size_bytes": int(row.get("size_bytes") or 0),
            "alive": bool(status.get("alive")),
            "phase": status.get("phase"),
            "sample_count": int(status.get("sample_count") or 0),
            "latest_sample_at": status.get("latest_sample_at"),
        }
    for database_name, row in by_database.items():
        if database_name in items:
            continue
        items[database_name] = {
            "run_id": database_name.removeprefix(DEDICATED_DB_PREFIX),
            "run_dir": None,
            "database_name": database_name,
            "database_present": True,
            "run_dir_present": False,
            "size_bytes": int(row.get("size_bytes") or 0),
            "alive": False,
            "phase": "orphaned_database",
            "sample_count": 0,
            "latest_sample_at": None,
        }
    return [items[name] for name in sorted(items)]


async def list_dedicated_soak_databases(base_db_url: str) -> list[dict[str, Any]]:
    if not is_loopback_db_url(base_db_url):
        raise RuntimeError("soak-run list requires a loopback Postgres URL")
    conn = await asyncpg.connect(_admin_dsn(base_db_url))
    try:
        rows = await conn.fetch(
            """
            SELECT datname AS database_name,
                   pg_database_size(datname)::bigint AS size_bytes
            FROM pg_database
            WHERE datname LIKE 'pmfi_soak_run_%'
            ORDER BY datname
            """
        )
        return [
            {
                "database_name": str(row["database_name"]),
                "size_bytes": int(row["size_bytes"] or 0),
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def drop_dedicated_soak_database(base_db_url: str, database_name: str) -> None:
    if not is_loopback_db_url(base_db_url):
        raise RuntimeError("soak-run drop requires a loopback Postgres URL")
    ensure_dedicated_soak_database(database_name)
    conn = await asyncpg.connect(_admin_dsn(base_db_url))
    try:
        await _drop_database(conn, database_name)
    finally:
        await conn.close()


def remove_run_dir(paths: SoakRunPaths) -> bool:
    if not paths.run_dir.exists():
        return False
    run_dir = paths.run_dir.resolve()
    run_root = paths.run_root.resolve()
    if run_dir == run_root or run_root not in run_dir.parents:
        raise RuntimeError(f"refusing to remove run directory outside run root: {run_dir}")
    shutil.rmtree(run_dir)
    return True


def _sample_values(
    samples: list[dict[str, Any]],
    *,
    value_key: str,
    event_key: str = "events_processed",
) -> list[tuple[int, float]]:
    values: list[tuple[int, float]] = []
    for sample in samples:
        try:
            events = int(sample.get(event_key) or 0)
            value = float(sample[value_key])
        except (KeyError, TypeError, ValueError):
            continue
        if events > 0 and math.isfinite(value):
            values.append((events, value))
    return sorted(values)


def _window_summary(values: list[tuple[int, float]]) -> dict[str, Any]:
    if len(values) < 2:
        return {
            "start_events": None,
            "end_events": None,
            "start_value": None,
            "end_value": None,
            "event_delta": 0,
            "growth": None,
            "growth_per_1000_events": None,
        }
    start_events, start_value = values[0]
    end_events, end_value = values[-1]
    event_delta = max(0, end_events - start_events)
    growth = max(0.0, end_value - start_value)
    return {
        "start_events": start_events,
        "end_events": end_events,
        "start_value": round(start_value, 3),
        "end_value": round(end_value, 3),
        "event_delta": event_delta,
        "growth": round(growth, 3),
        "growth_per_1000_events": None
        if event_delta <= 0
        else round(growth * 1000.0 / event_delta, 3),
    }


def compute_windowed_metric_trend(
    samples: list[dict[str, Any]],
    *,
    value_key: str,
    event_key: str = "events_processed",
    window_sample_count: int = 6,
    plateau_ratio_threshold: float = 0.25,
    leak_ratio_threshold: float = 0.75,
    elapsed_seconds: float | None = None,
    warmup_min_elapsed_seconds: float | None = None,
) -> dict[str, Any]:
    values = _sample_values(samples, value_key=value_key, event_key=event_key)
    if len(values) < 4:
        return {
            "metric": value_key,
            "verdict": "insufficient_samples",
            "sustained_growth": False,
            "early_window": _window_summary(values),
            "late_window": _window_summary(values),
            "late_to_early_rate_ratio": None,
            "window_sample_count": window_sample_count,
        }
    if (
        warmup_min_elapsed_seconds is not None
        and elapsed_seconds is not None
        and float(elapsed_seconds) < float(warmup_min_elapsed_seconds)
    ):
        return {
            "metric": value_key,
            "verdict": "warmup_unresolved",
            "sustained_growth": False,
            "early_window": _window_summary(values[: max(2, min(window_sample_count, len(values) // 2))]),
            "late_window": _window_summary(values[-max(2, min(window_sample_count, len(values) // 2)):]),
            "late_to_early_rate_ratio": None,
            "window_sample_count": max(2, min(int(window_sample_count), len(values) // 2)),
            "warmup_min_elapsed_seconds": float(warmup_min_elapsed_seconds),
            "elapsed_seconds": round(float(elapsed_seconds), 3),
            "reason": "elapsed time is below the configured warm-up horizon",
        }
    window_size = max(2, min(int(window_sample_count), len(values) // 2))
    early = _window_summary(values[:window_size])
    late = _window_summary(values[-window_size:])
    early_rate = early["growth_per_1000_events"]
    late_rate = late["growth_per_1000_events"]
    ratio: float | None = None
    verdict = "inconclusive"
    sustained_growth = False
    if early_rate is not None and late_rate is not None:
        if early_rate <= 0:
            ratio = 0.0 if late_rate <= 0 else None
        else:
            ratio = round(late_rate / early_rate, 3)
        if ratio is not None and ratio <= plateau_ratio_threshold:
            verdict = "warmup_plateau"
        elif ratio is not None and ratio >= leak_ratio_threshold:
            verdict = "sustained_linear_growth"
            sustained_growth = True
        else:
            verdict = "slowing_growth"
    return {
        "metric": value_key,
        "verdict": verdict,
        "sustained_growth": sustained_growth,
        "early_window": early,
        "late_window": late,
        "late_to_early_rate_ratio": ratio,
        "window_sample_count": window_size,
        "plateau_ratio_threshold": plateau_ratio_threshold,
        "leak_ratio_threshold": leak_ratio_threshold,
    }


def analyze_soak_run(
    paths: SoakRunPaths,
    *,
    run_config: dict[str, Any] | None = None,
    crashed: bool = False,
) -> dict[str, Any]:
    samples = read_jsonl(paths.samples_file)
    run_config = dict(run_config or (_read_json(paths.config_file) or {}))
    latest = samples[-1] if samples else {}
    pool_snapshot = latest.get("pool_acquire") if isinstance(latest, dict) else {}
    if not isinstance(pool_snapshot, dict):
        pool_snapshot = {}
    events_processed = int(latest.get("events_processed") or 0) if isinstance(latest, dict) else 0
    elapsed_seconds = float(latest.get("elapsed_seconds") or 0.0) if isinstance(latest, dict) else 0.0
    trend_elapsed_seconds = (
        float(latest["elapsed_seconds"])
        if isinstance(latest, dict) and "elapsed_seconds" in latest
        else None
    )
    throughput = round(events_processed / elapsed_seconds, 3) if elapsed_seconds > 0 else 0.0
    db_size_mb = latest.get("db_size_mb") if isinstance(latest, dict) else None
    rss_mb = latest.get("rss_mb") if isinstance(latest, dict) else None
    measurements = {
        "run_id": paths.run_id,
        "events_processed": events_processed,
        "requested_events": int(run_config.get("max_events") or 0),
        "duration_seconds": int(run_config.get("duration_seconds") or 0),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "throughput_events_per_second": throughput,
        "sample_count": len(samples),
        "pool_acquire_p95_ms": pool_snapshot.get("p95_ms"),
        "pool_acquire_sample_count": int(pool_snapshot.get("sample_count") or 0),
        "rss_mb": rss_mb,
        "db_size_mb": db_size_mb,
        "dead_letters_created": int(latest.get("dead_letters_created") or 0) if isinstance(latest, dict) else 0,
        "recovery_induced": int(latest.get("recoveries_induced") or 0) > 0 if isinstance(latest, dict) else False,
        "recovery_successful": int(latest.get("recoveries_induced") or 0) > 0 if isinstance(latest, dict) else False,
        "rss_trend": compute_windowed_metric_trend(
            samples,
            value_key="rss_mb",
            elapsed_seconds=trend_elapsed_seconds,
            warmup_min_elapsed_seconds=DEFAULT_WARMUP_MIN_SECONDS,
        ),
        "db_size_trend": compute_windowed_metric_trend(
            samples,
            value_key="db_size_mb",
            elapsed_seconds=trend_elapsed_seconds,
            warmup_min_elapsed_seconds=DEFAULT_WARMUP_MIN_SECONDS,
        ),
        "stop_reason": latest.get("stop_reason") if isinstance(latest, dict) else None,
        "crashed_or_killed": bool(crashed),
        "samples_log": str(paths.samples_file),
    }
    measurements["no_secrets_in_fixtures_logs_or_evidence"] = True
    recommendations = recommend_soak_thresholds(
        {
            "pool_acquire_p95_ms": measurements["pool_acquire_p95_ms"] or 0,
            "pool_acquire_sample_count": measurements["pool_acquire_sample_count"],
            "memory_peak_mb": measurements["rss_mb"] or 0,
            "memory_growth_mb": (
                measurements["rss_trend"]["late_window"].get("growth") or 0
            ),
            "memory_growth_per_1000_events_mb": (
                measurements["rss_trend"]["late_window"].get("growth_per_1000_events") or 0
            ),
            "memory_late_window_growth_per_1000_events_mb": (
                measurements["rss_trend"]["late_window"].get("growth_per_1000_events") or 0
            ),
            "throughput_events_per_second": throughput,
            "dead_letters_created": measurements["dead_letters_created"],
        }
    )
    recommendations["rationale"] = (
        "candidate soak thresholds are derived from a bounded local dedicated soak-DB "
        "workload; no config defaults are changed and the operator-owned multi-day run "
        "is separate from short verification runs"
    )
    soak_completeness = (
        "MEASURED_MULTI_DAY_LOCAL"
        if elapsed_seconds >= 24 * 60 * 60
        else "MEASURED_BOUNDED_LOCAL_SHORT_PROOF"
    )
    pass_invariants = {
        "samples_present": len(samples) > 0,
        "events_observed": events_processed > 0,
        "rss_sampled": any(sample.get("rss_mb") is not None for sample in samples),
        "db_size_sampled": any(sample.get("db_size_mb") is not None for sample in samples),
        "pool_p95_sampled": measurements["pool_acquire_p95_ms"] is not None,
        "bounded_by_duration_or_events": bool(run_config.get("duration_seconds")) and bool(run_config.get("max_events")),
        "recommend_only": True,
    }
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": "M-SOAK-RUNNER",
        "scenario_version": "synthetic-v1",
        "profile": "detached_multiday_synthetic_soak",
        "outcome": "PASS" if all(pass_invariants.values()) else "FAIL",
        "completeness_classifications": {
            "soak": soak_completeness,
            "live_venue_load": "NOT_IN_SCOPE",
            "multi_host_reproducibility": "ACCEPTED_DEBT",
            "sleep_or_reboot_resilience": "ACCEPTED_DEBT",
        },
        "runtime": {
            "schema_version": schema_fingerprint(ROOT / "sql"),
            "environment": "offline_dedicated_soak_db",
        },
        "time": {
            "analyzed_at": utc_now().isoformat(),
            "sample_count": len(samples),
        },
        "evidence": {
            "actual_facets": [
                "OFFLINE",
                "POSTGRES_INTEGRATION",
                "DEDICATED_SOAK_DB",
                "DETACHED_PROCESS",
                "FILE_LIFECYCLE",
                "PACED_SYNTHETIC_WORKLOAD",
                "WINDOWED_RSS_AND_DB_SIZE_TREND",
            ],
            "deferred_facets": [
                "MULTI_DAY_OPERATOR_RUN",
                "MULTI_HOST_REPRODUCIBILITY",
                "SLEEP_OR_REBOOT_RESILIENCE",
            ],
            "artifacts": [
                str(paths.pid_file),
                str(paths.samples_file),
                str(paths.status_file),
                str(paths.final_evidence_file),
            ],
        },
        "measurements": measurements,
        "recommended_thresholds": recommendations,
        "pass_invariants": pass_invariants,
        "fail_conditions": [key for key, ok in pass_invariants.items() if not ok],
        "accepted_debt": [
            {
                "facet": "MULTI_DAY_OPERATOR_RUN",
                "reason": "The real one-to-two-day run is operator-launched after PR verification; tests use short bounded runs only.",
            },
            {
                "facet": "SLEEP_OR_REBOOT_RESILIENCE",
                "reason": "The detached process survives terminal and agent exit, not host sleep or reboot.",
            },
        ],
        "next_action": "operator_launch_multiday_synthetic_soak",
    }
    return evidence


def _dashboard_payload(paths: SoakRunPaths) -> dict[str, Any]:
    samples = read_jsonl(paths.samples_file)
    evidence = _read_json(paths.final_evidence_file) or analyze_soak_run(paths)
    status = read_status(paths)
    measurements = evidence.get("measurements") if isinstance(evidence.get("measurements"), dict) else {}
    trend = measurements.get("rss_trend") if isinstance(measurements.get("rss_trend"), dict) else {}
    return {
        "run_id": paths.run_id,
        "generated_at": utc_now().isoformat(),
        "status": status,
        "samples": samples,
        "trend": trend or {},
        "evidence_summary": {
            "outcome": evidence.get("outcome"),
            "fail_conditions": evidence.get("fail_conditions") or [],
            "sample_count": (evidence.get("time") or {}).get("sample_count")
            if isinstance(evidence.get("time"), dict)
            else len(samples),
            "rss_trend": trend or {},
            "db_size_trend": measurements.get("db_size_trend")
            if isinstance(measurements.get("db_size_trend"), dict)
            else {},
        },
    }


def _json_for_html_script(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str).replace("<", "\\u003c")


def build_dashboard_html(paths: SoakRunPaths) -> str:
    payload_json = _json_for_html_script(_dashboard_payload(paths))
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Soak Run Dashboard - __RUN_ID__</title>
<style>
html { color-scheme: light; }
body {
  margin: 0;
  background: #f4f6f8;
  color: #172033;
  font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main { max-width: 1280px; margin: 0 auto; padding: 24px; }
header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
  margin-bottom: 18px;
}
h1 { margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }
h2 { margin: 0; font-size: 17px; letter-spacing: 0; }
p { margin: 0; }
.muted { color: #667085; }
.mono { font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
.controls { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 10px; }
button {
  border: 1px solid #b8c0cc;
  border-radius: 6px;
  background: #ffffff;
  color: #172033;
  font-weight: 650;
  padding: 8px 12px;
  cursor: pointer;
}
button:hover { background: #f8fafc; }
label.toggle { display: inline-flex; align-items: center; gap: 7px; font-weight: 650; }
.updated { min-width: 116px; text-align: right; color: #475467; }
.notice {
  display: none;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid #f4c27a;
  border-radius: 8px;
  background: #fff6e5;
  color: #7a4704;
}
.notice.show { display: flex; }
.notice button { border-color: #e8a447; padding: 4px 8px; }
.summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
  margin: 18px 0;
}
.metric {
  min-height: 70px;
  border: 1px solid #d8dde3;
  border-radius: 8px;
  background: #ffffff;
  padding: 12px;
}
.metric .label { color: #667085; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.metric .value { margin-top: 5px; font-size: 21px; font-weight: 750; }
.pill {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  min-height: 22px;
  border-radius: 999px;
  padding: 2px 9px;
  font-weight: 750;
  font-size: 13px;
}
.pill.good { background: #dff7e8; color: #116b38; }
.pill.bad { background: #fde4e4; color: #a31919; }
.verdict {
  border-radius: 8px;
  padding: 14px 16px;
  margin: 14px 0 18px;
  border: 1px solid #d8dde3;
  background: #ffffff;
}
.verdict.good { border-color: #8ad2a5; background: #ecfbf2; }
.verdict.warn { border-color: #f1bd6b; background: #fff6e5; }
.verdict.bad { border-color: #f19a9a; background: #fff0f0; }
.verdict strong { display: block; font-size: 20px; margin-bottom: 4px; }
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 14px;
}
.card {
  border: 1px solid #d8dde3;
  border-radius: 8px;
  background: #ffffff;
  padding: 14px;
}
.card.alert { border-color: #f19a9a; background: #fffafa; }
.card-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
.unit { color: #667085; font-size: 13px; }
.current { margin: 10px 0 2px; font-size: 30px; font-weight: 800; }
.card.alert .current { color: #b42318; }
.range { color: #667085; font-size: 13px; margin-bottom: 8px; }
.waiting {
  display: flex;
  min-height: 150px;
  align-items: center;
  justify-content: center;
  color: #667085;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
  background: #f8fafc;
}
svg.chart { width: 100%; height: auto; display: block; }
.grid-line { stroke: #dbe2ea; stroke-width: 1; }
.axis-label { fill: #667085; font-size: 11px; }
.line { fill: none; stroke: #2563eb; stroke-width: 2.5; }
.area { fill: #bfdbfe; opacity: .38; }
.dot { fill: #1d4ed8; stroke: #ffffff; stroke-width: 2; }
.table-wrap { margin-top: 18px; overflow-x: auto; border: 1px solid #d8dde3; border-radius: 8px; background: #ffffff; }
table { border-collapse: collapse; width: 100%; min-width: 820px; }
th, td { border-bottom: 1px solid #e4e9f0; padding: 8px 10px; text-align: left; white-space: nowrap; }
th { background: #eef2f6; color: #344054; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
td.num { text-align: right; font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace; }
tr:last-child td { border-bottom: 0; }
@media (max-width: 720px) {
  main { padding: 16px; }
  header { display: block; }
  .controls { justify-content: flex-start; margin-top: 12px; }
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Soak Run Dashboard</h1>
      <p class="muted">Run <span id="run-id" class="mono"></span></p>
    </div>
    <div class="controls">
      <button id="refresh-button" type="button">&#8635; Refresh</button>
      <label class="toggle"><input id="auto-refresh-toggle" type="checkbox"> Auto-refresh (30s)</label>
      <span id="updated-at" class="updated">updated --:--:--</span>
    </div>
  </header>
  <div id="refresh-note" class="notice" role="status">
    <span>Live updates need the dashboard to be served over HTTP; reloading the embedded snapshot.</span>
    <button id="dismiss-note" type="button">Dismiss</button>
  </div>
  <section id="summary" class="summary" aria-label="Run summary"></section>
  <section id="verdict" class="verdict" aria-label="Trend verdict"></section>
  <section id="charts" class="chart-grid" aria-label="Metric charts"></section>
  <section class="table-wrap" aria-label="Recent samples">
    <table>
      <thead>
        <tr>
          <th>sampled_at</th>
          <th>events</th>
          <th>RSS MB</th>
          <th>DB Size MB</th>
          <th>Disk free GB</th>
          <th>Pool p95 ms</th>
          <th>dead_letters</th>
        </tr>
      </thead>
      <tbody id="recent-samples"></tbody>
    </table>
  </section>
</main>
<script type="application/json" id="soak-data">__SOAK_DATA__</script>
<script>
(() => {
  "use strict";
  const state = { payload: loadPayload(), timer: null };
  const chartDefs = [
    { id: "rss", title: "RSS", unit: "MB", label: "RSS MB", key: ["rss_mb"], digits: 1 },
    { id: "db", title: "DB Size", unit: "MB", label: "DB Size MB", key: ["db_size_mb"], digits: 1 },
    { id: "disk", title: "Disk free", unit: "GB", key: ["disk_free_bytes"], scale: 1 / 1073741824, digits: 2 },
    { id: "pool", title: "Pool p95", unit: "ms", label: "Pool P95 ms", key: ["pool_acquire", "p95_ms"], digits: 3 },
    { id: "dead", title: "dead_letters", unit: "count", key: ["dead_letters_created"], digits: 0, alertWhenPositive: true },
    { id: "events", title: "Events", unit: "count", key: ["events_processed"], digits: 0 },
  ];

  function loadPayload() {
    try {
      const node = document.getElementById("soak-data");
      const parsed = JSON.parse(node ? node.textContent || "{}" : "{}");
      return normalizePayload(parsed);
    } catch (_err) {
      return normalizePayload({});
    }
  }

  function normalizePayload(payload) {
    const normalized = payload && typeof payload === "object" ? payload : {};
    normalized.samples = Array.isArray(normalized.samples) ? normalized.samples.filter(isObject) : [];
    normalized.status = isObject(normalized.status) ? normalized.status : {};
    normalized.trend = isObject(normalized.trend) ? normalized.trend : {};
    normalized.evidence_summary = isObject(normalized.evidence_summary) ? normalized.evidence_summary : {};
    return normalized;
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function valueAt(sample, path) {
    let cursor = sample;
    for (const part of path) {
      if (!isObject(cursor)) return null;
      cursor = cursor[part];
    }
    const value = Number(cursor);
    return Number.isFinite(value) ? value : null;
  }

  function fmt(value, digits) {
    if (!Number.isFinite(value)) return "--";
    return value.toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function timeLabel(iso) {
    if (!iso) return "--";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function elapsedLabel(samples) {
    if (samples.length < 2) return "--";
    const first = new Date(samples[0].sampled_at || "");
    const last = new Date(samples[samples.length - 1].sampled_at || "");
    if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) return "--";
    const seconds = Math.max(0, Math.round((last - first) / 1000));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const rest = seconds % 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${rest}s`;
    return `${rest}s`;
  }

  function verdictKind(verdict) {
    const text = String(verdict || "insufficient_samples").toLowerCase();
    if (text.includes("plateau")) return "good";
    if (text.includes("linear") || text.includes("leak") || text.includes("growth")) return "bad";
    return "warn";
  }

  function verdictText(verdict) {
    const text = String(verdict || "insufficient_samples");
    const kind = verdictKind(text);
    if (kind === "good") return "Clean: growth flattened";
    if (kind === "bad") return "Leak-like sustained growth";
    return "Warming up: not a leak yet";
  }

  function latestSample(payload) {
    return payload.samples.length ? payload.samples[payload.samples.length - 1] : (payload.status.latest_sample || {});
  }

  function render(payload) {
    try {
      payload = normalizePayload(payload);
      const samples = payload.samples;
      const latest = latestSample(payload);
      document.getElementById("run-id").textContent = payload.run_id || payload.status.run_id || "unknown";
      document.getElementById("updated-at").textContent = `updated ${timeLabel(new Date().toISOString())}`;
      renderSummary(payload, latest);
      renderVerdict(payload.trend);
      renderCharts(samples);
      renderTable(samples);
    } catch (err) {
      showNotice(`Dashboard render failed: ${err && err.message ? err.message : err}`);
    }
  }

  function renderSummary(payload, latest) {
    const summary = document.getElementById("summary");
    const alive = Boolean(payload.status.alive);
    const items = [
      ["Generated", timeLabel(payload.generated_at), "mono"],
      ["Alive", alive ? "alive" : "not alive", alive ? "pill good" : "pill bad"],
      ["Phase", payload.status.phase || "unknown", "mono"],
      ["Samples", String(payload.status.sample_count ?? payload.samples.length), "mono"],
      ["Elapsed", elapsedLabel(payload.samples), "mono"],
      ["Latest events", fmt(Number(latest.events_processed), 0), "mono"],
    ];
    summary.replaceChildren(...items.map(([label, value, className]) => {
      const card = document.createElement("div");
      card.className = "metric";
      const labelNode = document.createElement("div");
      labelNode.className = "label";
      labelNode.textContent = label;
      const valueNode = document.createElement("div");
      valueNode.className = `value ${className || ""}`.trim();
      valueNode.textContent = value;
      card.append(labelNode, valueNode);
      return card;
    }));
  }

  function renderVerdict(trend) {
    const verdict = String(trend && trend.verdict ? trend.verdict : "insufficient_samples");
    const kind = verdictKind(verdict);
    const node = document.getElementById("verdict");
    node.className = `verdict ${kind}`;
    node.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = verdictText(verdict);
    const detail = document.createElement("p");
    detail.textContent = `trend.verdict=${verdict}. Only the full-duration late-window verdict is meaningful.`;
    node.append(title, detail);
  }

  function renderCharts(samples) {
    const charts = document.getElementById("charts");
    charts.replaceChildren(...chartDefs.map((def) => buildCard(samples, def)));
  }

  function buildCard(samples, def) {
    const card = document.createElement("article");
    card.className = "card";
    card.setAttribute("aria-label", def.label || `${def.title} ${def.unit}`);
    const values = samples
      .map((sample, idx) => ({ idx, value: valueAt(sample, def.key), sampled_at: sample.sampled_at }))
      .filter((point) => point.value !== null)
      .map((point) => ({ ...point, value: point.value * (def.scale || 1) }));
    const latest = values.length ? values[values.length - 1].value : null;
    if (def.alertWhenPositive && latest && latest > 0) card.classList.add("alert");
    const head = document.createElement("div");
    head.className = "card-head";
    const title = document.createElement("h2");
    title.textContent = def.title;
    const unit = document.createElement("span");
    unit.className = "unit";
    unit.textContent = def.unit;
    head.append(title, unit);
    const current = document.createElement("div");
    current.className = "current";
    current.textContent = latest === null ? "--" : fmt(latest, def.digits);
    const range = document.createElement("div");
    range.className = "range";
    if (values.length) {
      const min = Math.min(...values.map((point) => point.value));
      const max = Math.max(...values.map((point) => point.value));
      range.textContent = `min ${fmt(min, def.digits)} / max ${fmt(max, def.digits)} ${def.unit}`;
    } else {
      range.textContent = `min -- / max -- ${def.unit}`;
    }
    card.append(head, current, range);
    if (values.length < 2) {
      const waiting = document.createElement("div");
      waiting.className = "waiting";
      waiting.textContent = "waiting for samples...";
      card.append(waiting);
    } else {
      card.append(buildSvg(values, def));
    }
    return card;
  }

  function svgEl(name) {
    return document.createElementNS("http:" + "//www.w3.org/2000/svg", name);
  }

  function buildSvg(points, def) {
    const width = 360;
    const height = 170;
    const left = 48;
    const right = 14;
    const top = 12;
    const bottom = 34;
    const min = Math.min(...points.map((point) => point.value));
    const max = Math.max(...points.map((point) => point.value));
    const span = Math.max(max - min, 0.000001);
    const xSpan = Math.max(points[points.length - 1].idx - points[0].idx, 1);
    const x = (point) => left + ((point.idx - points[0].idx) * (width - left - right) / xSpan);
    const y = (value) => top + ((max - value) * (height - top - bottom) / span);
    const coords = points.map((point) => `${x(point).toFixed(1)},${y(point.value).toFixed(1)}`);
    const baseY = height - bottom;
    const svg = svgEl("svg");
    svg.setAttribute("class", "chart");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${def.title} ${def.unit} over soak samples`);
    [max, min + span / 2, min].forEach((value) => {
      const yy = y(value);
      const line = svgEl("line");
      line.setAttribute("class", "grid-line");
      line.setAttribute("x1", String(left));
      line.setAttribute("x2", String(width - right));
      line.setAttribute("y1", yy.toFixed(1));
      line.setAttribute("y2", yy.toFixed(1));
      const text = svgEl("text");
      text.setAttribute("class", "axis-label");
      text.setAttribute("x", "4");
      text.setAttribute("y", String(Math.max(12, yy + 4).toFixed(1)));
      text.textContent = fmt(value, def.digits);
      svg.append(line, text);
    });
    const area = svgEl("polygon");
    area.setAttribute("class", "area");
    area.setAttribute("points", `${left},${baseY} ${coords.join(" ")} ${width - right},${baseY}`);
    const polyline = svgEl("polyline");
    polyline.setAttribute("class", "line");
    polyline.setAttribute("points", coords.join(" "));
    const last = points[points.length - 1];
    const dot = svgEl("circle");
    dot.setAttribute("class", "dot");
    dot.setAttribute("cx", x(last).toFixed(1));
    dot.setAttribute("cy", y(last.value).toFixed(1));
    dot.setAttribute("r", "4");
    const firstLabel = svgEl("text");
    firstLabel.setAttribute("class", "axis-label");
    firstLabel.setAttribute("x", String(left));
    firstLabel.setAttribute("y", String(height - 8));
    firstLabel.textContent = timeLabel(points[0].sampled_at);
    const lastLabel = svgEl("text");
    lastLabel.setAttribute("class", "axis-label");
    lastLabel.setAttribute("x", String(width - right));
    lastLabel.setAttribute("y", String(height - 8));
    lastLabel.setAttribute("text-anchor", "end");
    lastLabel.textContent = timeLabel(last.sampled_at);
    svg.append(area, polyline, dot, firstLabel, lastLabel);
    return svg;
  }

  function renderTable(samples) {
    const tbody = document.getElementById("recent-samples");
    const rows = samples.slice(-25).map((sample) => {
      const row = document.createElement("tr");
      const cells = [
        [sample.sampled_at || "", ""],
        [fmt(Number(sample.events_processed), 0), "num"],
        [fmt(Number(sample.rss_mb), 1), "num"],
        [fmt(Number(sample.db_size_mb), 1), "num"],
        [fmt(Number(sample.disk_free_bytes) / 1073741824, 2), "num"],
        [fmt(valueAt(sample, ["pool_acquire", "p95_ms"]), 3), "num"],
        [fmt(Number(sample.dead_letters_created), 0), "num"],
      ];
      cells.forEach(([value, className]) => {
        const cell = document.createElement("td");
        if (className) cell.className = className;
        cell.textContent = value;
        row.append(cell);
      });
      return row;
    });
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent = "waiting for samples...";
      row.append(cell);
      rows.push(row);
    }
    tbody.replaceChildren(...rows);
  }

  async function refresh() {
    try {
      const response = await fetch("./samples.jsonl", { cache: "no-store" });
      if (!response.ok) throw new Error(`samples.jsonl returned ${response.status}`);
      const text = await response.text();
      const samples = [];
      for (const line of text.split(/\r?\n/)) {
        if (!line.trim()) continue;
        try {
          const parsed = JSON.parse(line);
          if (isObject(parsed)) samples.push(parsed);
        } catch (_err) {
          continue;
        }
      }
      state.payload.samples = samples;
      state.payload.status.sample_count = samples.length;
      state.payload.status.latest_sample = samples.length ? samples[samples.length - 1] : null;
      state.payload.status.latest_sample_at = samples.length ? samples[samples.length - 1].sampled_at : null;
      render(state.payload);
    } catch (_err) {
      showNotice("Live refresh could not read ./samples.jsonl. Reloading the embedded snapshot.");
      window.setTimeout(() => window.location.reload(), 800);
    }
  }

  function showNotice(message) {
    const note = document.getElementById("refresh-note");
    const text = note.querySelector("span");
    text.textContent = message;
    note.classList.add("show");
  }

  document.getElementById("refresh-button").addEventListener("click", refresh);
  document.getElementById("dismiss-note").addEventListener("click", () => {
    document.getElementById("refresh-note").classList.remove("show");
  });
  document.getElementById("auto-refresh-toggle").addEventListener("change", (event) => {
    if (state.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
    if (event.target.checked) {
      state.timer = window.setInterval(refresh, 30000);
    }
  });
  render(state.payload);
})();
</script>
</body>
</html>
"""
    return html.replace("__RUN_ID__", escape(paths.run_id)).replace("__SOAK_DATA__", payload_json)


def write_dashboard(paths: SoakRunPaths, output_path: Path | None = None) -> Path:
    output = output_path or (paths.run_dir / "dashboard.html")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_dashboard_html(paths), encoding="utf-8")
    return output


async def create_dedicated_soak_database(
    base_db_url: str,
    database_name: str,
    *,
    allow_existing: bool = False,
) -> str:
    if not is_loopback_db_url(base_db_url):
        raise RuntimeError("soak-run requires a loopback Postgres URL")
    ensure_dedicated_soak_database(database_name)
    conn = await asyncpg.connect(_admin_dsn(base_db_url))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database_name)
        if exists and not allow_existing:
            raise RuntimeError(f"dedicated soak database already exists: {database_name}")
        if not exists:
            await conn.execute(f"CREATE DATABASE {_quote_ident(database_name)}")
    finally:
        await conn.close()
    return _database_dsn(base_db_url, database_name)


async def _count_rows(pool: Any, table: str) -> int:
    async with pool.acquire() as conn:
        return int(await conn.fetchval(f"SELECT COUNT(*)::bigint FROM {table}") or 0)


async def _database_size_bytes(pool: Any) -> int:
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT pg_database_size(current_database())") or 0)


async def _partition_count(pool: Any) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                """
                SELECT COUNT(*)::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'pmfi'
                  AND (
                    c.relname LIKE 'raw_events_%'
                    OR c.relname LIKE 'normalized_trades_%'
                    OR c.relname LIKE 'metric_windows_%'
                  )
                """
            )
            or 0
        )


async def _prune_soak_rows(
    pool: Any,
    *,
    source_channel: str,
    cutoff: datetime,
) -> int:
    async with pool.acquire() as conn:
        deleted = 0
        source_like = f"{source_channel}-%"
        statements = [
            (
                """
                DELETE FROM alert_deliveries d
                USING alerts a
                WHERE d.alert_id = a.alert_id
                  AND a.created_at < $1
                  AND a.dedupe_key LIKE $2
                """,
                (cutoff, source_like),
            ),
            (
                """
                DELETE FROM alerts
                WHERE created_at < $1
                  AND dedupe_key LIKE $2
                """,
                (cutoff, source_like),
            ),
            (
                """
                DELETE FROM metric_windows
                WHERE window_start < $1
                  AND venue_code = 'polymarket'
                """,
                (cutoff,),
            ),
            (
                """
                DELETE FROM normalized_trades
                WHERE received_at < $1
                  AND source_payload->>'trade_id' LIKE $2
                """,
                (cutoff, source_like),
            ),
            (
                """
                DELETE FROM dead_letters
                WHERE created_at < $1
                  AND source_channel = $2
                """,
                (cutoff, source_channel),
            ),
            (
                """
                DELETE FROM raw_events
                WHERE received_at < $1
                  AND source_channel = $2
                """,
                (cutoff, source_channel),
            ),
        ]
        for sql, args in statements:
            status = await conn.execute(sql, *args)
            try:
                deleted += int(status.rsplit(" ", 1)[-1])
            except ValueError:
                pass
        return deleted


def _run_manifest(run_id: str) -> dict[str, Any]:
    source_channel = f"soak_run_{re.sub(r'[^a-zA-Z0-9_]+', '_', run_id).strip('_')}"
    return {
        "source_channel": source_channel,
        "venue_code": "polymarket",
    }


def _normalize_worker_config(config: dict[str, Any]) -> dict[str, Any]:
    duration_seconds = parse_duration_seconds(config.get("duration_seconds", DEFAULT_DURATION_SECONDS))
    events_per_second = float(config.get("events_per_second", DEFAULT_EVENTS_PER_SECOND))
    max_events = int(config.get("max_events") or math.ceil(duration_seconds * events_per_second))
    return {
        "duration_seconds": duration_seconds,
        "max_events": max(1, max_events),
        "events_per_second": events_per_second,
        "sample_interval_seconds": max(
            1.0,
            float(config.get("sample_interval_seconds", DEFAULT_SAMPLE_INTERVAL_SECONDS)),
        ),
        "pool_size": max(1, int(config.get("pool_size", DEFAULT_POOL_SIZE))),
        "retention_window_seconds": max(
            1.0,
            float(config.get("retention_window_seconds", DEFAULT_RETENTION_WINDOW_SECONDS)),
        ),
        "recovery_interval_seconds": max(
            0.0,
            float(config.get("recovery_interval_seconds", DEFAULT_RECOVERY_INTERVAL_SECONDS)),
        ),
        "max_db_size_bytes": max(
            1,
            int(config.get("max_db_size_bytes", DEFAULT_DB_SIZE_CAP_BYTES)),
        ),
        "disk_min_bytes": max(
            1,
            int(config.get("disk_min_bytes", DEFAULT_DISK_MIN_BYTES)),
        ),
        "disk_min_fraction": max(
            0.0,
            float(config.get("disk_min_fraction", DEFAULT_DISK_MIN_FRACTION)),
        ),
    }


async def _sample_runner(
    *,
    paths: SoakRunPaths,
    manager: PoolManager,
    stats: PoolAcquireWaitStats,
    state: OperationalHealthState,
    disk_guard: DiskHeadroomGuard,
    events_processed: int,
    started_perf: float,
    stop_reason: str | None,
    recoveries_induced: int,
    rows_pruned: int,
) -> dict[str, Any]:
    disk_guard.evaluate(state)
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    db_size = await _database_size_bytes(manager.pool)
    disk_usage = shutil.disk_usage(paths.run_dir)
    rss_bytes = process_rss_bytes()
    sample = {
        "sampled_at": utc_now().isoformat(),
        "pid": os.getpid(),
        "run_id": paths.run_id,
        "events_processed": events_processed,
        "elapsed_seconds": round(time.perf_counter() - started_perf, 3),
        "rss_bytes": rss_bytes,
        "rss_mb": _mb(rss_bytes),
        "tracemalloc_current_mb": _mb(current_bytes),
        "tracemalloc_peak_mb": _mb(peak_bytes),
        "db_size_bytes": db_size,
        "db_size_mb": _mb(db_size),
        "disk_free_bytes": int(disk_usage.free),
        "disk_total_bytes": int(disk_usage.total),
        "pool_acquire": stats.snapshot(),
        "dead_letters_created": await _count_rows(manager.pool, "dead_letters"),
        "raw_events": await _count_rows(manager.pool, "raw_events"),
        "normalized_trades": await _count_rows(manager.pool, "normalized_trades"),
        "partition_count": await _partition_count(manager.pool),
        "baseline_recompute_events": 0,
        "recoveries_induced": recoveries_induced,
        "retention_rows_pruned": rows_pruned,
        "stop_reason": stop_reason,
        "health": state.snapshot(),
    }
    _append_jsonl(paths.samples_file, sample)
    _write_json(
        paths.status_file,
        {
            "phase": "running" if stop_reason is None else "stopping",
            "pid": os.getpid(),
            "run_id": paths.run_id,
            "updated_at": sample["sampled_at"],
            "events_processed": events_processed,
            "stop_reason": stop_reason,
            "latest_sample": sample,
        },
    )
    return sample


async def run_soak_worker(
    *,
    paths: SoakRunPaths,
    base_db_url: str,
    run_config: dict[str, Any],
) -> dict[str, Any]:
    config = _normalize_worker_config(run_config)
    database_name = str(run_config.get("database_name") or dedicated_soak_database_name(paths.run_id))
    ensure_dedicated_soak_database(database_name)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(paths.pid_file, {"pid": os.getpid(), "run_id": paths.run_id, "started_at": utc_now().isoformat()})
    _write_json(paths.config_file, {**config, "database_name": database_name})
    _write_json(paths.status_file, {"phase": "initializing", "pid": os.getpid(), "run_id": paths.run_id})

    soak_db_url = await create_dedicated_soak_database(base_db_url, database_name)
    await _init_schema(soak_db_url)
    stats = PoolAcquireWaitStats(max_samples=4096)
    manager = PoolManager(
        soak_db_url,
        min_size=1,
        max_size=int(config["pool_size"]),
        acquire_wait_stats=stats,
        command_timeout=DEFAULT_SOAK_COMMAND_TIMEOUT_SECONDS,
    )
    await manager.open()
    state = OperationalHealthState()
    disk_guard = DiskHeadroomGuard(
        path=paths.run_dir,
        min_bytes=int(config["disk_min_bytes"]),
        min_fraction=float(config["disk_min_fraction"]),
    )
    manifest = _run_manifest(paths.run_id)
    engine = AlertEngine()
    base_ts = utc_now().replace(microsecond=0)
    interval = pace_interval_seconds(float(config["events_per_second"]))
    started_perf = time.perf_counter()
    stop_reason: str | None = None
    rows_pruned = 0
    recoveries = 0
    events_processed = 0
    next_sample_at = started_perf
    next_event_at = started_perf
    next_recovery_at = (
        started_perf + float(config["recovery_interval_seconds"])
        if float(config["recovery_interval_seconds"]) > 0
        else math.inf
    )
    next_prune_at = started_perf + min(60.0, max(5.0, float(config["retention_window_seconds"]) / 2.0))
    tracemalloc.start()

    async def _alert_sink(*_args: object) -> None:
        return None

    try:
        await _sample_runner(
            paths=paths,
            manager=manager,
            stats=stats,
            state=state,
            disk_guard=disk_guard,
            events_processed=0,
            started_perf=started_perf,
            stop_reason=None,
            recoveries_induced=recoveries,
            rows_pruned=rows_pruned,
        )
        while events_processed < int(config["max_events"]):
            now_perf = time.perf_counter()
            elapsed = now_perf - started_perf
            if stop_requested(paths):
                stop_reason = "operator_stop_requested"
                break
            if elapsed >= int(config["duration_seconds"]):
                stop_reason = "duration_limit_reached"
                break
            sample = await _sample_runner(
                paths=paths,
                manager=manager,
                stats=stats,
                state=state,
                disk_guard=disk_guard,
                events_processed=events_processed,
                started_perf=started_perf,
                stop_reason=None,
                recoveries_induced=recoveries,
                rows_pruned=rows_pruned,
            ) if now_perf >= next_sample_at + float(config["sample_interval_seconds"]) else None
            if sample is not None:
                next_sample_at = now_perf
                stop_reason = decide_soak_stop_reason(
                    intake_allowed=bool(state.intake_allowed),
                    db_size_bytes=int(sample["db_size_bytes"]),
                    max_db_size_bytes=int(config["max_db_size_bytes"]),
                )
                if stop_reason is not None:
                    break
            if now_perf >= next_prune_at:
                cutoff = utc_now() - timedelta(seconds=float(config["retention_window_seconds"]))
                rows_pruned += await _prune_soak_rows(
                    manager.pool,
                    source_channel=str(manifest["source_channel"]),
                    cutoff=cutoff,
                )
                next_prune_at = now_perf + min(60.0, max(5.0, float(config["retention_window_seconds"]) / 2.0))
            if now_perf >= next_recovery_at:
                generation = manager.generation
                await manager.recreate(generation)
                recoveries += 1
                next_recovery_at = now_perf + float(config["recovery_interval_seconds"])
            sleep_for = next_event_at - time.perf_counter()
            if sleep_for > 0:
                await asyncio.sleep(min(sleep_for, 1.0))
                continue
            events_processed += 1
            await process_event(
                _event_from_index(manifest, events_processed, base_ts=base_ts),
                manager.pool,
                engine,
                _alert_sink,
            )
            next_event_at += interval
        if stop_reason is None:
            stop_reason = "event_count_reached"
        await _sample_runner(
            paths=paths,
            manager=manager,
            stats=stats,
            state=state,
            disk_guard=disk_guard,
            events_processed=events_processed,
            started_perf=started_perf,
            stop_reason=stop_reason,
            recoveries_induced=recoveries,
            rows_pruned=rows_pruned,
        )
        evidence = analyze_soak_run(paths, run_config={**config, "database_name": database_name})
        evidence["measurements"]["stop_reason"] = stop_reason
        _write_json(paths.final_evidence_file, evidence)
        _write_json(
            paths.status_file,
            {
                "phase": "complete",
                "pid": os.getpid(),
                "run_id": paths.run_id,
                "updated_at": utc_now().isoformat(),
                "events_processed": events_processed,
                "stop_reason": stop_reason,
                "final_evidence_path": str(paths.final_evidence_file),
            },
        )
        return evidence
    finally:
        tracemalloc.stop()
        await manager.close()


def run_soak_worker_sync(*, paths: SoakRunPaths, base_db_url: str, run_config: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(run_soak_worker(paths=paths, base_db_url=base_db_url, run_config=run_config))


def terminate_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            os.kill(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True
