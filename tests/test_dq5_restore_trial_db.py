from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq5_restore_manifest.yaml"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_dq5_restore_trial_proves_restore_rebuild_barrier() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq5_restore import run_dq5_restore_trial

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            evidence = await run_dq5_restore_trial(pool, MANIFEST)
            assert evidence["scenario_id"] == "DQ-5"
            assert evidence["outcome"] == "PASS", {
                "fail_conditions": evidence["fail_conditions"],
                "false_invariants": [
                    key for key, value in evidence["pass_invariants"].items() if not value
                ],
                "measurements": evidence["measurements"],
            }
            assert set(evidence["evidence"]["actual_facets"]) == {
                "RESTORE",
                "MIGRATION",
                "POSTGRES_INTEGRATION",
            }
            assert evidence["evidence"]["deferred_facets"] == ["LONG_HORIZON_SOAK"]
            assert evidence["measurements"]["source_counts"] == {
                "raw_events": 3,
                "normalized_trades": 3,
                "metric_windows": 3,
                "alerts": 2,
            }
            assert evidence["measurements"]["restored_counts"] == evidence["measurements"]["source_counts"]
            assert evidence["measurements"]["rebuilt_counts"] == evidence["measurements"]["source_counts"]
            assert evidence["measurements"]["source_hashes"] == evidence["measurements"]["restored_hashes"]
            assert evidence["measurements"]["source_hashes"] == evidence["measurements"]["rebuilt_hashes"]
            assert evidence["measurements"]["restored_schema_fingerprint"] == evidence["measurements"]["fresh_schema_fingerprint"]
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["fail_conditions"] == []
        finally:
            await pool.close()

    asyncio.run(_run())


def test_dq5_restore_invariant_fires_when_restored_row_is_missing() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq5_restore import (
        cleanup_dq5_scratch_databases,
        collect_dq5_state,
        evaluate_dq5_pass_invariants,
        run_dq5_restore_trial,
    )

    async def _run() -> None:
        pool = await create_pool(_dsn())
        evidence = None
        try:
            evidence = await run_dq5_restore_trial(pool, MANIFEST, keep_scratch=True)
            restored_db = evidence["evidence"]["scratch_databases"]["restored"]
            restored_url = _dsn().rsplit("/", 1)[0] + f"/{restored_db}"
            conn = await asyncpg.connect(restored_url)
            try:
                deleted = await conn.fetchval(
                    """DELETE FROM pmfi.alerts
                       WHERE alert_id = (
                           SELECT alert_id FROM pmfi.alerts
                           ORDER BY fired_at, alert_id
                           LIMIT 1
                       )
                       RETURNING 1"""
                )
                assert deleted == 1
            finally:
                await conn.close()

            mutated = await collect_dq5_state(restored_url, source_channel="dq5_restore_trial_v1")
            measurements = {
                **evidence["measurements"],
                "restored_counts": mutated["counts"],
                "restored_hashes": mutated["hashes"],
            }
            invariants = evaluate_dq5_pass_invariants(measurements)

            assert invariants["restore_preserves_all_canonical_state_without_loss"] is False
        finally:
            if evidence is not None:
                await cleanup_dq5_scratch_databases(evidence["evidence"]["scratch_databases"])
            await pool.close()

    asyncio.run(_run())
