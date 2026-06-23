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
MANIFEST = ROOT / "tests" / "qualification" / "soak_manifest.yaml"


def test_soak_stability_measurement_uses_scratch_db_and_cleans_up() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.soak_stability import (
        list_soak_scratch_databases,
        run_soak_stability_measurement,
    )

    async def _run() -> None:
        pool = await create_pool(os.environ["PMFI_DB_URL"], min_size=1, max_size=1)
        try:
            evidence = await run_soak_stability_measurement(
                pool,
                MANIFEST,
                db_url=os.environ["PMFI_DB_URL"],
            )

            assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
            assert evidence["scenario_id"] == "M2-SOAK"
            assert evidence["outcome"] == "PASS", {
                "fail_conditions": evidence["fail_conditions"],
                "pass_invariants": evidence["pass_invariants"],
                "measurements": evidence["measurements"],
            }
            assert set(evidence["evidence"]["actual_facets"]) == {
                "OFFLINE",
                "POSTGRES_INTEGRATION",
                "SCRATCH_DB",
                "BOUNDED_LOCAL_WORKLOAD",
                "RECOVERY_INDUCED",
            }
            measurements = evidence["measurements"]
            assert measurements["events_processed"] == 30
            assert measurements["sample_count"] >= 6
            assert measurements["pool_acquire_sample_count"] >= 20
            assert measurements["memory_growth_mb"] <= measurements["memory_growth_tolerance_mb"]
            assert measurements["recovery_induced"] is True
            assert measurements["recovery_successful"] is True
            assert measurements["dead_letters_created"] == 0
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["recommended_thresholds"]["mode"] == "recommend_only"
        finally:
            await pool.close()

        assert await list_soak_scratch_databases(db_url=os.environ["PMFI_DB_URL"]) == []

    asyncio.run(_run())
