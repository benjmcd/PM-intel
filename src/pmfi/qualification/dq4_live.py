from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from pmfi.commands.data import fetch_data_coverage_rows
from pmfi.config import load_config
from pmfi.data_reports import NON_TRADE_RAW_EVENT_TYPES_BY_VENUE, summarize_data_coverage_rows
from pmfi.qualification.dq1_capture import _count_duplicate_canonical_facts
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    scrubbed_git_remote,
    schema_fingerprint,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq4_live_manifest.yaml"


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def load_dq4_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("DQ-4 manifest must be a mapping")
    return data


async def cleanup_dq4_offline_rows(pool: Any, source_channel: str) -> None:
    async with pool.acquire() as conn:
        raw_rows = await conn.fetch(
            "SELECT raw_event_id FROM raw_events WHERE source_channel = $1",
            source_channel,
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        market_rows = await conn.fetch(
            "SELECT market_id FROM markets WHERE venue_market_id LIKE 'DQ4-LIVE-%'"
        )
        market_ids = [row["market_id"] for row in market_rows]
        if raw_ids:
            await conn.execute("DELETE FROM alerts WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM normalized_trades WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            await conn.execute("DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        await conn.execute("DELETE FROM event_dedupe_keys WHERE source_channel = $1", source_channel)
        if market_ids:
            await conn.execute("DELETE FROM alerts WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM metric_windows WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute(
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = ANY($1::uuid[])",
                market_ids,
            )
            await conn.execute("DELETE FROM normalized_trades WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM market_outcomes WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_ids)


def evaluate_dq4_pass_invariants(measurements: dict[str, Any]) -> dict[str, bool]:
    counts = (measurements.get("coverage") or {}).get("counts") or {}
    required_venues = [str(v) for v in measurements.get("required_venues") or []]
    per_venue_counts = measurements.get("per_venue_counts") or {}
    min_per_venue = int(measurements.get("min_per_venue", 1) or 1)
    operational_health = measurements.get("operational_health") or {}
    status_map = measurements.get("status_map") or {}
    any_circuit_open = any(bool(item.get("circuit_open", False)) for item in status_map.values())
    return {
        "no_silent_loss_every_raw_event_accounted": int(counts.get("unaccounted") or 0) == 0,
        "no_duplicate_canonical_facts_in_window": int(
            measurements.get("duplicate_canonical_facts") or 0
        ) == 0,
        "dead_letter_rate_within_threshold": float(
            measurements.get("dead_letter_rate") or 0.0
        ) <= float(measurements.get("dead_letter_rate_threshold") or 0.0),
        "both_venues_captured": all(
            int(per_venue_counts.get(venue) or 0) >= min_per_venue
            for venue in required_venues
        ),
        "operational_health_ok_during_run": (
            str(operational_health.get("status", "")).upper() == "OK"
            and bool(operational_health.get("intake_allowed", False)) is True
            and not any_circuit_open
        ),
        "no_secrets_in_fixtures_logs_or_evidence": bool(
            measurements.get("no_secrets_in_fixtures_logs_or_evidence")
        ),
    }


async def _db_now(pool: Any) -> datetime:
    async with pool.acquire() as conn:
        return _as_utc(await conn.fetchval("SELECT now()"))


async def _raw_ids_for_window(
    conn: Any,
    window_start: datetime,
    window_end: datetime,
    *,
    source_channel: str | None = None,
) -> list[int]:
    params: list[Any] = [window_start, window_end]
    channel_sql = ""
    if source_channel:
        params.append(source_channel)
        channel_sql = f" AND source_channel = ${len(params)}"
    rows = await conn.fetch(
        f"""SELECT raw_event_id
            FROM raw_events
            WHERE received_at >= $1
              AND received_at <= $2
              {channel_sql}
            ORDER BY raw_event_id""",
        *params,
    )
    return [int(row["raw_event_id"]) for row in rows]


async def _coverage_rows_for_window(
    pool: Any,
    window_start: datetime,
    window_end: datetime,
    *,
    source_channel: str | None = None,
) -> list[Any]:
    if not source_channel:
        return await fetch_data_coverage_rows(pool, since=window_start, until=window_end)
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                WITH normalized_raw AS (
                    SELECT DISTINCT raw_event_id
                    FROM normalized_trades
                    WHERE raw_event_id IS NOT NULL
                ),
                dead_letter_raw AS (
                    SELECT DISTINCT raw_event_id
                    FROM dead_letters
                    WHERE raw_event_id IS NOT NULL
                      AND resolved IS NOT TRUE
                ),
                raw_dispositions AS (
                    SELECT
                        re.venue_code,
                        re.source_event_type,
                        (nr.raw_event_id IS NOT NULL) AS has_normalized,
                        (dl.raw_event_id IS NOT NULL) AS has_dead_letter,
                        (
                            re.venue_code = 'polymarket'
                            AND (
                                COALESCE(re.venue_market_id, '') LIKE 'pm-%'
                                OR COALESCE(re.payload->>'market', '') LIKE 'pm-%'
                            )
                        ) AS is_synthetic
                    FROM raw_events re
                    LEFT JOIN normalized_raw nr ON nr.raw_event_id = re.raw_event_id
                    LEFT JOIN dead_letter_raw dl ON dl.raw_event_id = re.raw_event_id
                    WHERE re.received_at >= $1
                      AND re.received_at <= $2
                      AND re.source_channel = $3
                )
                SELECT
                    venue_code,
                    source_event_type,
                    has_normalized,
                    has_dead_letter,
                    is_synthetic,
                    COUNT(*) AS cnt
                FROM raw_dispositions
                GROUP BY venue_code, source_event_type, has_normalized, has_dead_letter, is_synthetic
                ORDER BY venue_code, source_event_type, has_normalized DESC, has_dead_letter DESC, is_synthetic
                """,
                window_start,
                window_end,
                source_channel,
            )
        )


async def collect_dq4_window_measurements(
    pool: Any,
    window_start: datetime,
    window_end: datetime,
    *,
    required_venues: list[str],
    dead_letter_rate_threshold: float,
    min_per_venue: int,
    source_channel: str | None = None,
    operational_health: dict[str, Any] | None = None,
    status_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    window_start = _as_utc(window_start)
    window_end = _as_utc(window_end)
    coverage_rows = await _coverage_rows_for_window(
        pool,
        window_start,
        window_end,
        source_channel=source_channel,
    )
    coverage = summarize_data_coverage_rows(coverage_rows, exclude_synthetic=False)
    async with pool.acquire() as conn:
        raw_ids = await _raw_ids_for_window(
            conn,
            window_start,
            window_end,
            source_channel=source_channel,
        )
        normalized_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM normalized_trades WHERE raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        alert_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM alerts WHERE raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        dead_letter_count = int(
            await conn.fetchval(
                """SELECT COUNT(*)
                   FROM dead_letters
                   WHERE raw_event_id = ANY($1::bigint[])
                     AND resolved IS NOT TRUE""",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        venue_rows = await conn.fetch(
            """SELECT venue_code, COUNT(*)::int AS cnt
               FROM raw_events
               WHERE raw_event_id = ANY($1::bigint[])
               GROUP BY venue_code
               ORDER BY venue_code""",
            raw_ids,
        ) if raw_ids else []
        duplicate_canonical = await _count_duplicate_canonical_facts(conn, raw_ids)
        postgres_version = str(await conn.fetchval("SHOW server_version"))
    per_venue_counts = {str(row["venue_code"]): int(row["cnt"] or 0) for row in venue_rows}
    for venue in required_venues:
        per_venue_counts.setdefault(str(venue), 0)
    raw_event_count = len(raw_ids)
    window_seconds = max(0.001, (window_end - window_start).total_seconds())
    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_duration_seconds": round(window_seconds, 3),
        "raw_event_count": raw_event_count,
        "normalized_trade_count": normalized_count,
        "alert_count": alert_count,
        "dead_letter_count": dead_letter_count,
        "dead_letter_rate": 0.0 if raw_event_count == 0 else dead_letter_count / raw_event_count,
        "dead_letter_rate_threshold": float(dead_letter_rate_threshold),
        "events_per_second": round(raw_event_count / window_seconds, 6),
        "per_venue_counts": per_venue_counts,
        "required_venues": list(required_venues),
        "min_per_venue": int(min_per_venue),
        "coverage": coverage,
        "duplicate_canonical_facts": duplicate_canonical,
        "operational_health": operational_health or {"status": "OK", "intake_allowed": True},
        "status_map": status_map or {
            venue: {"circuit_open": False}
            for venue in required_venues
        },
        "postgres_version": postgres_version,
        "documented_non_trade_skip_types": {
            venue: sorted(types)
            for venue, types in sorted(NON_TRADE_RAW_EVENT_TYPES_BY_VENUE.items())
        },
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }


def _run_bounded_live_ingest(max_seconds: int, max_events: int) -> int:
    if os.environ.get("PMFI_ENABLE_LIVE") != "1":
        raise RuntimeError("DQ-4 live trial requires PMFI_ENABLE_LIVE=1")
    from pmfi.cli import cmd_ingest

    args = argparse.Namespace(
        venue=["polymarket", "kalshi"],
        dry_run=False,
        max_events=int(max_events),
        max_seconds=int(max_seconds),
        kalshi_all_market_poll=False,
        kalshi_poll_interval_seconds=None,
        kalshi_trade_poll_limit=None,
        kalshi_trade_poll_max_pages=None,
        log_file=None,
    )
    return int(cmd_ingest(args))


def _heartbeat_health() -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    from pmfi.health import HEARTBEAT_PATH, read_heartbeat

    payload = read_heartbeat(HEARTBEAT_PATH)
    if not payload:
        return {"status": "UNKNOWN", "intake_allowed": False}, {}, "missing"
    operational = payload.get("operational_health") or {
        "status": "UNKNOWN",
        "intake_allowed": False,
    }
    venues = payload.get("venues") or {}
    return dict(operational), {str(k): dict(v) for k, v in venues.items()}, str(HEARTBEAT_PATH)


async def run_dq4_live_trial(
    pool: Any,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    max_seconds: int,
    max_events: int,
    db_url: str,
) -> dict[str, Any]:
    manifest_path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest = load_dq4_manifest(manifest_path)
    cfg = load_config()
    window_start = await _db_now(pool)
    rc = await asyncio.to_thread(
        _run_bounded_live_ingest,
        max_seconds=max_seconds,
        max_events=max_events,
    )
    window_end = await _db_now(pool)
    operational_health, status_map, health_source = _heartbeat_health()
    measurements = await collect_dq4_window_measurements(
        pool,
        window_start,
        window_end,
        required_venues=list(manifest["venues"]),
        dead_letter_rate_threshold=float(
            getattr(cfg.ingestion, "dead_letter_rate_p1_threshold_fraction", 0.05)
        ),
        min_per_venue=int((manifest.get("thresholds") or {}).get("min_per_venue", 1)),
        operational_health=operational_health,
        status_map=status_map,
    )
    measurements["live_command_return_code"] = rc
    measurements["bounds"] = {
        "max_seconds": int(max_seconds),
        "max_events": int(max_events),
    }
    measurements["health_snapshot_source"] = health_source

    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {
            "live_barrier": "PROVEN_BOUNDED_LIVE",
            "long_horizon_soak": "ACCEPTED_DEBT",
            "known_answer": "NOT_APPLICABLE_LIVE",
        },
        "repository": {
            "remote": scrubbed_git_remote(_git_value),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_live_trial",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": measurements["postgres_version"],
            "schema_version": schema_fingerprint(ROOT / "sql"),
            "environment": "bounded_live_read_only",
            "db_url_host_only": "provided" if db_url else "missing",
        },
        "time": {
            "started_at": measurements["window_start"],
            "ended_at": measurements["window_end"],
            "input_bounds": {
                "window_start": measurements["window_start"],
                "window_end": measurements["window_end"],
            },
        },
        "expected_truth": {
            "manifest": manifest_path.relative_to(ROOT).as_posix(),
            "artifact_hash": _sha256_path(manifest_path),
            "structural_only": True,
            "known_answer_counts": "not_applicable_live",
        },
        "evidence": {
            "required_facets": manifest["required_facets"],
            "actual_facets": [],
            "deferred_facets": [
                item["facet"] for item in manifest["manual_deferred_facets"]
            ],
            "commands": [
                "PMFI_ENABLE_LIVE=1 pmfi ingest --venue polymarket --venue kalshi --max-seconds <N> --max-events <N>",
            ],
            "artifacts": [manifest_path.relative_to(ROOT).as_posix()],
            "artifact_hashes": [_sha256_path(manifest_path)],
            "documented_non_trade_skip_types": manifest["documented_non_trade_skip_types"],
        },
        "measurements": measurements,
        "pass_invariants": {},
        "fail_conditions": [],
        "blocker_or_inconclusive_reason": None,
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": manifest["manual_deferred_facets"],
        "next_action": "orchestrator_verify_pr",
    }
    measurements["no_secrets_in_fixtures_logs_or_evidence"] = not evidence_contains_secret(
        manifest_path,
        evidence,
    )
    pass_invariants = evaluate_dq4_pass_invariants(measurements)
    actual_facets: list[str] = []
    if measurements["raw_event_count"] > 0:
        actual_facets.append("POSTGRES_INTEGRATION")
    if pass_invariants["both_venues_captured"]:
        actual_facets.append("DUAL_VENUE")
    if rc == 0 and measurements["window_duration_seconds"] <= max_seconds + 5:
        actual_facets.append("BOUNDED_LIVE")
    evidence["evidence"]["actual_facets"] = actual_facets
    evidence["pass_invariants"] = pass_invariants
    if rc != 0:
        evidence["fail_conditions"].append(f"bounded live ingest returned {rc}")
    if not all(pass_invariants.values()):
        evidence["fail_conditions"].extend(
            key for key, value in pass_invariants.items() if not value
        )
    if evidence["fail_conditions"]:
        evidence["outcome"] = "FAIL"
    return evidence
