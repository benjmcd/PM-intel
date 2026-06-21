from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq1_capture_manifest.yaml"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_dq1_capture_gauntlet_reports_only_exercised_capture_core() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq1_capture import (
        cleanup_dq1_capture_rows,
        run_dq1_capture_gauntlet,
    )

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            await cleanup_dq1_capture_rows(pool)
            evidence = await run_dq1_capture_gauntlet(pool, MANIFEST)
            assert evidence["scenario_id"] == "DQ-1"
            assert evidence["outcome"] == "PASS"
            assert set(evidence["evidence"]["actual_facets"]) == {
                "OFFLINE_TEST",
                "POSTGRES_INTEGRATION",
                "CONCURRENCY",
            }
            assert "FAULT_INJECTION" in evidence["evidence"]["deferred_facets"]
            assert "expected_unique_raw_events" not in evidence["measurements"]
            assert evidence["measurements"] == {
                "generated_observations": 19,
                "accepted_observations": 16,
                "db_persisted_unique_raw_events": 16,
                "extracted_raw_events": 19,
                "duplicate_observations": 3,
                "legitimate_repeated_events": 2,
                "cursor_page_checkpoints": 6,
                "quarantined_events": 1,
                "normalized_trade_rows": 13,
                "duplicate_canonical_facts": 0,
                "buffer_high_water_mark": 6,
                "payload_hashes_verified_rows": 16,
                "lineage_verified_rows": 16,
                "concurrency_probe_attempts": 8,
                "concurrency_probe_persisted_rows": 1,
                "concurrency_probe_duplicate_observations": 7,
            }
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["completeness_classifications"]["controlled_capture"] == "PROVEN_CORE"
            assert evidence["completeness_classifications"]["bounded_outage_overflow"] == "DEFERRED_TO_DQ3"
            assert evidence["expected_truth"]["expected_unique_raw_events"] == 16
            assert any(item["facet"] == "FAULT_INJECTION" for item in evidence["accepted_debt"])
            assert evidence["fail_conditions"] == []
            assert evidence["expected_truth"]["manifest"].endswith("tests/qualification/dq1_capture_manifest.yaml")
        finally:
            await cleanup_dq1_capture_rows(pool)
            await pool.close()
    asyncio.run(_run())


def test_dq1_duplicate_canonical_fact_detection_includes_null_venue_trade_id() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq1_capture import (
        _count_duplicate_canonical_facts,
        cleanup_dq1_capture_rows,
        run_dq1_capture_gauntlet,
    )

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            await cleanup_dq1_capture_rows(pool)
            evidence = await run_dq1_capture_gauntlet(pool, MANIFEST)
            assert evidence["outcome"] == "PASS"
            async with pool.acquire() as conn:
                raw_ids = [
                    row["raw_event_id"]
                    for row in await conn.fetch(
                        "SELECT raw_event_id FROM raw_events WHERE source_channel = $1",
                        "dq1_capture_gauntlet_v1",
                    )
                ]
                assert await _count_duplicate_canonical_facts(conn, raw_ids) == 0
                duplicate_trade_id = await conn.fetchval(
                    """INSERT INTO normalized_trades
                         (raw_event_id, raw_event_received_at, venue_code, venue_trade_id,
                          market_id, outcome_id, outcome_key, aggressor_side, directional_side,
                          side_confidence, price, contracts, capital_at_risk_usd,
                          payout_notional_usd, fee_usd, exchange_ts, received_at,
                          normalization_version, warnings, source_payload)
                       SELECT
                          nt.raw_event_id, nt.raw_event_received_at, nt.venue_code,
                          nt.venue_trade_id, nt.market_id, nt.outcome_id, nt.outcome_key,
                          nt.aggressor_side, nt.directional_side, nt.side_confidence,
                          nt.price, nt.contracts, nt.capital_at_risk_usd,
                          nt.payout_notional_usd, nt.fee_usd, nt.exchange_ts,
                          nt.received_at, nt.normalization_version, nt.warnings,
                          nt.source_payload
                       FROM normalized_trades nt
                       JOIN raw_events re ON re.raw_event_id = nt.raw_event_id
                       WHERE re.source_channel = $1
                         AND nt.venue_trade_id IS NULL
                       LIMIT 1
                       RETURNING trade_id""",
                    "dq1_capture_gauntlet_v1",
                )
                assert duplicate_trade_id is not None
                assert await _count_duplicate_canonical_facts(conn, raw_ids) == 1
        finally:
            await cleanup_dq1_capture_rows(pool)
            await pool.close()

    asyncio.run(_run())
