from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

ROOT = Path(__file__).resolve().parents[1]
DQ3_SOURCE_CHANNEL = "dq3_recovery_trial_v1"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _raw_trade(source_event_id: str, venue_market_id: str, *, price: str = "0.50", size: str = "100") -> object:
    from pmfi.domain import RawEvent

    now = datetime.now(timezone.utc).replace(microsecond=0)
    return RawEvent(
        venue_code="polymarket",
        source_channel=DQ3_SOURCE_CHANNEL,
        source_event_type="last_trade_price",
        source_event_id=source_event_id,
        venue_market_id=venue_market_id,
        exchange_ts=now,
        received_at=now,
        payload={
            "trade_id": source_event_id,
            "market": venue_market_id,
            "outcome": "yes",
            "side": "buy",
            "price": price,
            "size": size,
        },
    )


async def _cleanup(pool) -> None:
    async with pool.acquire() as conn:
        raw_ids = [
            row["raw_event_id"]
            for row in await conn.fetch(
                "SELECT raw_event_id FROM raw_events WHERE source_channel = $1",
                DQ3_SOURCE_CHANNEL,
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
        await conn.execute(
            "DELETE FROM event_dedupe_keys WHERE source_channel = $1",
            DQ3_SOURCE_CHANNEL,
        )
        if market_ids:
            await conn.execute("DELETE FROM markets WHERE market_id = ANY($1::uuid[])", market_ids)


def test_duplicate_raw_without_disposition_is_reprocessed_on_restart() -> None:
    from pmfi.db import create_pool
    from pmfi.db.repos.raw_events import insert_raw_event
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import process_event

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            await _cleanup(pool)
            raw = _raw_trade("dq3-hard-kill-after-raw", "DQ3-RECOVERY-RAW")
            async with pool.acquire() as conn:
                raw_event_id, is_duplicate = await insert_raw_event(conn, raw)
            assert raw_event_id > 0
            assert is_duplicate is False

            await process_event(raw, pool, AlertEngine(), lambda *_args: asyncio.sleep(0))

            async with pool.acquire() as conn:
                normalized_count = int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM normalized_trades WHERE raw_event_id = $1",
                        raw_event_id,
                    )
                    or 0
                )
                dead_letter_count = int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM dead_letters WHERE raw_event_id = $1",
                        raw_event_id,
                    )
                    or 0
                )
            assert normalized_count == 1
            assert dead_letter_count == 0
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_dq3_recovery_trial_proves_honest_recovery_barrier() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq3_recovery import (
        cleanup_dq3_recovery_rows,
        run_dq3_recovery_trial,
    )

    manifest = ROOT / "tests" / "qualification" / "dq3_recovery_manifest.yaml"

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            await cleanup_dq3_recovery_rows(pool, manifest)
            evidence = await run_dq3_recovery_trial(pool, manifest)
            assert evidence["scenario_id"] == "DQ-3"
            assert evidence["outcome"] == "PASS"
            assert set(evidence["evidence"]["actual_facets"]) == {
                "POSTGRES_INTEGRATION",
                "CONCURRENCY",
                "FAULT_INJECTION",
            }
            assert "OPERATOR_DRILL" in evidence["evidence"]["deferred_facets"]
            assert evidence["evidence"]["supporting_facets"] == ["OPERATOR_DRILL_SCAFFOLD_PRESENT"]
            assert "OPERATOR_DRILL_MANUAL_SLEEP_RESUME" in evidence["evidence"]["deferred_facets"]
            assert evidence["measurements"]["accepted_unique_raw_events"] == 12
            assert evidence["measurements"]["accounted_unique_raw_events"] == 12
            assert evidence["measurements"]["accounting_ratio"] == 1.0
            assert evidence["measurements"]["normalized_trade_rows"] == 10
            assert evidence["measurements"]["dead_letter_rows"] == 2
            assert evidence["measurements"]["duplicate_canonical_facts"] == 0
            assert evidence["measurements"]["duplicate_metric_windows"] == 0
            assert evidence["measurements"]["duplicate_historical_alerts"] == 0
            assert evidence["measurements"]["unsupported_concurrent_instances"] == 0
            assert evidence["measurements"]["poison_dead_letters"] == 1
            assert evidence["measurements"]["pool_acquire_wait_timed_out"] is True
            assert evidence["measurements"]["pool_acquire_wait_alarm_breached"] is True
            assert evidence["measurements"]["pool_exhaustion_raw_rows_before_release"] == 0
            assert evidence["measurements"]["pool_exhaustion_raw_rows_after_retry"] == 1
            assert evidence["evidence"]["backpressure"]["status"] == "DEGRADED"
            assert evidence["evidence"]["backpressure"]["signal"] == "pool_acquire_wait_exceeded_alarm"
            assert evidence["measurements"]["restart_convergence_iterations"] <= evidence["expected_truth"]["thresholds"]["recovery_backlog_convergence_max_iterations"]
            assert sorted(evidence["evidence"]["kill_points_exercised"]) == [
                "after_canonical_fact_commit",
                "after_cursor_checkpoint",
                "after_raw_event_commit",
                "before_alert_persistence",
                "before_cursor_checkpoint",
                "before_metric_update",
                "before_optional_enrichment",
                "processing_claim_held",
            ]
            known_gaps = {
                gap["source_event_id"]: gap for gap in evidence["evidence"]["known_gaps"]
            }
            assert set(known_gaps) == {
                "dq3-fault-after-canonical",
                "dq3-fault-before-metric",
                "dq3-fault-before-alert",
            }
            for gap in known_gaps.values():
                assert gap["classification"] == "KNOWN_GAP"
                assert gap["db_verified"] is True
                assert gap["raw_event_id"] > 0
                assert gap["trade_id"]
                assert gap["alert_rows"] == 0
            assert known_gaps["dq3-fault-after-canonical"]["metric_window_rows"] == 0
            assert known_gaps["dq3-fault-before-metric"]["metric_window_rows"] == 0
            assert known_gaps["dq3-fault-before-alert"]["expected_missing_metric_window"] is False
            assert known_gaps["dq3-fault-before-alert"]["metric_window_rows"] >= 1
            assert evidence["measurements"]["known_gap_count"] == 3
            assert evidence["completeness_classifications"]["operator_drill"] == "SCAFFOLD_PRESENT_EXECUTION_DEFERRED"
            assert evidence["completeness_classifications"]["manual_sleep_resume"] == "ACCEPTED_DEBT"
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert "operator_commands_identify_incident_backlog_repair_final_status" not in evidence["pass_invariants"]
            assert evidence["fail_conditions"] == []
            operator_commands = evidence["evidence"]["operator_commands"]
            assert {"incident", "backlog", "repair", "final_status"} <= set(operator_commands)
            assert evidence["evidence"]["operator_drill"]["executed_against_db"] is False
        finally:
            await cleanup_dq3_recovery_rows(pool, manifest)
            await pool.close()

    asyncio.run(_run())
