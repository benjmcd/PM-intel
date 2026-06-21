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
MANIFEST = ROOT / "tests" / "qualification" / "dq2_semantics_manifest.yaml"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_dq2_semantics_matrix_proves_all_pass_invariants() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq2_semantics import cleanup_dq2_semantics_rows, run_dq2_semantics_matrix

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            await cleanup_dq2_semantics_rows(pool)
            evidence = await run_dq2_semantics_matrix(pool, MANIFEST)
            assert evidence["scenario_id"] == "DQ-2"
            assert evidence["outcome"] == "PASS"
            assert evidence["measurements"] == {
                "fixture_inputs": 24,
                "explicit_dispositions": 24,
                "normalized": 13,
                "ignored_valid": 4,
                "quarantined": 6,
                "duplicates": 1,
                "dead_letters": 7,
                "postgres_roundtrip_checked": 13,
                "fixture_hashes_checked": 24,
            }
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["fail_conditions"] == []
            assert evidence["evidence"]["actual_facets"] == [
                "SOURCE_INSPECTION",
                "OFFLINE_TEST",
                "POSTGRES_INTEGRATION",
            ]
        finally:
            await cleanup_dq2_semantics_rows(pool)
            await pool.close()

    asyncio.run(_run())
