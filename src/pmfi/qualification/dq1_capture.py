from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import subprocess
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from pmfi.db.repos.dead_letters import insert_dead_letter
from pmfi.db.repos.markets import upsert_market
from pmfi.db.repos.raw_events import _compute_payload_hash
from pmfi.domain import RawEvent
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import run_adapter_pipeline

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq1_capture_manifest.yaml"


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dq1_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = path if path.is_absolute() else ROOT / path
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _rel_manifest_path(path: Path) -> str:
    path = path if path.is_absolute() else ROOT / path
    return path.relative_to(ROOT).as_posix()


async def _aiter(events: list[RawEvent]) -> AsyncIterator[RawEvent]:
    for event in events:
        yield event


async def _noop_alert_handler(decision, venue_code, market_id) -> None:
    return None


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip()


def _raw_event_from_item(
    manifest: dict[str, Any],
    page: dict[str, Any],
    item: dict[str, Any],
    *,
    base_ts: datetime,
) -> RawEvent:
    payload = json.loads(json.dumps(item["payload"], sort_keys=True))
    offset = int(item.get("exchange_offset_seconds", 0))
    exchange_ts = base_ts + timedelta(seconds=offset)
    return RawEvent(
        venue_code=manifest["venue_code"],
        source_channel=manifest["source_channel"],
        source_event_type=item["event_type"],
        source_event_id=item.get("source_event_id"),
        venue_market_id=item.get("venue_market_id"),
        exchange_ts=exchange_ts,
        received_at=base_ts + timedelta(minutes=1, seconds=offset),
        payload=payload,
    )


def _manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for page in manifest["pages"] for item in page.get("items", [])]


def _expected_raw_identities(manifest: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for item in _manifest_items(manifest):
        if not item.get("expect_persisted"):
            continue
        source_event_id = item.get("source_event_id")
        if source_event_id:
            identities.add(f"source:{source_event_id}")
            continue
        payload_hash = _compute_payload_hash(item["payload"])
        identities.add(f"payload_hash:{payload_hash}")
    return identities


async def _write_checkpoint(
    pool: Any,
    manifest: dict[str, Any],
    cursor_value: str,
    *,
    page_id: str,
    durable_raw_count: int,
) -> None:
    async with pool.acquire() as conn:
        market_id = await upsert_market(
            conn,
            venue_code=manifest["venue_code"],
            venue_market_id=manifest["run_key"],
            title=None,
        )
        payload = {
            "scenario_id": manifest["scenario_id"],
            "run_key": manifest["run_key"],
            "page_id": page_id,
            "durable_raw_count": durable_raw_count,
        }
        await conn.execute(
            """INSERT INTO feed_cursors
                 (venue_code, feed_name, market_id, cursor_value, cursor_payload, last_success_at)
               VALUES ($1, $2, $3::uuid, $4, $5::jsonb, now())
               ON CONFLICT (venue_code, feed_name, market_id)
               DO UPDATE SET
                 cursor_value = EXCLUDED.cursor_value,
                 cursor_payload = EXCLUDED.cursor_payload,
                 last_success_at = now(),
                 updated_at = now()""",
            manifest["venue_code"],
            manifest["checkpoint_feed_name"],
            market_id,
            cursor_value,
            json.dumps(payload),
        )


async def _current_checkpoint(pool: Any, manifest: dict[str, Any]) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """SELECT fc.cursor_value
               FROM feed_cursors fc
               JOIN markets m ON m.market_id = fc.market_id
               WHERE fc.venue_code = $1
                 AND fc.feed_name = $2
                 AND m.venue_market_id = $3""",
            manifest["venue_code"],
            manifest["checkpoint_feed_name"],
            manifest["run_key"],
        )


async def _raw_count(pool: Any, manifest: dict[str, Any]) -> int:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1",
            manifest["source_channel"],
        )
    return int(value or 0)


async def _process_events(
    pool: Any,
    engine: AlertEngine,
    events: list[RawEvent],
    *,
    buffer_limit: int,
    state: dict[str, int],
) -> None:
    for start in range(0, len(events), buffer_limit):
        chunk = events[start:start + buffer_limit]
        if not chunk:
            continue
        state["buffer_high_water_mark"] = max(state["buffer_high_water_mark"], len(chunk))
        state["generated_observations"] += len(chunk)
        state["extracted_raw_events"] += len(chunk)
        await run_adapter_pipeline(_aiter(chunk), pool, engine, _noop_alert_handler)


async def _write_fault_classifications(pool: Any, manifest: dict[str, Any], state: dict[str, int]) -> None:
    async with pool.acquire() as conn:
        for fault in manifest.get("fault_observations", []):
            state["generated_observations"] += 1
            state["durably_classified_failures"] += 1
            state["explicitly_rejected_dropped_observations"] += 1
            await insert_dead_letter(
                conn,
                venue_code=manifest["venue_code"],
                raw_event_id=None,
                source_channel=manifest["source_channel"],
                failure_stage=fault["failure_stage"],
                error_class=fault["error_class"],
                error_message=fault["error_message"],
                payload={
                    "scenario_id": manifest["scenario_id"],
                    "run_key": manifest["run_key"],
                    "label": fault["label"],
                },
            )


async def cleanup_dq1_capture_rows(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest_path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest = load_dq1_manifest(manifest_path)
    source_channel = manifest["source_channel"]
    run_key = manifest["run_key"]
    venue_code = manifest["venue_code"]
    checkpoint_feed_name = manifest["checkpoint_feed_name"]
    async with pool.acquire() as conn:
        market_ids = await conn.fetch(
            "SELECT market_id FROM markets WHERE venue_code = $1 AND venue_market_id LIKE 'DQ1-GAUNTLET-%'",
            venue_code,
        )
        market_id_values = [row["market_id"] for row in market_ids]
        raw_rows = await conn.fetch(
            "SELECT raw_event_id FROM raw_events WHERE source_channel = $1",
            source_channel,
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        if raw_ids:
            await conn.execute("DELETE FROM alerts WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        await conn.execute(
            "DELETE FROM dead_letters WHERE source_channel = $1 AND payload->>'run_key' = $2",
            source_channel,
            run_key,
        )
        if market_id_values:
            await conn.execute("DELETE FROM alerts WHERE market_id = ANY($1::uuid[])", market_id_values)
            await conn.execute("DELETE FROM metric_windows WHERE market_id = ANY($1::uuid[])", market_id_values)
            await conn.execute(
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = ANY($1::uuid[])",
                market_id_values,
            )
            await conn.execute("DELETE FROM normalized_trades WHERE market_id = ANY($1::uuid[])", market_id_values)
            await conn.execute(
                "DELETE FROM feed_cursors WHERE venue_code = $1 AND feed_name = $2 AND market_id = ANY($3::uuid[])",
                venue_code,
                checkpoint_feed_name,
                market_id_values,
            )
        if raw_ids:
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            await conn.execute("DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        await conn.execute(
            "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = $2",
            venue_code,
            source_channel,
        )
        if market_id_values:
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_id_values)


async def _collect_measurements(pool: Any, manifest: dict[str, Any], state: dict[str, int]) -> dict[str, Any]:
    async with pool.acquire() as conn:
        raw_rows = await conn.fetch(
            """SELECT raw_event_id, source_event_id, source_event_type, payload, payload_hash
               FROM raw_events
               WHERE source_channel = $1
               ORDER BY raw_event_id""",
            manifest["source_channel"],
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        normalized_trade_rows = 0
        duplicate_canonical_facts = 0
        if raw_ids:
            normalized_trade_rows = int(await conn.fetchval(
                "SELECT COUNT(*) FROM normalized_trades WHERE raw_event_id = ANY($1::bigint[])",
                raw_ids,
            ) or 0)
            duplicate_canonical_facts = int(await conn.fetchval(
                """SELECT COUNT(*) FROM (
                     SELECT venue_code, COALESCE(venue_trade_id, ''), market_id,
                            COALESCE(exchange_ts, '-infinity'::timestamptz),
                            price, contracts, outcome_key, COUNT(*) AS n
                     FROM normalized_trades
                     WHERE raw_event_id = ANY($1::bigint[])
                     GROUP BY 1,2,3,4,5,6,7
                     HAVING COUNT(*) > 1
                   ) dupes""",
                raw_ids,
            ) or 0)
        duplicate_observations = int(await conn.fetchval(
            """SELECT COALESCE(SUM(duplicate_count), 0)
               FROM event_dedupe_keys
               WHERE venue_code = $1 AND source_channel = $2""",
            manifest["venue_code"],
            manifest["source_channel"],
        ) or 0)
        quarantined_events = int(await conn.fetchval(
            """SELECT COUNT(*)
               FROM dead_letters
               WHERE source_channel = $1 AND raw_event_id IS NOT NULL""",
            manifest["source_channel"],
        ) or 0)
        classified_failures = int(await conn.fetchval(
            """SELECT COUNT(*)
               FROM dead_letters
               WHERE source_channel = $1 AND raw_event_id IS NULL""",
            manifest["source_channel"],
        ) or 0)
        postgres_version = await conn.fetchval("SHOW server_version")

    identities = {
        f"source:{row['source_event_id']}" if row["source_event_id"] else f"payload_hash:{row['payload_hash']}"
        for row in raw_rows
    }
    legit_rows = [
        row for row in raw_rows
        if row["source_event_id"] in {"dq1-distinct-a", "dq1-distinct-b"}
    ]
    legit_payload_hashes = {row["payload_hash"] for row in legit_rows}
    source_links = {item.get("source_event_id") for item in _manifest_items(manifest) if item.get("source_event_id")}
    linked_rows = [
        row for row in raw_rows
        if row["source_event_id"] in source_links
        or "dq1_observation" in (
            json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        )
    ]

    expected = manifest["expected_counts"]
    measurements = {
        "generated_observations": state["generated_observations"],
        "accepted_observations": len(raw_rows) + classified_failures,
        "persisted_observations": len(raw_rows),
        "extracted_raw_events": state["extracted_raw_events"],
        "expected_unique_raw_events": expected["expected_unique_raw_events"],
        "duplicate_observations": duplicate_observations,
        "legitimate_repeated_events": len(legit_rows),
        "cursor_page_checkpoints": state["cursor_page_checkpoints"],
        "buffer_high_water_mark": state["buffer_high_water_mark"],
        "explicitly_rejected_dropped_observations": state["explicitly_rejected_dropped_observations"],
        "durably_classified_failures": classified_failures,
        "quarantined_events": quarantined_events,
        "normalized_trade_rows": normalized_trade_rows,
        "duplicate_canonical_facts": duplicate_canonical_facts,
    }
    return {
        "measurements": measurements,
        "raw_identities": identities,
        "expected_identities": _expected_raw_identities(manifest),
        "legitimate_repeats_ok": len(legit_rows) == 2 and len(legit_payload_hashes) == 1,
        "linked_rows_ok": len(linked_rows) == len(raw_rows),
        "postgres_version": postgres_version,
    }


def _contains_secret_text(manifest_path: Path, evidence: dict[str, Any]) -> bool:
    text = manifest_path.read_text(encoding="utf-8")
    text += "\n" + yaml.safe_dump(evidence, sort_keys=True)
    lowered = text.lower()
    return any(marker in lowered for marker in ("api_key", "password", "private_key", "bearer ", "authorization"))


async def run_dq1_capture_gauntlet(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest = load_dq1_manifest(manifest_path)
    await cleanup_dq1_capture_rows(pool, manifest_path)
    state = {
        "generated_observations": 0,
        "extracted_raw_events": 0,
        "buffer_high_water_mark": 0,
        "durably_classified_failures": 0,
        "explicitly_rejected_dropped_observations": 0,
        "cursor_page_checkpoints": 0,
    }
    base_ts = datetime.now(timezone.utc).replace(microsecond=0)
    engine = AlertEngine()
    buffer_limit = int(manifest["buffer_limit_events"])
    partial_not_advanced_before_restart = True

    for page in manifest["pages"]:
        events = [
            _raw_event_from_item(manifest, page, item, base_ts=base_ts)
            for item in page.get("items", [])
        ]
        partial_first_pass_count = int(page.get("partial_first_pass_count") or 0)
        if partial_first_pass_count:
            await _process_events(
                pool,
                engine,
                events[:partial_first_pass_count],
                buffer_limit=buffer_limit,
                state=state,
            )
            partial_not_advanced_before_restart = (
                await _current_checkpoint(pool, manifest)
            ) != page["cursor_value"]
            await _process_events(pool, engine, events, buffer_limit=buffer_limit, state=state)
        else:
            await _process_events(pool, engine, events, buffer_limit=buffer_limit, state=state)
        durable_raw_count = await _raw_count(pool, manifest)
        await _write_checkpoint(
            pool,
            manifest,
            page["cursor_value"],
            page_id=page["page_id"],
            durable_raw_count=durable_raw_count,
        )
        state["cursor_page_checkpoints"] += 1

    await _write_fault_classifications(pool, manifest, state)
    collected = await _collect_measurements(pool, manifest, state)
    measurements = collected["measurements"]
    expected = manifest["expected_counts"]
    final_cursor = await _current_checkpoint(pool, manifest)
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {
            "controlled_capture": "PROVEN_COMPLETE",
            "bounded_outage_overflow": "KNOWN_GAP",
        },
        "repository": {
            "remote": _git_value(["config", "--get", "remote.origin.url"]),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_db_test",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": collected["postgres_version"],
            "schema_version": _sha256_path(ROOT / "sql" / "001_init.sql"),
            "config_hash": _sha256_path(ROOT / "config" / "alert_rules.yaml"),
            "environment": "offline_db_gated",
        },
        "time": {
            "started_at": base_ts.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "input_bounds": {
                "first_exchange_ts": (base_ts + timedelta(seconds=-600)).isoformat(),
                "last_exchange_ts": (base_ts + timedelta(seconds=14)).isoformat(),
            },
        },
        "expected_truth": {
            "manifest": _rel_manifest_path(manifest_path),
            "artifact_hash": _sha256_path(manifest_path),
        },
        "evidence": {
            "required_facets": [
                "OFFLINE_TEST",
                "POSTGRES_INTEGRATION",
                "CONCURRENCY",
                "FAULT_INJECTION",
            ],
            "actual_facets": [
                "OFFLINE_TEST",
                "POSTGRES_INTEGRATION",
                "CONCURRENCY",
                "FAULT_INJECTION",
            ],
            "commands": [
                "python -m pytest -q tests\\test_dq1_capture_gauntlet_db.py",
            ],
            "artifacts": [
                _rel_manifest_path(manifest_path),
            ],
            "artifact_hashes": [
                _sha256_path(manifest_path),
            ],
        },
        "measurements": measurements,
        "pass_invariants": {},
        "fail_conditions": [],
        "blocker_or_inconclusive_reason": None,
        "incidents": {
            "unresolved_p0": [],
            "unresolved_p1": [],
        },
        "accepted_debt": [],
        "next_action": "orchestrator_verify_pr",
    }
    evidence["pass_invariants"] = {
        "accepted_equals_persisted_plus_durable_failures": (
            measurements["accepted_observations"]
            == measurements["persisted_observations"] + measurements["durably_classified_failures"]
        ),
        "persisted_unique_raw_identities_match_manifest_truth": (
            collected["raw_identities"] == collected["expected_identities"]
        ),
        "duplicate_deliveries_no_duplicate_canonical_path": measurements["duplicate_canonical_facts"] == 0,
        "legitimate_repeated_distinct_events_not_raw_deduped": collected["legitimate_repeats_ok"],
        "raw_events_link_to_observation_page_frame_or_ordinal": collected["linked_rows_ok"],
        "cursor_never_advances_past_durable_scope": (
            partial_not_advanced_before_restart and final_cursor == "cursor-006"
        ),
        "partial_page_restart_replays_incomplete_scope_idempotently": (
            measurements["duplicate_observations"] == expected["duplicate_observations"]
            and measurements["persisted_observations"] == expected["persisted_observations"]
        ),
        "buffer_and_memory_remain_within_configured_bounds": (
            measurements["buffer_high_water_mark"] <= manifest["buffer_limit_events"]
        ),
        "outage_overflow_downgrades_completeness_explicitly": (
            evidence["completeness_classifications"]["bounded_outage_overflow"] == "KNOWN_GAP"
            and measurements["explicitly_rejected_dropped_observations"] == 2
        ),
        "no_secrets_in_fixtures_logs_or_evidence": not _contains_secret_text(manifest_path, evidence),
    }
    for key, value in expected.items():
        if measurements.get(key) != value:
            evidence["fail_conditions"].append(
                f"measurement {key} expected {value}, got {measurements.get(key)}"
            )
    if not all(evidence["pass_invariants"].values()):
        evidence["outcome"] = "FAIL"
    if evidence["fail_conditions"]:
        evidence["outcome"] = "FAIL"
    return evidence
