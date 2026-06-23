from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import time
import tracemalloc
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import yaml

from pmfi.commands._shared import ROOT, is_loopback_db_url
from pmfi.db import create_pool
from pmfi.domain import RawEvent
from pmfi.operational_health import OperationalHealthState, PoolAcquireWaitStats
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event
from pmfi.pipeline.supervisor import PoolManager
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    sanitize_git_remote,
    schema_fingerprint,
)

DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "soak_manifest.yaml"
DEFAULT_BASELINE_MANIFEST = ROOT / "tests" / "qualification" / "soak_baseline_manifest.yaml"
MIN_POOL_P95_SAMPLE_COUNT = 20
POOL_P95_RECOMMEND_MARGIN = 2.0
MEMORY_RECOMMEND_MARGIN = 2.0
THROUGHPUT_RECOMMEND_FRACTION = 0.5
DEFAULT_MEMORY_GROWTH_TOLERANCE_MB = 1.0
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def load_soak_stability_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("soak stability manifest must be a mapping")
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
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _admin_dsn(base_dsn: str) -> str:
    if not is_loopback_db_url(base_dsn):
        raise RuntimeError("soak stability measurement requires a loopback PMFI database URL")
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
    prefix = f"pmfi_{base}"[:42].strip("_")
    return {"source": f"{prefix}_{suffix}"}


async def _admin_connect(db_url: str | None = None) -> asyncpg.Connection:
    dsn = db_url or os.environ.get("PMFI_DB_URL")
    if not dsn:
        raise RuntimeError("PMFI_DB_URL is required for soak stability measurement")
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


async def _prepare_scratch_database(name: str, *, db_url: str) -> None:
    conn = await _admin_connect(db_url)
    try:
        await _create_database(conn, name)
    finally:
        await conn.close()


async def cleanup_soak_scratch_databases(
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


async def list_soak_scratch_databases(*, db_url: str | None = None) -> list[str]:
    conn = await _admin_connect(db_url)
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE 'pmfi_soak_%' ORDER BY datname"
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


def _event_from_index(manifest: dict[str, Any], idx: int, *, base_ts: datetime) -> RawEvent:
    event_ts = base_ts + timedelta(milliseconds=idx * 100)
    source_channel = str(manifest["source_channel"])
    source_event_id = f"{source_channel}-{idx:04d}"
    venue_market_id = f"pm-soak-market-{idx % 3}"
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
            "price": "0.52",
            "size": str(5 + idx),
        },
    )


async def _dead_letters_created(pool: Any) -> int:
    async with pool.acquire() as conn:
        return int(await conn.fetchval("SELECT COUNT(*)::bigint FROM dead_letters") or 0)


def _memory_mb(bytes_value: int) -> float:
    return round(bytes_value / (1024 * 1024), 3)


def _memory_growth_mb(measurements: dict[str, Any]) -> float | None:
    samples = measurements.get("samples") or []
    values: list[tuple[int, float]] = []
    for sample in samples:
        try:
            events = int(sample.get("events_processed") or 0)
            value = float(sample["memory_current_mb"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append((events, value))
    if not values:
        raw_growth = measurements.get("memory_growth_mb")
        if raw_growth is None:
            return None
        try:
            growth = float(raw_growth)
        except (TypeError, ValueError):
            return None
        return round(max(0.0, growth), 3) if math.isfinite(growth) else None
    post_start = [value for events, value in values if events > 0]
    baseline = post_start[0] if post_start else values[0][1]
    max_current = max(value for _events, value in values)
    return round(max(0.0, max_current - baseline), 3)


def workload_stop_reason(
    *,
    events_processed: int,
    requested_events: int,
    started_perf: float,
    now_perf: float,
    max_duration_seconds: float | None,
) -> str | None:
    if events_processed >= requested_events:
        return "event_count_reached"
    if (
        max_duration_seconds is not None
        and max_duration_seconds > 0
        and now_perf - started_perf >= max_duration_seconds
    ):
        return "duration_limit_reached"
    return None


def add_soak_baseline_rate_metrics(measurements: dict[str, Any]) -> dict[str, Any]:
    updated = dict(measurements)
    events_processed = int(updated.get("events_processed") or 0)
    memory_growth = _memory_growth_mb(updated)
    if memory_growth is not None:
        updated["memory_growth_mb"] = memory_growth
    if events_processed <= 0:
        updated["memory_growth_per_1000_events_mb"] = 0.0
        updated["dead_letters_per_1000_events"] = 0.0
        return updated
    per_1000 = 1000.0 / events_processed
    updated["memory_growth_per_1000_events_mb"] = round(
        (memory_growth or 0.0) * per_1000,
        3,
    )
    updated["dead_letters_per_1000_events"] = round(
        int(updated.get("dead_letters_created") or 0) * per_1000,
        3,
    )
    return updated


async def _run_workload(db_url: str, manifest: dict[str, Any], started_at: datetime) -> dict[str, Any]:
    workload = manifest["workload"]
    requested_events = int(workload["events"])
    max_duration_seconds_raw = workload.get("max_duration_seconds")
    max_duration_seconds = (
        None
        if max_duration_seconds_raw is None
        else max(0.0, float(max_duration_seconds_raw))
    )
    pool_size = max(1, int(workload.get("pool_size", 2)))
    min_samples = max(1, int(workload.get("min_samples", 4)))
    memory_growth_tolerance_mb = max(
        0.0,
        float(workload.get("memory_growth_tolerance_mb", DEFAULT_MEMORY_GROWTH_TOLERANCE_MB)),
    )
    sample_every = max(
        1,
        int(workload.get("sample_every_events", max(1, requested_events // min_samples))),
    )
    recovery_after = max(
        1,
        min(requested_events, int(workload.get("recovery_after_events", requested_events // 2 or 1))),
    )
    stats = PoolAcquireWaitStats(max_samples=max(32, requested_events * 4))
    state = OperationalHealthState()
    manager = PoolManager(db_url, min_size=1, max_size=pool_size, acquire_wait_stats=stats)
    await manager.open()
    engine = AlertEngine()
    samples: list[dict[str, Any]] = []
    recovery_induced = False
    recovery_successful = False
    tracemalloc.start()
    memory_start, _ = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    events_processed = 0
    stop_reason: str | None = None

    async def _noop_alert_handler(*_args: object) -> None:
        return None

    async def _sample(label: str, events_processed: int) -> None:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        pool_snapshot = stats.snapshot()
        samples.append(
            {
                "label": label,
                "events_processed": events_processed,
                "pool_acquire": dict(pool_snapshot),
                "memory_current_mb": _memory_mb(current_bytes),
                "memory_peak_mb": _memory_mb(peak_bytes),
                "dead_letters_created": await _dead_letters_created(manager.pool),
                "health": state.snapshot(),
            }
        )

    try:
        await _sample("start", 0)
        for idx in range(1, requested_events + 1):
            duration_reason = workload_stop_reason(
                events_processed=events_processed,
                requested_events=requested_events,
                started_perf=started,
                now_perf=time.perf_counter(),
                max_duration_seconds=max_duration_seconds,
            )
            if duration_reason == "duration_limit_reached":
                stop_reason = duration_reason
                break
            await process_event(
                _event_from_index(manifest, idx, base_ts=started_at),
                manager.pool,
                engine,
                _noop_alert_handler,
            )
            events_processed = idx
            if idx == recovery_after and not recovery_induced:
                recovery_induced = True
                observed_generation = manager.generation
                state.set_reason(
                    "soak_induced_pool_recovery",
                    status="DEGRADED",
                    message="soak stability harness intentionally recreated the local DB pool",
                    blocks_intake=False,
                    observed={"events_processed": idx},
                    threshold={"scope": "single bounded local recovery"},
                )
                await _sample("pre_recovery", idx)
                await manager.recreate(observed_generation)
                recovery_successful = manager.generation > observed_generation
                state.clear_reason("soak_induced_pool_recovery")
                await _sample("post_recovery", idx)
            if idx % sample_every == 0:
                await _sample("interval", idx)
        if stop_reason is None:
            stop_reason = workload_stop_reason(
                events_processed=events_processed,
                requested_events=requested_events,
                started_perf=started,
                now_perf=time.perf_counter(),
                max_duration_seconds=max_duration_seconds,
            )
        if stop_reason is None:
            stop_reason = "event_count_reached"
        await _sample("final", events_processed)
        elapsed = max(0.000001, time.perf_counter() - started)
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        pool_snapshot = stats.snapshot()
        dead_letters = await _dead_letters_created(manager.pool)
        measurements = {
            "requested_events": requested_events,
            "events_processed": events_processed,
            "max_duration_seconds": max_duration_seconds,
            "duration_limit_reached": stop_reason == "duration_limit_reached",
            "stop_reason": stop_reason,
            "throughput_events_per_second": round(events_processed / elapsed, 3),
            "elapsed_seconds": round(elapsed, 3),
            "sample_count": len(samples),
            "min_required_samples": min_samples,
            "pool_acquire_p95_ms": pool_snapshot["p95_ms"],
            "pool_acquire_max_ms": pool_snapshot["max_ms"],
            "pool_acquire_sample_count": pool_snapshot["sample_count"],
            "memory_start_mb": _memory_mb(memory_start),
            "memory_current_mb": _memory_mb(current_bytes),
            "memory_peak_mb": _memory_mb(peak_bytes),
            "memory_growth_tolerance_mb": memory_growth_tolerance_mb,
            "dead_letters_created": dead_letters,
            "max_allowed_dead_letters": 0,
            "recovery_induced": recovery_induced,
            "recovery_successful": recovery_successful,
            "health_final": state.snapshot(),
            "samples": samples,
            "no_secrets_in_fixtures_logs_or_evidence": False,
        }
        return add_soak_baseline_rate_metrics(measurements)
    finally:
        tracemalloc.stop()
        await manager.close()


def evaluate_soak_stability_pass_invariants(measurements: dict[str, Any]) -> dict[str, bool]:
    pool_p95 = measurements.get("pool_acquire_p95_ms")
    memory_peak = measurements.get("memory_peak_mb")
    memory_growth = _memory_growth_mb(measurements)
    memory_growth_tolerance = float(
        measurements.get("memory_growth_tolerance_mb") or DEFAULT_MEMORY_GROWTH_TOLERANCE_MB
    )
    dead_letters = int(measurements.get("dead_letters_created") or 0)
    max_dead_letters = int(measurements.get("max_allowed_dead_letters") or 0)
    return {
        "sustained_throughput_observed": (
            int(measurements.get("events_processed") or 0) > 0
            and float(measurements.get("throughput_events_per_second") or 0.0) > 0.0
        ),
        "resource_metrics_finite": (
            pool_p95 is not None
            and math.isfinite(float(pool_p95))
            and float(pool_p95) >= 0.0
            and memory_peak is not None
            and math.isfinite(float(memory_peak))
            and float(memory_peak) >= 0.0
        ),
        "pool_acquire_p95_has_minimum_samples": (
            pool_p95 is not None
            and int(measurements.get("pool_acquire_sample_count") or 0) >= MIN_POOL_P95_SAMPLE_COUNT
        ),
        "memory_growth_within_tolerance": (
            memory_growth is not None
            and math.isfinite(memory_growth)
            and memory_growth <= memory_growth_tolerance
        ),
        "recovered_after_induced_pool_recreation": (
            bool(measurements.get("recovery_induced"))
            and bool(measurements.get("recovery_successful"))
        ),
        "dead_letters_bounded": dead_letters <= max_dead_letters,
        "sample_count_at_least_minimum": (
            int(measurements.get("sample_count") or 0)
            >= int(measurements.get("min_required_samples") or 0)
        ),
        "no_secrets_in_fixtures_logs_or_evidence": bool(
            measurements.get("no_secrets_in_fixtures_logs_or_evidence")
        ),
    }


def recommend_soak_thresholds(measurements: dict[str, Any]) -> dict[str, Any]:
    pool_p95 = float(measurements.get("pool_acquire_p95_ms") or 0.0)
    memory_peak = float(measurements.get("memory_peak_mb") or 0.0)
    memory_growth = float(measurements.get("memory_growth_mb") or 0.0)
    memory_growth_per_1000 = float(measurements.get("memory_growth_per_1000_events_mb") or 0.0)
    throughput = float(measurements.get("throughput_events_per_second") or 0.0)
    return {
        "mode": "recommend_only",
        "mutates_config": False,
        "recommended": {
            "pool_acquire_wait_p95_alarm_ms": int(math.ceil(pool_p95 * POOL_P95_RECOMMEND_MARGIN)),
            "memory_peak_alarm_mb": int(math.ceil(memory_peak * MEMORY_RECOMMEND_MARGIN)),
            "memory_growth_alarm_mb": int(math.ceil(memory_growth * MEMORY_RECOMMEND_MARGIN)),
            "memory_growth_per_1000_events_alarm_mb": round(
                memory_growth_per_1000 * MEMORY_RECOMMEND_MARGIN,
                3,
            ),
            "min_throughput_events_per_second": round(throughput * THROUGHPUT_RECOMMEND_FRACTION, 3),
            "max_dead_letters_per_bounded_run": int(measurements.get("dead_letters_created") or 0),
        },
        "rationale": (
            "candidate soak thresholds are derived from a bounded local scratch-DB workload; "
            "no config defaults are changed and multi-day soak approval remains separate"
        ),
    }


def build_soak_stability_evidence(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    measurements: dict[str, Any],
    actual_facets: list[str],
    commands: list[str],
    scratch_databases: dict[str, str],
    postgres_version: str | None = None,
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
            "soak": "MEASURED_BOUNDED_LOCAL",
            "multi_day_soak": "ACCEPTED_DEBT",
            "multi_host_reproducibility": "ACCEPTED_DEBT",
        },
        "repository": {
            "remote": sanitize_git_remote(_git_value(["config", "--get", "remote.origin.url"])),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_soak_stability",
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
            "manifest": _manifest_rel(manifest_path),
            "artifact_hash": _sha256_path(manifest_path),
        },
        "evidence": {
            "required_facets": list(manifest.get("required_facets", [])),
            "actual_facets": list(actual_facets),
            "deferred_facets": [
                item["facet"] for item in manifest.get("manual_deferred_facets", [])
            ],
            "commands": list(commands),
            "artifacts": [_manifest_rel(manifest_path)],
            "artifact_hashes": [_sha256_path(manifest_path)],
            "scratch_databases": dict(scratch_databases),
        },
        "measurements": measured,
        "recommended_thresholds": recommend_soak_thresholds(measured),
        "pass_invariants": {},
        "fail_conditions": [],
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": list(manifest.get("manual_deferred_facets", [])),
        "next_action": "orchestrator_verify_pr",
    }
    measured["no_secrets_in_fixtures_logs_or_evidence"] = not evidence_contains_secret(
        manifest_path,
        evidence,
    )
    invariants = evaluate_soak_stability_pass_invariants(measured)
    evidence["measurements"] = measured
    evidence["recommended_thresholds"] = recommend_soak_thresholds(measured)
    evidence["pass_invariants"] = invariants
    if not all(invariants.values()):
        evidence["outcome"] = "FAIL"
        evidence["fail_conditions"] = [
            key for key, value in invariants.items() if value is not True
        ]
    return evidence


async def run_soak_stability_measurement(
    pool: Any,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    db_url: str | None = None,
    keep_scratch: bool = False,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_soak_stability_manifest(manifest_path)
    configured_db_url = db_url or os.environ.get("PMFI_DB_URL")
    if not configured_db_url:
        raise RuntimeError("PMFI_DB_URL is required for soak stability measurement")
    if not is_loopback_db_url(configured_db_url):
        raise RuntimeError("soak stability measurement requires a loopback PMFI database URL")

    scratch = _scratch_databases(str(manifest["run_key"]))
    await _prepare_scratch_database(scratch["source"], db_url=configured_db_url)
    source_url = _database_dsn(configured_db_url, scratch["source"])
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        await _init_schema(source_url)
        measurements = await _run_workload(source_url, manifest, started_at)
        return build_soak_stability_evidence(
            manifest=manifest,
            manifest_path=manifest_path,
            measurements=measurements,
            actual_facets=[
                "OFFLINE",
                "POSTGRES_INTEGRATION",
                "SCRATCH_DB",
                "BOUNDED_LOCAL_WORKLOAD",
                "RECOVERY_INDUCED",
            ],
            commands=[
                (
                    "pmfi soak --measure-stability --manifest "
                    f"{_manifest_rel(manifest_path)} --format json"
                ),
                "python -m pytest -q tests\\test_soak_stability_db.py",
            ],
            scratch_databases=scratch,
            postgres_version=await _postgres_version(pool),
        )
    finally:
        if not keep_scratch:
            await cleanup_soak_scratch_databases(scratch, db_url=configured_db_url)
