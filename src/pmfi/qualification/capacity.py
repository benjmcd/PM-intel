from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import yaml

from pmfi.baseline import load_baselines
from pmfi.commands._shared import ROOT, is_loopback_db_url
from pmfi.commands.backup import create_backup
from pmfi.commands.restore import restore_backup
from pmfi.db import create_pool
from pmfi.db.migrations import startup_maintenance
from pmfi.domain import RawEvent
from pmfi.operational_health import PoolAcquireWaitStats
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event
from pmfi.pipeline.supervisor import PoolManager, _TimedPoolProxy
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    sanitize_git_remote,
    schema_fingerprint,
)

DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "capacity_manifest.yaml"
CONFIG_CAPACITY_DEFAULTS: dict[str, int | float] = {
    "pool_acquire_wait_p95_alarm_ms": 100,
    "disk_headroom_min_bytes": 5 * 1024 * 1024 * 1024,
    "disk_headroom_min_fraction": 0.10,
}
RTO_PROVISIONAL_BASELINE: dict[str, int] = {
    "rto_restart_seconds": 300,
    "rto_restore_seconds": 1800,
}
MIN_POOL_P95_SAMPLE_COUNT = 20
# Double the measured steady-state p95 to leave room for ordinary local jitter.
POOL_P95_SAFETY_MARGIN = 2.0
# Five times observed RTO keeps recommendations conservative without claiming SLO proof.
RTO_SAFETY_MARGIN = 5.0
# Keep at least a 100k-event runway when deriving a disk-headroom candidate.
DISK_EVENTS_RUNWAY_MARGIN = 100_000

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def load_capacity_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("capacity manifest must be a mapping")
    return data


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _admin_dsn(base_dsn: str) -> str:
    if not is_loopback_db_url(base_dsn):
        raise RuntimeError("capacity measurement requires a loopback PMFI database URL")
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _quote_ident(identifier: str) -> str:
    if not _DB_NAME_RE.fullmatch(identifier):
        raise ValueError(f"unsafe scratch database name: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _scratch_databases(run_key: str) -> dict[str, str]:
    base = re.sub(r"[^a-z0-9]+", "_", run_key.lower()).strip("_")
    suffix = f"p{os.getpid()}_{uuid.uuid4().hex[:8]}"
    prefix = f"pmfi_{base}"[:38].strip("_")
    return {
        "source": f"{prefix}_{suffix}_source",
        "restored": f"{prefix}_{suffix}_restored",
    }


async def _admin_connect(db_url: str | None = None) -> asyncpg.Connection:
    dsn = db_url or os.environ.get("PMFI_DB_URL")
    if not dsn:
        raise RuntimeError("PMFI_DB_URL is required for capacity measurement")
    return await asyncpg.connect(_admin_dsn(dsn))


async def _drop_database(conn: asyncpg.Connection, name: str) -> None:
    current_db = await conn.fetchval("SELECT current_database()")
    if current_db == name:
        raise RuntimeError(f"refusing to drop scratch database from its own connection: {name}")
    await conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
        name,
    )
    await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(name)} WITH (FORCE)")
    still_exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
    if still_exists:
        raise RuntimeError(f"scratch database still exists after drop: {name}")


async def _create_database(conn: asyncpg.Connection, name: str) -> None:
    await _drop_database(conn, name)
    await conn.execute(f"CREATE DATABASE {_quote_ident(name)}")


async def _prepare_scratch_databases(names: dict[str, str], *, db_url: str) -> None:
    conn = await _admin_connect(db_url)
    try:
        for name in names.values():
            await _create_database(conn, name)
    finally:
        await conn.close()


async def cleanup_capacity_scratch_databases(
    scratch_databases: dict[str, str],
    *,
    db_url: str | None = None,
) -> None:
    conn = await _admin_connect(db_url)
    try:
        for name in scratch_databases.values():
            await _drop_database(conn, name)
    finally:
        await conn.close()


async def list_capacity_scratch_databases(*, db_url: str | None = None) -> list[str]:
    conn = await _admin_connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE 'pmfi_capacity_%' ORDER BY datname"
        )
        return [str(row["datname"]) for row in rows]
    finally:
        await conn.close()


async def _init_schema(db_url: str) -> None:
    conn = await asyncpg.connect(db_url, server_settings={"search_path": "pmfi,public"})
    try:
        for path in sorted((ROOT / "sql").glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def _postgres_version(pool: Any) -> str:
    async with pool.acquire() as conn:
        return str(await conn.fetchval("SELECT version()"))


async def _database_size_bytes(db_url: str) -> int:
    conn = await asyncpg.connect(db_url, server_settings={"search_path": "pmfi,public"})
    try:
        return int(await conn.fetchval("SELECT pg_database_size(current_database())") or 0)
    finally:
        await conn.close()


async def _schema_fingerprint_from_db(db_url: str) -> str:
    conn = await asyncpg.connect(db_url, server_settings={"search_path": "pmfi,public"})
    try:
        columns = await conn.fetch(
            """SELECT table_name, column_name, ordinal_position, data_type, udt_name,
                      is_nullable, column_default
               FROM information_schema.columns
               WHERE table_schema = 'pmfi'
               ORDER BY table_name, ordinal_position"""
        )
        indexes = await conn.fetch(
            """SELECT tablename, indexname, indexdef
               FROM pg_indexes
               WHERE schemaname = 'pmfi'
               ORDER BY tablename, indexname"""
        )
        constraints = await conn.fetch(
            """SELECT c.conname, t.relname AS table_name, pg_get_constraintdef(c.oid) AS definition
               FROM pg_constraint c
               JOIN pg_class t ON t.oid = c.conrelid
               JOIN pg_namespace n ON n.oid = t.relnamespace
               WHERE n.nspname = 'pmfi'
               ORDER BY t.relname, c.conname"""
        )
    finally:
        await conn.close()
    payload = json.dumps(
        {
            "columns": [dict(row) for row in columns],
            "indexes": [dict(row) for row in indexes],
            "constraints": [dict(row) for row in constraints],
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_growth_projection(
    *,
    db_size_before_bytes: int,
    db_size_after_bytes: int,
    workload_events: int,
    free_disk_bytes: int,
) -> dict[str, int | float | None]:
    growth = max(0, int(db_size_after_bytes) - int(db_size_before_bytes))
    events = int(workload_events)
    if events <= 0:
        return {
            "db_growth_bytes": growth,
            "est_bytes_per_event": None,
            "projected_runway_events_or_days": None,
        }
    est_bytes_per_event = growth / events
    runway_events = int(int(free_disk_bytes) / est_bytes_per_event) if est_bytes_per_event > 0 else None
    return {
        "db_growth_bytes": growth,
        "est_bytes_per_event": round(est_bytes_per_event, 3),
        "projected_runway_events_or_days": runway_events,
    }


def recommend_capacity_thresholds(
    measurements: dict[str, Any],
    *,
    config_defaults: dict[str, int | float] = CONFIG_CAPACITY_DEFAULTS,
    rto_baseline: dict[str, int] = RTO_PROVISIONAL_BASELINE,
) -> dict[str, Any]:
    pool_p95 = float(measurements.get("pool_acquire_p95_ms") or 0.0)
    restart = float(measurements.get("rto_restart_seconds") or 0.0)
    restore = float(measurements.get("rto_restore_seconds") or 0.0)
    bytes_per_event = float(measurements.get("est_bytes_per_event") or 0.0)

    recommended = {
        "pool_acquire_wait_p95_alarm_ms": max(
            int(config_defaults["pool_acquire_wait_p95_alarm_ms"]),
            int(math.ceil(pool_p95 * POOL_P95_SAFETY_MARGIN)),
        ),
        "disk_headroom_min_bytes": max(
            int(config_defaults["disk_headroom_min_bytes"]),
            int(math.ceil(bytes_per_event * DISK_EVENTS_RUNWAY_MARGIN)),
        ),
        "disk_headroom_min_fraction": float(config_defaults["disk_headroom_min_fraction"]),
        "rto_restart_seconds": max(
            int(rto_baseline["rto_restart_seconds"]),
            int(math.ceil(restart * RTO_SAFETY_MARGIN)),
        ),
        "rto_restore_seconds": max(
            int(rto_baseline["rto_restore_seconds"]),
            int(math.ceil(restore * RTO_SAFETY_MARGIN)),
        ),
    }
    return {
        "mode": "recommend_only",
        "current_config": dict(config_defaults),
        "provisional_baseline": dict(rto_baseline),
        "recommended": recommended,
        "rationale": (
            "candidate thresholds use bounded-local measurements with safety margins; "
            "config defaults are not changed in this PR; harness-local RTO policy baselines "
            "are also unchanged"
        ),
    }


def evaluate_capacity_pass_invariants(
    measurements: dict[str, Any],
    *,
    min_pool_samples: int,
) -> dict[str, bool]:
    p95 = measurements.get("pool_acquire_p95_ms")
    sample_count = int(measurements.get("sample_count") or 0)
    est_bytes = measurements.get("est_bytes_per_event")
    runway = measurements.get("projected_runway_events_or_days")
    return {
        "pool_acquire_p95_is_measured_from_samples": (
            sample_count >= max(int(min_pool_samples), MIN_POOL_P95_SAMPLE_COUNT)
            and p95 is not None
            and math.isfinite(float(p95))
            and float(p95) >= 0.0
        ),
        "disk_growth_projection_is_computed": (
            int(measurements.get("free_disk_bytes") or 0) > 0
            and int(measurements.get("db_size_bytes") or 0) > 0
            and est_bytes is not None
            and math.isfinite(float(est_bytes))
            and float(est_bytes) >= 0.0
            and runway is not None
            and int(runway) >= 0
        ),
        "restart_rto_is_measured": float(measurements.get("rto_restart_seconds") or 0.0) > 0.0,
        "restore_rto_is_measured": float(measurements.get("rto_restore_seconds") or 0.0) > 0.0,
        "bounded_workload_executed": int(measurements.get("workload_events") or 0) > 0,
        "no_secrets_in_fixtures_logs_or_evidence": bool(
            measurements.get("no_secrets_in_fixtures_logs_or_evidence")
        ),
    }


def _event_from_index(
    manifest: dict[str, Any],
    idx: int,
    *,
    base_ts: datetime,
) -> RawEvent:
    event_ts = base_ts + timedelta(seconds=idx)
    source_channel = str(manifest["source_channel"])
    source_event_id = f"{source_channel}-{idx:04d}"
    venue_market_id = f"pm-capacity-market-{idx % 3}"
    return RawEvent(
        venue_code=str(manifest["venue_code"]),
        source_channel=source_channel,
        source_event_type="last_trade_price",
        source_event_id=source_event_id,
        venue_market_id=venue_market_id,
        exchange_ts=event_ts,
        received_at=event_ts,
        payload={
            "trade_id": source_event_id,
            "market": venue_market_id,
            "outcome": "yes",
            "side": "buy",
            "price": "0.56",
            "size": str(10 + idx),
        },
    )


async def _run_workload(db_url: str, manifest: dict[str, Any], started_at: datetime) -> int:
    workload = manifest["workload"]
    event_count = int(workload["events"])
    pool = await create_pool(
        db_url,
        min_size=1,
        max_size=max(1, int(workload.get("pool_size", 2))),
    )
    engine = AlertEngine()

    async def _noop_alert_handler(*_args: object) -> None:
        return None

    try:
        for idx in range(1, event_count + 1):
            await process_event(
                _event_from_index(manifest, idx, base_ts=started_at),
                pool,
                engine,
                _noop_alert_handler,
            )
    finally:
        await pool.close()
    return event_count


async def _measure_pool_acquire_p95(db_url: str, manifest: dict[str, Any]) -> dict[str, Any]:
    workload = manifest["workload"]
    sample_count = int(workload["pool_sample_count"])
    hold_seconds = float(workload.get("pool_hold_seconds", 0.0))
    concurrency = max(1, int(workload.get("concurrency", sample_count)))
    pool_size = max(1, int(workload.get("pool_size", 2)))
    gate = asyncio.Semaphore(concurrency)
    stats = PoolAcquireWaitStats(max_samples=max(1, sample_count))
    pool = await create_pool(
        db_url,
        min_size=1,
        max_size=pool_size,
    )

    async def _warm_one() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    timed_pool = _TimedPoolProxy(pool, stats)

    async def _sample() -> None:
        async with gate:
            async with timed_pool.acquire() as conn:
                await conn.fetchval("SELECT pg_sleep($1::double precision)", hold_seconds)

    try:
        await asyncio.gather(*(_warm_one() for _ in range(pool_size)))
        await asyncio.gather(*(_sample() for _ in range(sample_count)))
        return stats.snapshot()
    finally:
        await pool.close()


async def _measure_restart_to_ready(db_url: str) -> float:
    started = time.perf_counter()
    stats = PoolAcquireWaitStats()
    pm = PoolManager(db_url, min_size=1, max_size=2, acquire_wait_stats=stats)
    await pm.open()
    try:
        await startup_maintenance(pm.pool)
        await load_baselines(pm.pool)
        async with pm.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    finally:
        await pm.close()
    return time.perf_counter() - started


def _measure_restore_seconds(
    *,
    source_db: str,
    restored_db: str,
    db_url: str,
) -> tuple[float, int]:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pmfi-capacity-backup-") as tmp_dir:
        backup_path = create_backup(
            backup_dir=tmp_dir,
            source_db=source_db,
            configured_db_url=db_url,
        )
        backup_size = backup_path.stat().st_size
        restore_backup(
            backup_file=backup_path,
            target_db=restored_db,
            configured_db_url=db_url,
        )
    return time.perf_counter() - started, backup_size


def build_capacity_evidence(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    measurements: dict[str, Any],
    postgres_version: str,
    actual_facets: list[str],
    commands: list[str],
    scratch_databases: dict[str, str],
    measurement_error: str | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    manifest_path = Path(manifest_path)
    measured = dict(measurements)
    measured["no_secrets_in_fixtures_logs_or_evidence"] = False
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {
            "envelope": "MEASURED_BOUNDED_LOCAL" if measurement_error is None else "DEFERRED_DB_UNAVAILABLE",
            "long_horizon_soak": "ACCEPTED_DEBT",
            "multi_host_reproducibility": "ACCEPTED_DEBT",
        },
        "repository": {
            "remote": sanitize_git_remote(_git_value(["config", "--get", "remote.origin.url"])),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_capacity_measure",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": postgres_version,
            "schema_version": schema_fingerprint(ROOT / "sql"),
            "environment": "offline_db_gated",
        },
        "time": {
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
        },
        "expected_truth": {
            "manifest": _manifest_rel(manifest_path) if manifest_path.is_relative_to(ROOT) else str(manifest_path),
            "artifact_hash": _sha256_path(manifest_path),
        },
        "evidence": {
            "required_facets": list(manifest.get("required_facets", [])),
            "actual_facets": ["OFFLINE"] if measurement_error else list(actual_facets),
            "deferred_facets": [
                item["facet"] for item in manifest.get("manual_deferred_facets", [])
            ],
            "commands": list(commands),
            "artifacts": [
                _manifest_rel(manifest_path) if manifest_path.is_relative_to(ROOT) else str(manifest_path)
            ],
            "artifact_hashes": [_sha256_path(manifest_path)],
            "scratch_databases": dict(scratch_databases),
        },
        "measurements": measured,
        "recommended_thresholds": recommend_capacity_thresholds(measured),
        "pass_invariants": {},
        "fail_conditions": [],
        "blocker_or_inconclusive_reason": measurement_error,
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": list(manifest.get("manual_deferred_facets", [])),
        "next_action": "orchestrator_verify_pr",
    }
    measured["no_secrets_in_fixtures_logs_or_evidence"] = not evidence_contains_secret(
        manifest_path,
        evidence,
    )
    invariants = evaluate_capacity_pass_invariants(
        measured,
        min_pool_samples=int(manifest.get("workload", {}).get("pool_sample_count", 1)),
    )
    evidence["measurements"] = measured
    evidence["recommended_thresholds"] = recommend_capacity_thresholds(measured)
    evidence["pass_invariants"] = invariants
    if measurement_error:
        evidence["outcome"] = "INCONCLUSIVE"
    elif not all(invariants.values()):
        evidence["outcome"] = "FAIL"
        evidence["fail_conditions"] = [
            key for key, value in invariants.items() if value is not True
        ]
    return evidence


async def run_capacity_measurement(
    pool: Any,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    db_url: str | None = None,
    keep_scratch: bool = False,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_capacity_manifest(manifest_path)
    configured_db_url = db_url or os.environ.get("PMFI_DB_URL")
    if not configured_db_url:
        raise RuntimeError("PMFI_DB_URL is required for capacity measurement")
    if not is_loopback_db_url(configured_db_url):
        raise RuntimeError("capacity measurement requires a loopback PMFI database URL")

    names = _scratch_databases(str(manifest["run_key"]))
    await _prepare_scratch_databases(names, db_url=configured_db_url)
    urls = {key: _database_dsn(configured_db_url, name) for key, name in names.items()}
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        await _init_schema(urls["source"])
        rto_restart_seconds = await _measure_restart_to_ready(urls["source"])
        db_size_before = await _database_size_bytes(urls["source"])
        workload_events = await _run_workload(urls["source"], manifest, started_at)
        pool_snapshot = await _measure_pool_acquire_p95(urls["source"], manifest)
        db_size_after = await _database_size_bytes(urls["source"])
        rto_restore_seconds, backup_size = _measure_restore_seconds(
            source_db=names["source"],
            restored_db=names["restored"],
            db_url=configured_db_url,
        )
        source_schema = await _schema_fingerprint_from_db(urls["source"])
        restored_schema = await _schema_fingerprint_from_db(urls["restored"])
        disk_usage = shutil.disk_usage(ROOT)
        growth = compute_growth_projection(
            db_size_before_bytes=db_size_before,
            db_size_after_bytes=db_size_after,
            workload_events=workload_events,
            free_disk_bytes=int(disk_usage.free),
        )
        measurements: dict[str, Any] = {
            "pool_acquire_p95_ms": pool_snapshot["p95_ms"],
            "sample_count": pool_snapshot["sample_count"],
            "free_disk_bytes": int(disk_usage.free),
            "db_size_bytes": db_size_after,
            "rto_restart_seconds": round(rto_restart_seconds, 3),
            "rto_restore_seconds": round(rto_restore_seconds, 3),
            "workload_events": workload_events,
            "concurrency": int(manifest["workload"]["concurrency"]),
            "backup_size_bytes": backup_size,
            "source_schema_fingerprint": source_schema,
            "restored_schema_fingerprint": restored_schema,
            "no_secrets_in_fixtures_logs_or_evidence": False,
            **growth,
        }
        actual_facets = ["OFFLINE"]
        if workload_events > 0:
            actual_facets.extend(["POSTGRES_INTEGRATION", "BOUNDED_LOCAL_WORKLOAD"])
        if rto_restart_seconds > 0:
            actual_facets.append("RTO_RESTART")
        if rto_restore_seconds > 0 and backup_size > 0:
            actual_facets.append("RTO_RESTORE")
        if source_schema == restored_schema:
            actual_facets.append("SCHEMA_DUMP_FIDELITY")
        return build_capacity_evidence(
            manifest=manifest,
            manifest_path=manifest_path,
            measurements=measurements,
            postgres_version=await _postgres_version(pool),
            actual_facets=actual_facets,
            commands=[
                "pmfi capacity-measure --format json",
                "python -m pytest -q tests\\test_capacity_measure_db.py",
            ],
            scratch_databases=names,
        )
    finally:
        if not keep_scratch:
            await cleanup_capacity_scratch_databases(names, db_url=configured_db_url)
