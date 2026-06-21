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


def test_dq1_capture_gauntlet_proves_all_pass_invariants() -> None:
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
                "FAULT_INJECTION",
            }
            assert evidence["measurements"] == {
                "generated_observations": 21,
                "accepted_observations": 18,
                "persisted_observations": 16,
                "extracted_raw_events": 19,
                "expected_unique_raw_events": 16,
                "duplicate_observations": 3,
                "legitimate_repeated_events": 2,
                "cursor_page_checkpoints": 6,
                "buffer_high_water_mark": 4,
                "explicitly_rejected_dropped_observations": 2,
                "durably_classified_failures": 2,
                "quarantined_events": 1,
                "normalized_trade_rows": 13,
                "duplicate_canonical_facts": 0,
            }
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["completeness_classifications"]["controlled_capture"] == "PROVEN_COMPLETE"
            assert evidence["completeness_classifications"]["bounded_outage_overflow"] == "KNOWN_GAP"
            assert evidence["fail_conditions"] == []
            assert evidence["expected_truth"]["manifest"].endswith("tests/qualification/dq1_capture_manifest.yaml")
        finally:
            await cleanup_dq1_capture_rows(pool)
            await pool.close()
    asyncio.run(_run())
