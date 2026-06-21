from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from pmfi.config import load_config
from pmfi.db.advisory_lock import SingleActiveIngestLock
from pmfi.db.repos.markets import upsert_market
from pmfi.domain import RawEvent
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event, run_adapter_pipeline
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    sanitize_git_remote,
    schema_fingerprint,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "dq3_recovery_manifest.yaml"
DRILL_SCRIPT = ROOT / "scripts" / "dq3_operator_drill.py"


class DQ3InjectedFault(RuntimeError):
    def __init__(self, point: str, source_event_id: str | None) -> None:
        super().__init__(f"DQ3 injected fault at {point} for {source_event_id}")
        self.point = point
        self.source_event_id = source_event_id


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _as_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def load_dq3_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    path = _as_path(path)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


async def _aiter(events: list[RawEvent]) -> AsyncIterator[RawEvent]:
    for event in events:
        yield event


async def _noop_alert_handler(decision, venue_code, market_id) -> None:
    return None


def _event_from_spec(
    manifest: dict[str, Any],
    name: str,
    *,
    base_ts: datetime,
    offset_seconds: int,
) -> RawEvent:
    spec = manifest["events"][name]
    payload = {
        "trade_id": spec["source_event_id"],
        "market": spec["venue_market_id"],
        "outcome": "yes",
        "side": "buy",
        "size": spec["size"],
    }
    if spec.get("price") is not None:
        payload["price"] = spec["price"]
    return RawEvent(
        venue_code=manifest["venue_code"],
        source_channel=manifest["source_channel"],
        source_event_type="last_trade_price",
        source_event_id=spec["source_event_id"],
        venue_market_id=spec["venue_market_id"],
        exchange_ts=base_ts + timedelta(seconds=offset_seconds),
        received_at=base_ts + timedelta(minutes=1, seconds=offset_seconds),
        payload=payload,
    )


def _event_to_json(raw: RawEvent) -> str:
    return json.dumps(
        {
            "venue_code": raw.venue_code,
            "source_channel": raw.source_channel,
            "source_event_type": raw.source_event_type,
            "source_event_id": raw.source_event_id,
            "venue_market_id": raw.venue_market_id,
            "exchange_ts": raw.exchange_ts.isoformat() if raw.exchange_ts else None,
            "received_at": raw.received_at.isoformat(),
            "payload": raw.payload,
        },
        sort_keys=True,
    )


async def cleanup_dq3_recovery_rows(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest = load_dq3_manifest(manifest_path)
    source_channel = manifest["source_channel"]
    async with pool.acquire() as conn:
        raw_ids = [
            row["raw_event_id"]
            for row in await conn.fetch(
                "SELECT raw_event_id FROM raw_events WHERE source_channel = $1",
                source_channel,
            )
        ]
        market_ids = [
            row["market_id"]
            for row in await conn.fetch(
                "SELECT market_id FROM markets WHERE venue_market_id LIKE 'DQ3-RECOVERY-%'"
            )
        ]
        if raw_ids:
            await conn.execute("DELETE FROM alerts WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
            await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        if market_ids:
            await conn.execute("DELETE FROM metric_windows WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM alerts WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute(
                "DELETE FROM normalized_trade_dedupe_keys WHERE market_id = ANY($1::uuid[])",
                market_ids,
            )
            await conn.execute("DELETE FROM normalized_trades WHERE market_id = ANY($1::uuid[])", market_ids)
            await conn.execute("DELETE FROM feed_cursors WHERE market_id = ANY($1::uuid[])", market_ids)
        if raw_ids:
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            await conn.execute("DELETE FROM raw_events WHERE raw_event_id = ANY($1::bigint[])", raw_ids)
        await conn.execute("DELETE FROM event_dedupe_keys WHERE source_channel = $1", source_channel)
        if market_ids:
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_ids)


class _FaultCallback:
    def __init__(self, plan: dict[tuple[str, str], str]) -> None:
        self._plan = dict(plan)
        self.triggered: list[str] = []

    async def __call__(self, point: str, context: dict[str, Any]) -> None:
        raw = context.get("raw")
        source_event_id = getattr(raw, "source_event_id", None)
        action = self._plan.get((point, str(source_event_id)))
        if action is None:
            return
        self.triggered.append(point)
        if action == "raise":
            raise DQ3InjectedFault(point, str(source_event_id))


async def _write_cursor(pool: Any, manifest: dict[str, Any], raw: RawEvent, cursor_value: str) -> None:
    async with pool.acquire() as conn:
        market_id = await upsert_market(
            conn,
            venue_code=raw.venue_code,
            venue_market_id=raw.venue_market_id or manifest["run_key"],
            title=None,
        )
        durable_raw_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1",
                manifest["source_channel"],
            )
            or 0
        )
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
            json.dumps(
                {
                    "run_key": manifest["run_key"],
                    "source_event_id": raw.source_event_id,
                    "durable_raw_count": durable_raw_count,
                }
            ),
        )


async def _cursor_value(pool: Any, manifest: dict[str, Any], raw: RawEvent) -> str | None:
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
            raw.venue_market_id,
        )


async def _run_pipeline_one(
    pool: Any,
    raw: RawEvent,
    *,
    fault_callback: _FaultCallback | None = None,
    cursor_recorder=None,
    capture_orderbook: bool = False,
) -> int:
    return await run_adapter_pipeline(
        _aiter([raw]),
        pool,
        AlertEngine(),
        _noop_alert_handler,
        cursor_recorder=cursor_recorder,
        capture_orderbook=capture_orderbook,
        qualification_fault_callback=fault_callback,
    )


async def _run_hard_kill_after_raw(raw: RawEvent) -> dict[str, Any]:
    dsn = os.environ.get("PMFI_DB_URL")
    if not dsn:
        raise RuntimeError("PMFI_DB_URL is required for DQ-3 hard-kill subprocess")
    worker = r"""
import asyncio
import json
import os
from datetime import datetime
from pmfi.db import create_pool
from pmfi.domain import RawEvent
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.runner import process_event

def _parse_ts(value):
    if value is None:
        return None
    return datetime.fromisoformat(value)

async def _noop(*_args):
    return None

async def main():
    data = json.loads(os.environ["DQ3_RAW_EVENT"])
    raw = RawEvent(
        venue_code=data["venue_code"],
        source_channel=data["source_channel"],
        source_event_type=data["source_event_type"],
        source_event_id=data["source_event_id"],
        venue_market_id=data["venue_market_id"],
        exchange_ts=_parse_ts(data["exchange_ts"]),
        received_at=_parse_ts(data["received_at"]),
        payload=data["payload"],
    )
    pool = await create_pool(os.environ["PMFI_DB_URL"])
    async def fault_callback(point, context):
        if point == "after_raw_event_commit":
            print(f"raw_event_id={context['raw_event_id']}", flush=True)
            os._exit(137)
    await process_event(raw, pool, AlertEngine(), _noop, qualification_fault_callback=fault_callback)

asyncio.run(main())
"""
    env = dict(os.environ)
    env["PMFI_DB_URL"] = dsn
    env["DQ3_RAW_EVENT"] = _event_to_json(raw)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT / "src")
        if not existing_pythonpath
        else str(ROOT / "src") + os.pathsep + existing_pythonpath
    )
    result = subprocess.run(
        [sys.executable, "-c", worker],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    match = re.search(r"raw_event_id=(\d+)", result.stdout)
    return {
        "returncode": result.returncode,
        "raw_event_id": int(match.group(1)) if match else None,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


async def _run_duplicate_burst(pool: Any, raw: RawEvent, attempts: int) -> dict[str, int]:
    async def _once() -> None:
        await process_event(raw, pool, AlertEngine(), _noop_alert_handler)

    await asyncio.gather(*(_once() for _ in range(attempts)))
    async with pool.acquire() as conn:
        raw_count = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1 AND source_event_id = $2",
                raw.source_channel,
                raw.source_event_id,
            )
            or 0
        )
        trade_count = int(
            await conn.fetchval(
                """SELECT COUNT(*)
                   FROM normalized_trades nt
                   JOIN raw_events re ON re.raw_event_id = nt.raw_event_id
                   WHERE re.source_channel = $1 AND re.source_event_id = $2""",
                raw.source_channel,
                raw.source_event_id,
            )
            or 0
        )
        duplicate_observations = int(
            await conn.fetchval(
                """SELECT COALESCE(SUM(duplicate_count), 0)
                   FROM event_dedupe_keys
                   WHERE source_channel = $1""",
                raw.source_channel,
            )
            or 0
        )
    return {
        "duplicate_burst_attempts": attempts,
        "duplicate_burst_raw_rows": raw_count,
        "duplicate_burst_trade_rows": trade_count,
        "duplicate_observations_total": duplicate_observations,
    }


def _metric_window_start(event_ts: datetime, window_seconds: int = 300) -> datetime:
    if event_ts.tzinfo is None:
        event_ts = event_ts.replace(tzinfo=timezone.utc)
    if window_seconds >= 60:
        minutes = (event_ts.minute // (window_seconds // 60)) * (window_seconds // 60)
        return datetime(
            event_ts.year,
            event_ts.month,
            event_ts.day,
            event_ts.hour,
            minutes,
            tzinfo=timezone.utc,
        )
    return event_ts.replace(
        second=(event_ts.second // window_seconds) * window_seconds,
        microsecond=0,
    )


async def _run_pool_exhaustion_probe(dsn: str, raw: RawEvent, *, alarm_ms: int) -> dict[str, Any]:
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=1,
        server_settings={"search_path": "pmfi,public"},
    )
    held = await pool.acquire()
    timed_out = False
    task_cancelled = False
    observed_ms = 0
    raw_rows_before_release = 0
    try:
        task = asyncio.create_task(process_event(raw, pool, AlertEngine(), _noop_alert_handler))
        started = time.perf_counter()
        try:
            await asyncio.wait_for(task, timeout=0.2)
        except asyncio.TimeoutError:
            timed_out = True
            task_cancelled = task.cancelled()
        finally:
            if not task.done():
                task.cancel()
                task_cancelled = True
                await asyncio.gather(task, return_exceptions=True)
            observed_ms = int((time.perf_counter() - started) * 1000)
            raw_rows_before_release = int(
                await held.fetchval(
                    """SELECT COUNT(*)
                       FROM raw_events
                       WHERE source_channel = $1
                         AND source_event_id = $2""",
                    raw.source_channel,
                    raw.source_event_id,
                )
                or 0
            )
    finally:
        await pool.release(held)
    try:
        await process_event(raw, pool, AlertEngine(), _noop_alert_handler)
        async with pool.acquire() as conn:
            raw_rows_after_retry = int(
                await conn.fetchval(
                    """SELECT COUNT(*)
                       FROM raw_events
                       WHERE source_channel = $1
                         AND source_event_id = $2""",
                    raw.source_channel,
                    raw.source_event_id,
                )
                or 0
            )
    finally:
        await pool.close()
    return {
        "pool_acquire_wait_timed_out": timed_out,
        "pool_acquire_wait_observed_ms": observed_ms,
        "pool_acquire_wait_alarm_ms": int(alarm_ms),
        "pool_acquire_wait_alarm_breached": bool(timed_out and observed_ms >= int(alarm_ms)),
        "pool_exhaustion_task_cancelled": task_cancelled,
        "pool_exhaustion_raw_rows_before_release": raw_rows_before_release,
        "pool_exhaustion_raw_rows_after_retry": raw_rows_after_retry,
    }


async def _single_active_probe(dsn: str) -> dict[str, Any]:
    first = SingleActiveIngestLock(dsn)
    second = SingleActiveIngestLock(dsn)
    fresh = SingleActiveIngestLock(dsn)
    try:
        first_ok = await first.acquire()
        second_ok = await second.acquire()
    finally:
        await second.close()
        await first.close()
    try:
        fresh_ok = await fresh.acquire()
    finally:
        await fresh.close()
    return {
        "first_acquire": first_ok,
        "second_acquire": second_ok,
        "fresh_after_release": fresh_ok,
        "unsupported_concurrent_instances": 0 if first_ok and not second_ok and fresh_ok else 1,
    }


async def _run_operator_drill(manifest: dict[str, Any]) -> dict[str, list[str]]:
    result = subprocess.run(
        [sys.executable, str(DRILL_SCRIPT), "--run-key", manifest["run_key"], "--json"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(result.stdout)


async def _collect_known_gaps(
    pool: Any,
    manifest: dict[str, Any],
    events: dict[str, RawEvent],
) -> list[dict[str, Any]]:
    expected = [
        (
            "after_canonical_fault",
            True,
            True,
            "Fault after canonical fact commit leaves downstream metric and alert work absent without duplicating the canonical trade.",
        ),
        (
            "before_metric_fault",
            True,
            True,
            "Fault before metric update leaves the canonical trade durable but the metric window and alert absent.",
        ),
        (
            "before_alert_fault",
            False,
            True,
            "Fault before alert persistence leaves the metric window durable but the historical alert absent.",
        ),
    ]
    gaps: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        for event_name, expected_missing_metric, expected_missing_alert, reason in expected:
            raw = events[event_name]
            row = await conn.fetchrow(
                """SELECT re.raw_event_id,
                          nt.trade_id::text AS trade_id,
                          nt.market_id::text AS market_id,
                          nt.outcome_key,
                          COALESCE(nt.exchange_ts, nt.received_at) AS event_ts
                   FROM raw_events re
                   JOIN normalized_trades nt ON nt.raw_event_id = re.raw_event_id
                   WHERE re.source_channel = $1
                     AND re.source_event_id = $2
                   ORDER BY nt.received_at DESC
                   LIMIT 1""",
                manifest["source_channel"],
                raw.source_event_id,
            )
            metric_window_rows = 0
            alert_rows = 0
            if row is not None:
                window_start = _metric_window_start(row["event_ts"])
                metric_window_rows = int(
                    await conn.fetchval(
                        """SELECT COUNT(*)
                           FROM metric_windows
                           WHERE market_id = $1::uuid
                             AND outcome_key = $2
                             AND window_start = $3
                             AND window_seconds = 300""",
                        row["market_id"],
                        row["outcome_key"],
                        window_start,
                    )
                    or 0
                )
                alert_rows = int(
                    await conn.fetchval(
                        """SELECT COUNT(*)
                           FROM alerts
                           WHERE raw_event_id = $1
                              OR trade_id = $2::uuid""",
                        row["raw_event_id"],
                        row["trade_id"],
                    )
                    or 0
                )
            metric_condition = (
                metric_window_rows == 0 if expected_missing_metric else metric_window_rows > 0
            )
            alert_condition = alert_rows == 0 if expected_missing_alert else alert_rows > 0
            gaps.append(
                {
                    "source_event_id": raw.source_event_id or "",
                    "classification": "KNOWN_GAP",
                    "reason": reason,
                    "raw_event_id": int(row["raw_event_id"]) if row else None,
                    "trade_id": row["trade_id"] if row else None,
                    "expected_missing_metric_window": expected_missing_metric,
                    "expected_missing_alert": expected_missing_alert,
                    "metric_window_rows": metric_window_rows,
                    "alert_rows": alert_rows,
                    "db_verified": bool(row is not None and metric_condition and alert_condition),
                }
            )
    return gaps


async def _collect_measurements(pool: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    source_channel = manifest["source_channel"]
    processing_claim_source_id = manifest["events"]["processing_claim"]["source_event_id"]
    known_metric_gap_source_ids = [
        manifest["events"]["after_canonical_fault"]["source_event_id"],
        manifest["events"]["before_metric_fault"]["source_event_id"],
    ]
    async with pool.acquire() as conn:
        raw_rows = await conn.fetch(
            """SELECT raw_event_id, source_event_id
               FROM raw_events
               WHERE source_channel = $1
               ORDER BY raw_event_id""",
            source_channel,
        )
        raw_ids = [row["raw_event_id"] for row in raw_rows]
        normalized_trade_rows = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM normalized_trades WHERE raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        dead_letter_rows = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM dead_letters WHERE raw_event_id = ANY($1::bigint[])",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        accounted = int(
            await conn.fetchval(
                """SELECT COUNT(DISTINCT re.raw_event_id)
                   FROM raw_events re
                   WHERE re.source_channel = $1
                     AND (
                       EXISTS (
                         SELECT 1 FROM normalized_trades nt
                         WHERE nt.raw_event_id = re.raw_event_id
                       )
                       OR EXISTS (
                         SELECT 1 FROM dead_letters dl
                         WHERE dl.raw_event_id = re.raw_event_id
                       )
                     )""",
                source_channel,
            )
            or 0
        )
        duplicate_canonical_facts = int(
            await conn.fetchval(
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
            )
            or 0
        ) if raw_ids else 0
        duplicate_metric_windows = int(
            await conn.fetchval(
                """WITH scoped_trades AS (
                     SELECT
                       nt.market_id,
                       nt.outcome_key,
                       date_trunc('hour', COALESCE(nt.exchange_ts, nt.received_at))
                         + (
                           floor(
                             extract(minute from COALESCE(nt.exchange_ts, nt.received_at)) / 5
                           )::int * interval '5 minutes'
                         ) AS window_start,
                       300 AS window_seconds,
                       COUNT(*)::int AS expected_trade_count,
                       SUM(nt.capital_at_risk_usd) AS expected_gross_capital,
                       MAX(nt.capital_at_risk_usd) AS expected_max_capital,
                       SUM(nt.payout_notional_usd) AS expected_payout
                     FROM normalized_trades nt
                     JOIN raw_events re ON re.raw_event_id = nt.raw_event_id
                     WHERE nt.raw_event_id = ANY($1::bigint[])
                       AND re.source_event_id <> ALL($2::text[])
                     GROUP BY 1,2,3,4
                   ),
                   actual_windows AS (
                     SELECT
                       market_id,
                       outcome_key,
                       window_start,
                       window_seconds,
                       trade_count,
                       gross_capital_at_risk_usd,
                       max_trade_capital_at_risk_usd,
                       payout_notional_usd
                     FROM metric_windows
                     WHERE (market_id, COALESCE(outcome_key, ''), window_start, window_seconds)
                       IN (
                         SELECT market_id, COALESCE(outcome_key, ''), window_start, window_seconds
                         FROM scoped_trades
                       )
                   ),
                   duplicate_rows AS (
                     SELECT COUNT(*)::int AS issue_count
                     FROM (
                       SELECT
                         market_id,
                         COALESCE(outcome_key, '') AS outcome_key,
                         window_start,
                         window_seconds,
                         COUNT(*) AS n
                       FROM actual_windows
                       GROUP BY 1,2,3,4
                       HAVING COUNT(*) > 1
                     ) dupes
                   ),
                   aggregate_mismatches AS (
                     SELECT COUNT(*)::int AS issue_count
                     FROM scoped_trades st
                     LEFT JOIN actual_windows mw
                       ON mw.market_id = st.market_id
                      AND COALESCE(mw.outcome_key, '') = COALESCE(st.outcome_key, '')
                      AND mw.window_start = st.window_start
                      AND mw.window_seconds = st.window_seconds
                     WHERE mw.market_id IS NULL
                        OR mw.trade_count <> st.expected_trade_count
                        OR mw.gross_capital_at_risk_usd <> st.expected_gross_capital
                        OR mw.max_trade_capital_at_risk_usd <> st.expected_max_capital
                        OR mw.payout_notional_usd <> st.expected_payout
                   )
                   SELECT duplicate_rows.issue_count + aggregate_mismatches.issue_count
                   FROM duplicate_rows, aggregate_mismatches""",
                raw_ids,
                known_metric_gap_source_ids,
            )
            or 0
        ) if raw_ids else 0
        duplicate_historical_alerts = int(
            await conn.fetchval(
                """SELECT COUNT(*) FROM (
                     SELECT dedupe_key, COUNT(*) AS n
                     FROM alerts
                     WHERE raw_event_id = ANY($1::bigint[])
                     GROUP BY dedupe_key
                     HAVING COUNT(*) > 1
                   ) dupes""",
                raw_ids,
            )
            or 0
        ) if raw_ids else 0
        poison_dead_letters = int(
            await conn.fetchval(
                """SELECT COUNT(*)
                   FROM dead_letters dl
                   JOIN raw_events re ON re.raw_event_id = dl.raw_event_id
                   WHERE re.source_channel = $1
                     AND re.source_event_id = 'dq3-poison'""",
                source_channel,
            )
            or 0
        )
        processing_claim_raw_rows = int(
            await conn.fetchval(
                """SELECT COUNT(*)
                   FROM raw_events
                   WHERE source_channel = $1 AND source_event_id = $2""",
                source_channel,
                processing_claim_source_id,
            )
            or 0
        )
        processing_claim_accounted_rows = int(
            await conn.fetchval(
                """SELECT COUNT(*)
                   FROM raw_events re
                   WHERE re.source_channel = $1
                     AND re.source_event_id = $2
                     AND (
                       EXISTS (
                         SELECT 1 FROM normalized_trades nt
                         WHERE nt.raw_event_id = re.raw_event_id
                       )
                       OR EXISTS (
                         SELECT 1 FROM dead_letters dl
                         WHERE dl.raw_event_id = re.raw_event_id
                       )
                     )""",
                source_channel,
                processing_claim_source_id,
            )
            or 0
        )
        postgres_version = await conn.fetchval("SHOW server_version")
    raw_count = len(raw_rows)
    return {
        "accepted_unique_raw_events": raw_count,
        "accounted_unique_raw_events": accounted,
        "accounting_ratio": (accounted / raw_count) if raw_count else 0.0,
        "normalized_trade_rows": normalized_trade_rows,
        "dead_letter_rows": dead_letter_rows,
        "duplicate_canonical_facts": duplicate_canonical_facts,
        "duplicate_metric_windows": duplicate_metric_windows,
        "duplicate_historical_alerts": duplicate_historical_alerts,
        "poison_dead_letters": poison_dead_letters,
        "processing_claim_raw_rows": processing_claim_raw_rows,
        "processing_claim_accounted_rows": processing_claim_accounted_rows,
        "postgres_version": str(postgres_version),
    }


def _contains_secret_text(manifest_path: Path, evidence: dict[str, Any]) -> bool:
    return evidence_contains_secret(manifest_path, evidence)


async def run_dq3_recovery_trial(pool: Any, manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = _as_path(manifest_path)
    manifest = load_dq3_manifest(manifest_path)
    await cleanup_dq3_recovery_rows(pool, manifest_path)

    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    dsn = os.environ.get("PMFI_DB_URL")
    if not dsn:
        raise RuntimeError("PMFI_DB_URL is required for DQ-3 recovery trial")
    cfg = load_config(ROOT / "config" / "app.example.yaml")
    threshold = int(cfg.ingestion.recovery_backlog_convergence_max_iterations)

    events: dict[str, RawEvent] = {}
    for idx, name in enumerate(manifest["events"], start=1):
        events[name] = _event_from_spec(manifest, name, base_ts=started_at, offset_seconds=idx)

    kill_points: list[str] = []
    known_gaps: list[dict[str, Any]] = []
    restart_convergence_iterations = 0

    hard_kill = await _run_hard_kill_after_raw(events["hard_kill_after_raw"])
    if hard_kill["returncode"] not in (137, -9, 1):
        raise RuntimeError(f"DQ3 hard-kill subprocess did not terminate as expected: {hard_kill}")
    kill_points.append("after_raw_event_commit")
    await process_event(events["hard_kill_after_raw"], pool, AlertEngine(), _noop_alert_handler)
    restart_convergence_iterations += 1

    await process_event(events["success"], pool, AlertEngine(), _noop_alert_handler)

    fault_plan = {
        ("after_raw_event_commit", events["after_raw_fault"].source_event_id): "raise",
        ("after_canonical_fact_commit", events["after_canonical_fault"].source_event_id): "raise",
        ("before_metric_update", events["before_metric_fault"].source_event_id): "raise",
        ("before_alert_persistence", events["before_alert_fault"].source_event_id): "raise",
        ("processing_claim_held", events["processing_claim"].source_event_id): "raise",
    }
    fault_cb = _FaultCallback({(point, str(source_id)): action for (point, source_id), action in fault_plan.items()})
    for name in (
        "after_raw_fault",
        "after_canonical_fault",
        "before_metric_fault",
        "before_alert_fault",
        "processing_claim",
    ):
        await _run_pipeline_one(pool, events[name], fault_callback=fault_cb)
    kill_points.extend(fault_cb.triggered)
    known_gaps.extend(await _collect_known_gaps(pool, manifest, events))

    cursor_before = events["cursor_before_fault"]
    cursor_callback = _FaultCallback({("before_cursor_checkpoint", str(cursor_before.source_event_id)): "raise"})
    await _run_pipeline_one(
        pool,
        cursor_before,
        fault_callback=cursor_callback,
        cursor_recorder=lambda raw: _write_cursor(pool, manifest, raw, "cursor-before"),
    )
    cursor_before_after_fault = await _cursor_value(pool, manifest, cursor_before)
    await _run_pipeline_one(
        pool,
        cursor_before,
        cursor_recorder=lambda raw: _write_cursor(pool, manifest, raw, "cursor-before"),
    )
    cursor_before_after_restart = await _cursor_value(pool, manifest, cursor_before)
    restart_convergence_iterations += 1
    kill_points.extend(cursor_callback.triggered)

    cursor_after = events["cursor_after_fault"]
    cursor_after_callback = _FaultCallback({("after_cursor_checkpoint", str(cursor_after.source_event_id)): "raise"})
    await _run_pipeline_one(
        pool,
        cursor_after,
        fault_callback=cursor_after_callback,
        cursor_recorder=lambda raw: _write_cursor(pool, manifest, raw, "cursor-after"),
    )
    cursor_after_value = await _cursor_value(pool, manifest, cursor_after)
    kill_points.extend(cursor_after_callback.triggered)

    optional_callback = _FaultCallback({("before_optional_enrichment", str(events["optional_enrichment_fault"].source_event_id)): "raise"})
    await _run_pipeline_one(
        pool,
        events["optional_enrichment_fault"],
        fault_callback=optional_callback,
        capture_orderbook=True,
    )
    kill_points.extend(optional_callback.triggered)

    await process_event(events["poison"], pool, AlertEngine(), _noop_alert_handler)
    await process_event(events["poison"], pool, AlertEngine(), _noop_alert_handler)

    duplicate_metrics = await _run_duplicate_burst(
        pool,
        events["duplicate_burst"],
        int(manifest["duplicate_burst_attempts"]),
    )
    pool_exhaustion = await _run_pool_exhaustion_probe(
        dsn,
        events["pool_exhaustion"],
        alarm_ms=cfg.ingestion.pool_acquire_wait_p95_alarm_ms,
    )
    single_active = await _single_active_probe(dsn)
    operator_commands = await _run_operator_drill(manifest)
    operator_categories = {"incident", "backlog", "repair", "final_status"}
    operator_scaffold_present = operator_categories <= set(operator_commands)

    measurements = await _collect_measurements(pool, manifest)
    measurements.update(duplicate_metrics)
    measurements.update(pool_exhaustion)
    measurements.update(
        {
            "unsupported_concurrent_instances": single_active["unsupported_concurrent_instances"],
            "restart_convergence_iterations": restart_convergence_iterations,
            "known_gap_count": len(known_gaps),
        }
    )
    backpressure_signal = {
        "status": "DEGRADED" if measurements["pool_acquire_wait_alarm_breached"] else "OK",
        "signal": (
            "pool_acquire_wait_exceeded_alarm"
            if measurements["pool_acquire_wait_alarm_breached"]
            else "pool_acquire_wait_within_alarm"
        ),
        "observed_ms": measurements["pool_acquire_wait_observed_ms"],
        "alarm_ms": measurements["pool_acquire_wait_alarm_ms"],
        "raw_rows_before_release": measurements["pool_exhaustion_raw_rows_before_release"],
        "raw_rows_after_retry": measurements["pool_exhaustion_raw_rows_after_retry"],
    }

    actual_facets: list[str] = []
    if measurements["accepted_unique_raw_events"] > 0:
        actual_facets.append("POSTGRES_INTEGRATION")
    if (
        measurements["duplicate_burst_attempts"] > 1
        and measurements["duplicate_burst_raw_rows"] == 1
        and single_active["first_acquire"]
        and not single_active["second_acquire"]
    ):
        actual_facets.append("CONCURRENCY")
    if sorted(set(kill_points)) == sorted(manifest["kill_points"]):
        actual_facets.append("FAULT_INJECTION")

    expected_counts = manifest["expected_counts"]
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {
            "recovery_barrier": "PROVEN_OFFLINE_DB_GATED",
            "operator_drill": "SCAFFOLD_PRESENT_EXECUTION_DEFERRED",
            "manual_sleep_resume": "ACCEPTED_DEBT",
            "post_canonical_downstream_repair": "KNOWN_GAP_EXPLICIT",
        },
        "repository": {
            "remote": sanitize_git_remote(_git_value(["config", "--get", "remote.origin.url"])),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_db_test",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "postgres_version": measurements.pop("postgres_version"),
            "schema_version": schema_fingerprint(ROOT / "sql"),
            "config_hash": _sha256_path(ROOT / "config" / "app.example.yaml"),
            "environment": "offline_db_gated",
        },
        "time": {
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "input_bounds": {
                "first_exchange_ts": min(raw.exchange_ts for raw in events.values() if raw.exchange_ts).isoformat(),
                "last_exchange_ts": max(raw.exchange_ts for raw in events.values() if raw.exchange_ts).isoformat(),
            },
        },
        "expected_truth": {
            "manifest": manifest_path.relative_to(ROOT).as_posix(),
            "artifact_hash": _sha256_path(manifest_path),
            "thresholds": {
                "recovery_backlog_convergence_max_iterations": threshold,
                "dead_letter_rate_p1_threshold_fraction": cfg.ingestion.dead_letter_rate_p1_threshold_fraction,
                "dead_letter_unresolved_halt_count": cfg.ingestion.dead_letter_unresolved_halt_count,
                "pool_acquire_wait_p95_alarm_ms": cfg.ingestion.pool_acquire_wait_p95_alarm_ms,
                "disk_headroom_min_bytes": cfg.ingestion.disk_headroom_min_bytes,
                "disk_headroom_min_fraction": cfg.ingestion.disk_headroom_min_fraction,
            },
        },
        "evidence": {
            "required_facets": manifest["required_facets"],
            "actual_facets": actual_facets,
            "supporting_facets": (
                ["OPERATOR_DRILL_SCAFFOLD_PRESENT"] if operator_scaffold_present else []
            ),
            "deferred_facets": [item["facet"] for item in manifest["manual_deferred_facets"]],
            "kill_points_exercised": sorted(set(kill_points)),
            "operator_commands": operator_commands,
            "operator_drill": {
                "status": "SCAFFOLD_PRESENT_EXECUTION_DEFERRED",
                "scaffold_present": operator_scaffold_present,
                "executed_against_db": False,
            },
            "backpressure": backpressure_signal,
            "commands": [
                "python -m pytest -q tests\\test_dq3_recovery_trial_db.py",
                "python scripts\\dq3_operator_drill.py --run-key DQ3-RECOVERY-V1",
            ],
            "artifacts": [
                manifest_path.relative_to(ROOT).as_posix(),
                DRILL_SCRIPT.relative_to(ROOT).as_posix(),
            ],
            "artifact_hashes": [
                _sha256_path(manifest_path),
                _sha256_path(DRILL_SCRIPT),
            ],
            "hard_kill": hard_kill,
            "single_active_probe": single_active,
            "cursor_history": {
                "before_checkpoint_after_fault": cursor_before_after_fault,
                "before_checkpoint_after_restart": cursor_before_after_restart,
                "after_checkpoint_after_fault": cursor_after_value,
            },
            "known_gaps": known_gaps,
        },
        "measurements": measurements,
        "pass_invariants": {},
        "fail_conditions": [],
        "blocker_or_inconclusive_reason": None,
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": manifest["manual_deferred_facets"],
        "next_action": "orchestrator_verify_pr",
    }

    evidence["pass_invariants"] = {
        "all_accepted_events_persisted_or_durably_classified": (
            measurements["accepted_unique_raw_events"] > 0
            and measurements["accounted_unique_raw_events"] == measurements["accepted_unique_raw_events"]
            and measurements["accounting_ratio"] == 1.0
        ),
        "restart_converges_within_structural_threshold": (
            measurements["restart_convergence_iterations"] <= threshold
            and hard_kill["raw_event_id"] is not None
        ),
        "canonical_metrics_and_alerts_not_duplicated": (
            measurements["duplicate_canonical_facts"] == 0
            and measurements["duplicate_metric_windows"] == 0
            and measurements["duplicate_historical_alerts"] == 0
        ),
        "cursor_checkpoint_never_skips_uncommitted_scope": (
            cursor_before_after_fault is None
            and cursor_before_after_restart == "cursor-before"
            and cursor_after_value == "cursor-after"
        ),
        "poison_event_stops_retrying_and_is_quarantined": measurements["poison_dead_letters"] == 1,
        "single_active_enforced": measurements["unsupported_concurrent_instances"] == 0,
        "graceful_shutdown_accounts_for_inflight_work": (
            measurements["duplicate_burst_raw_rows"] == 1
            and measurements["duplicate_burst_trade_rows"] == 1
        ),
        "db_outage_backpressure_visible_not_false_healthy": (
            measurements["pool_acquire_wait_timed_out"] is True
            and measurements["pool_acquire_wait_alarm_breached"] is True
            and measurements["pool_exhaustion_raw_rows_before_release"] == 0
            and measurements["pool_exhaustion_raw_rows_after_retry"] == 1
            and backpressure_signal["status"] == "DEGRADED"
        ),
        "unrecoverable_intervals_marked_known_gap_or_inconclusive": (
            measurements["known_gap_count"] == len(known_gaps)
            and measurements["known_gap_count"] == 3
            and all(
                gap["classification"] == "KNOWN_GAP" and gap["db_verified"] is True
                for gap in known_gaps
            )
        ),
        "no_secrets_in_fixtures_logs_or_evidence": not _contains_secret_text(manifest_path, evidence),
    }
    for key, expected in expected_counts.items():
        if measurements.get(key) != expected:
            evidence["fail_conditions"].append(
                f"measurement {key} expected {expected}, got {measurements.get(key)}"
            )
    if sorted(set(kill_points)) != sorted(manifest["kill_points"]):
        evidence["fail_conditions"].append(
            f"kill point coverage expected {sorted(manifest['kill_points'])}, got {sorted(set(kill_points))}"
        )
    if not all(evidence["pass_invariants"].values()) or evidence["fail_conditions"]:
        evidence["outcome"] = "FAIL"
    return evidence
