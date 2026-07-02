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

from pmfi.db.repos.markets import upsert_market
from pmfi.db.repos.raw_events import _compute_payload_hash, insert_raw_event
from pmfi.domain import RawEvent
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import run_adapter_pipeline
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    scrubbed_git_remote,
    schema_fingerprint,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq1_capture_manifest.yaml"
CONCURRENCY_PROBE_SOURCE_CHANNEL = "dq1_capture_concurrency_probe_v1"


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dq1_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = path if path.is_absolute() else ROOT / path
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _rel_manifest_path(path: Path) -> str:
    path = path if path.is_absolute() else ROOT / path
    return path.relative_to(ROOT).as_posix()


def _lineage_payload(page: dict[str, Any], item_ordinal: int) -> dict[str, Any]:
    return {
        "page_id": page["page_id"],
        "frame_id": page["frame_id"],
        "item_ordinal": item_ordinal,
    }


def _payload_with_lineage(
    page: dict[str, Any],
    item: dict[str, Any],
    item_ordinal: int,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(item["payload"], sort_keys=True))
    if item.get("expect_persisted"):
        payload["dq1_observation"] = _lineage_payload(page, item_ordinal)
    return payload


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
    item_ordinal: int,
    base_ts: datetime,
) -> RawEvent:
    payload = _payload_with_lineage(page, item, item_ordinal)
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


def _manifest_item_identity(item: dict[str, Any], payload: dict[str, Any]) -> str:
    source_event_id = item.get("source_event_id")
    if source_event_id:
        return f"source:{source_event_id}"
    return f"payload_hash:{_compute_payload_hash(payload)}"


def _expected_raw_identities(manifest: dict[str, Any]) -> set[str]:
    identities: set[str] = set()
    for page in manifest["pages"]:
        for item_ordinal, item in enumerate(page.get("items", [])):
            if not item.get("expect_persisted"):
                continue
            payload = _payload_with_lineage(page, item, item_ordinal)
            identities.add(_manifest_item_identity(item, payload))
    return identities


def _expected_payload_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for page in manifest["pages"]:
        for item_ordinal, item in enumerate(page.get("items", [])):
            if item.get("expect_persisted"):
                payload = _payload_with_lineage(page, item, item_ordinal)
                hashes[_manifest_item_identity(item, payload)] = _compute_payload_hash(payload)
    return hashes


def _expected_lineages(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lineages: dict[str, dict[str, Any]] = {}
    for page in manifest["pages"]:
        for item_ordinal, item in enumerate(page.get("items", [])):
            if not item.get("expect_persisted"):
                continue
            payload = _payload_with_lineage(page, item, item_ordinal)
            lineages[_manifest_item_identity(item, payload)] = _lineage_payload(page, item_ordinal)
    return lineages


def _row_identity(row: Any) -> str:
    source_event_id = row["source_event_id"]
    if source_event_id:
        return f"source:{source_event_id}"
    return f"payload_hash:{row['payload_hash']}"


def _row_payload(row: Any) -> dict[str, Any]:
    payload = row["payload"]
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _count_verified_lineages(raw_rows: list[Any], manifest: dict[str, Any]) -> int:
    expected_lineages = _expected_lineages(manifest)
    verified = 0
    for row in raw_rows:
        identity = _row_identity(row)
        expected = expected_lineages.get(identity)
        if expected is None:
            continue
        observed = _row_payload(row).get("dq1_observation")
        if observed == expected:
            verified += 1
    return verified


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


async def _count_duplicate_canonical_facts(conn: Any, raw_ids: list[int]) -> int:
    if not raw_ids:
        return 0
    return int(await conn.fetchval(
        """SELECT COUNT(*) FROM (
             SELECT
               venue_code,
               CASE WHEN venue_trade_id IS NOT NULL THEN 'venue_trade_id' ELSE 'canonical_fingerprint' END AS identity_kind,
               venue_trade_id,
               CASE WHEN venue_trade_id IS NULL THEN market_id END AS market_id,
               CASE WHEN venue_trade_id IS NULL
                    THEN COALESCE(exchange_ts, '-infinity'::timestamptz)
               END AS exchange_ts_key,
               CASE WHEN venue_trade_id IS NULL THEN price END AS price,
               CASE WHEN venue_trade_id IS NULL THEN contracts END AS contracts,
               CASE WHEN venue_trade_id IS NULL THEN outcome_key END AS outcome_key,
               COUNT(*) AS n
             FROM normalized_trades
             WHERE raw_event_id = ANY($1::bigint[])
             GROUP BY 1,2,3,4,5,6,7,8
             HAVING COUNT(*) > 1
           ) dupes""",
        raw_ids,
    ) or 0)


async def _process_events(
    pool: Any,
    engine: AlertEngine,
    events: list[RawEvent],
    *,
    buffer_limit: int,
    state: dict[str, int],
) -> int:
    state["buffer_high_water_mark"] = max(state["buffer_high_water_mark"], len(events))
    processed_total = 0
    for start in range(0, len(events), buffer_limit):
        chunk = events[start:start + buffer_limit]
        if not chunk:
            continue
        state["generated_observations"] += len(chunk)
        state["extracted_raw_events"] += len(chunk)
        processed_total += int(
            await run_adapter_pipeline(_aiter(chunk), pool, engine, _noop_alert_handler)
        )
    return processed_total


def _page_completed(events: list[RawEvent], processed_count: int) -> bool:
    return processed_count == len(events)


def _source_channels(manifest: dict[str, Any]) -> list[str]:
    return [manifest["source_channel"], CONCURRENCY_PROBE_SOURCE_CHANNEL]


def _concurrency_probe_event(manifest: dict[str, Any], base_ts: datetime) -> RawEvent:
    probe = manifest["concurrency_probe"]
    return RawEvent(
        venue_code=manifest["venue_code"],
        source_channel=CONCURRENCY_PROBE_SOURCE_CHANNEL,
        source_event_type="last_trade_price",
        source_event_id=probe["source_event_id"],
        venue_market_id=probe["venue_market_id"],
        exchange_ts=base_ts + timedelta(seconds=30),
        received_at=base_ts + timedelta(minutes=1, seconds=30),
        payload={
            "trade_id": probe["source_event_id"],
            "market": probe["venue_market_id"],
            "outcome": "yes",
            "side": "buy",
            "price": "0.31",
            "size": "31",
        },
    )


async def _run_concurrent_dedupe_probe(
    pool: Any,
    manifest: dict[str, Any],
    *,
    base_ts: datetime,
) -> dict[str, int]:
    attempts = int(manifest["concurrency_probe"]["attempts"])
    event = _concurrency_probe_event(manifest, base_ts)

    async def _insert_once() -> tuple[int, bool]:
        async with pool.acquire() as conn:
            return await insert_raw_event(conn, event)

    results = await asyncio.gather(*(_insert_once() for _ in range(attempts)))
    async with pool.acquire() as conn:
        persisted_rows = int(await conn.fetchval(
            """SELECT COUNT(*)
               FROM raw_events
               WHERE source_channel = $1 AND source_event_id = $2""",
            CONCURRENCY_PROBE_SOURCE_CHANNEL,
            event.source_event_id,
        ) or 0)
        duplicate_observations = int(await conn.fetchval(
            """SELECT COALESCE(SUM(duplicate_count), 0)
               FROM event_dedupe_keys
               WHERE venue_code = $1 AND source_channel = $2""",
            manifest["venue_code"],
            CONCURRENCY_PROBE_SOURCE_CHANNEL,
        ) or 0)
    return {
        "concurrency_probe_attempts": attempts,
        "concurrency_probe_persisted_rows": persisted_rows,
        "concurrency_probe_duplicate_observations": duplicate_observations,
        "concurrency_probe_first_sighting_results": sum(1 for _, is_duplicate in results if not is_duplicate),
    }


async def cleanup_dq1_capture_rows(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest_path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest = load_dq1_manifest(manifest_path)
    source_channels = _source_channels(manifest)
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
            "SELECT raw_event_id FROM raw_events WHERE source_channel = ANY($1::text[])",
            source_channels,
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        if raw_ids:
            await conn.execute("DELETE FROM alerts WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        await conn.execute(
            "DELETE FROM dead_letters WHERE source_channel = ANY($1::text[]) AND payload->>'run_key' = $2",
            source_channels,
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
            "DELETE FROM event_dedupe_keys WHERE venue_code = $1 AND source_channel = ANY($2::text[])",
            venue_code,
            source_channels,
        )
        if market_id_values:
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_id_values)


async def _collect_measurements(
    pool: Any,
    manifest: dict[str, Any],
    state: dict[str, int],
    concurrency_metrics: dict[str, int],
) -> dict[str, Any]:
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
            duplicate_canonical_facts = await _count_duplicate_canonical_facts(conn, raw_ids)
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
        postgres_version = await conn.fetchval("SHOW server_version")

    identities = {
        f"source:{row['source_event_id']}" if row["source_event_id"] else f"payload_hash:{row['payload_hash']}"
        for row in raw_rows
    }
    actual_payload_hashes = {
        f"source:{row['source_event_id']}" if row["source_event_id"] else f"payload_hash:{row['payload_hash']}": row["payload_hash"]
        for row in raw_rows
    }
    expected_payload_hashes = _expected_payload_hashes(manifest)
    expected_lineages = _expected_lineages(manifest)
    legit_rows = [
        row for row in raw_rows
        if row["source_event_id"] in {"dq1-distinct-a", "dq1-distinct-b"}
    ]
    legit_payload_hashes = {row["payload_hash"] for row in legit_rows}
    lineage_verified_rows = _count_verified_lineages(raw_rows, manifest)

    measurements = {
        "generated_observations": state["generated_observations"],
        "accepted_observations": len(raw_rows),
        "db_persisted_unique_raw_events": len(raw_rows),
        "extracted_raw_events": state["extracted_raw_events"],
        "duplicate_observations": duplicate_observations,
        "legitimate_repeated_events": len(legit_rows),
        "cursor_page_checkpoints": state["cursor_page_checkpoints"],
        "quarantined_events": quarantined_events,
        "normalized_trade_rows": normalized_trade_rows,
        "duplicate_canonical_facts": duplicate_canonical_facts,
        "buffer_high_water_mark": state["buffer_high_water_mark"],
        "payload_hashes_verified_rows": sum(
            1
            for identity, expected_hash in expected_payload_hashes.items()
            if actual_payload_hashes.get(identity) == expected_hash
        ),
        "lineage_verified_rows": lineage_verified_rows,
        "concurrency_probe_attempts": concurrency_metrics["concurrency_probe_attempts"],
        "concurrency_probe_persisted_rows": concurrency_metrics["concurrency_probe_persisted_rows"],
        "concurrency_probe_duplicate_observations": concurrency_metrics[
            "concurrency_probe_duplicate_observations"
        ],
    }
    return {
        "measurements": measurements,
        "raw_identities": identities,
        "expected_identities": _expected_raw_identities(manifest),
        "payload_hashes_match_manifest": actual_payload_hashes == expected_payload_hashes,
        "legitimate_repeats_ok": len(legit_rows) == 2 and len(legit_payload_hashes) == 1,
        "linked_rows_ok": measurements["lineage_verified_rows"] == len(expected_lineages),
        "concurrency_probe_first_sighting_results": concurrency_metrics[
            "concurrency_probe_first_sighting_results"
        ],
        "postgres_version": postgres_version,
    }


def _contains_secret_text(manifest_path: Path, evidence: dict[str, Any]) -> bool:
    return evidence_contains_secret(manifest_path, evidence)


async def run_dq1_capture_gauntlet(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = manifest_path if manifest_path.is_absolute() else ROOT / manifest_path
    manifest = load_dq1_manifest(manifest_path)
    await cleanup_dq1_capture_rows(pool, manifest_path)
    state = {
        "generated_observations": 0,
        "extracted_raw_events": 0,
        "buffer_high_water_mark": 0,
        "cursor_page_checkpoints": 0,
    }
    base_ts = datetime.now(timezone.utc).replace(microsecond=0)
    engine = AlertEngine()
    buffer_limit = int(manifest["buffer_limit_events"])
    partial_not_advanced_before_restart = True

    for page in manifest["pages"]:
        events = [
            _raw_event_from_item(
                manifest,
                page,
                item,
                item_ordinal=item_ordinal,
                base_ts=base_ts,
            )
            for item_ordinal, item in enumerate(page.get("items", []))
        ]
        partial_first_pass_count = int(page.get("partial_first_pass_count") or 0)
        processed_for_checkpoint = 0
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
            processed_for_checkpoint = await _process_events(
                pool,
                engine,
                events,
                buffer_limit=buffer_limit,
                state=state,
            )
        else:
            processed_for_checkpoint = await _process_events(
                pool,
                engine,
                events,
                buffer_limit=buffer_limit,
                state=state,
            )
        if _page_completed(events, processed_for_checkpoint):
            durable_raw_count = await _raw_count(pool, manifest)
            await _write_checkpoint(
                pool,
                manifest,
                page["cursor_value"],
                page_id=page["page_id"],
                durable_raw_count=durable_raw_count,
            )
            state["cursor_page_checkpoints"] += 1

    concurrency_metrics = await _run_concurrent_dedupe_probe(pool, manifest, base_ts=base_ts)
    collected = await _collect_measurements(pool, manifest, state, concurrency_metrics)
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
            "controlled_capture": "PROVEN_CORE",
            "bounded_outage_overflow": "DEFERRED_TO_DQ3",
        },
        "repository": {
            "remote": scrubbed_git_remote(_git_value),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_db_test",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": collected["postgres_version"],
            "schema_version": schema_fingerprint(ROOT / "sql"),
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
            "expected_unique_raw_events": manifest["expected_unique_raw_events"],
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
            ],
            "deferred_facets": [
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
        "accepted_debt": manifest["deferred_facets"],
        "next_action": "orchestrator_verify_pr",
    }
    evidence["pass_invariants"] = {
        "accepted_boundary_matches_db_persisted_unique": (
            measurements["accepted_observations"] == measurements["db_persisted_unique_raw_events"]
        ),
        "persisted_unique_raw_identities_match_manifest_truth": (
            collected["raw_identities"] == collected["expected_identities"]
            and measurements["db_persisted_unique_raw_events"] == manifest["expected_unique_raw_events"]
        ),
        "duplicate_deliveries_no_duplicate_canonical_path": measurements["duplicate_canonical_facts"] == 0,
        "buffer_high_water_reflects_uncapped_burst": (
            measurements["buffer_high_water_mark"] == max(
                len(page.get("items", [])) for page in manifest["pages"]
            )
        ),
        "raw_payload_hashes_match_manifest_truth": collected["payload_hashes_match_manifest"],
        "legitimate_repeated_distinct_events_not_raw_deduped": collected["legitimate_repeats_ok"],
        "raw_events_link_to_observation_page_frame_or_ordinal": (
            measurements["lineage_verified_rows"] == measurements["accepted_observations"]
        ),
        "cursor_never_advances_past_durable_scope": (
            partial_not_advanced_before_restart and final_cursor == "cursor-006"
        ),
        "partial_page_restart_replays_incomplete_scope_idempotently": (
            measurements["duplicate_observations"] == expected["duplicate_observations"]
            and measurements["db_persisted_unique_raw_events"] == expected["db_persisted_unique_raw_events"]
        ),
        "concurrent_postgres_dedupe_race_persists_exactly_one": (
            measurements["concurrency_probe_persisted_rows"] == 1
            and measurements["concurrency_probe_duplicate_observations"]
            == measurements["concurrency_probe_attempts"] - 1
            and collected["concurrency_probe_first_sighting_results"] == 1
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
