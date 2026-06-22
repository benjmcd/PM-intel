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
MANIFEST = ROOT / "tests" / "qualification" / "capacity_manifest.yaml"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_capacity_measurement_emits_structural_evidence_from_scratch_db() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.capacity import (
        list_capacity_scratch_databases,
        run_capacity_measurement,
    )

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            evidence = await run_capacity_measurement(pool, MANIFEST)

            assert evidence["version"] == "pmfi-data-plane-scenario-run.v1"
            assert evidence["scenario_id"] == "M-CAPACITY"
            assert evidence["outcome"] == "PASS", {
                "fail_conditions": evidence["fail_conditions"],
                "pass_invariants": evidence["pass_invariants"],
                "measurements": evidence["measurements"],
            }
            assert set(evidence["evidence"]["actual_facets"]) == {
                "OFFLINE",
                "POSTGRES_INTEGRATION",
                "BOUNDED_LOCAL_WORKLOAD",
                "RTO_RESTART",
                "RTO_RESTORE",
                "SCHEMA_DUMP_FIDELITY",
            }
            assert evidence["completeness_classifications"]["envelope"] == "MEASURED_BOUNDED_LOCAL"
            assert evidence["evidence"]["deferred_facets"] == [
                "LONG_HORIZON_SOAK",
                "MULTI_HOST_REPRODUCIBILITY",
            ]
            measurements = evidence["measurements"]
            assert measurements["workload_events"] == 12
            assert measurements["sample_count"] >= 12
            assert measurements["pool_acquire_p95_ms"] >= 0
            assert measurements["free_disk_bytes"] > 0
            assert measurements["db_size_bytes"] > 0
            assert measurements["est_bytes_per_event"] is not None
            assert measurements["projected_runway_events_or_days"] is not None
            assert measurements["rto_restart_seconds"] > 0
            assert measurements["rto_restore_seconds"] > 0
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
            assert evidence["recommended_thresholds"]["mode"] == "recommend_only"
            assert evidence["fail_conditions"] == []
            assert evidence["evidence"]["scratch_databases"]["source"].startswith("pmfi_capacity_")
        finally:
            await pool.close()

        assert await list_capacity_scratch_databases() == []

    asyncio.run(_run())
