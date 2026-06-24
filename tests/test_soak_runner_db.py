from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL") or os.environ.get("PMFI_RUN_SOAK_RUN_E2E") != "1",
    reason="Requires PMFI_DB_URL and PMFI_RUN_SOAK_RUN_E2E=1 for detached soak-run DB lifecycle",
)


def test_soak_run_detached_lifecycle_uses_dedicated_db_and_leaves_primary_untouched(tmp_path: Path) -> None:
    import asyncio

    from pmfi.cli import main
    from pmfi.db import close_pool, create_pool
    from pmfi.qualification.soak_runner import SoakRunPaths, dedicated_soak_database_name, read_status
    from pmfi.qualification.soak_stability import cleanup_soak_scratch_databases

    run_id = f"test-soak-run-{os.getpid()}"
    paths = SoakRunPaths.from_root(tmp_path, run_id)
    database_name = dedicated_soak_database_name(run_id)

    async def _primary_counts() -> dict[str, int]:
        pool = await create_pool(os.environ["PMFI_DB_URL"], min_size=1, max_size=1)
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                      (SELECT COUNT(*)::bigint FROM raw_events) AS raw_events,
                      (SELECT COUNT(*)::bigint FROM normalized_trades) AS normalized_trades,
                      (SELECT COUNT(*)::bigint FROM dead_letters) AS dead_letters,
                      (SELECT COUNT(*)::bigint FROM alerts) AS alerts
                    """
                )
                return {key: int(row[key] or 0) for key in row.keys()}
        finally:
            await close_pool(pool)

    before = asyncio.run(_primary_counts())
    try:
        rc = main(
            [
                "soak-run",
                "start",
                "--run-id",
                run_id,
                "--run-root",
                str(tmp_path),
                "--duration",
                "45s",
                "--max-events",
                "120",
                "--events-per-second",
                "8",
                "--sample-interval-seconds",
                "2",
                "--retention-window-seconds",
                "2",
                "--recovery-interval-seconds",
                "5",
                "--max-db-size-bytes",
                str(256 * 1024 * 1024),
                "--format",
                "json",
            ]
        )
        assert rc == 0

        deadline = time.time() + 45
        status = read_status(paths)
        while time.time() < deadline:
            status = read_status(paths)
            samples = [
                json.loads(line)
                for line in paths.samples_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ] if paths.samples_file.exists() else []
            if (
                status["alive"]
                and int(status["sample_count"]) >= 4
                and any(sample["retention_rows_pruned"] > 0 for sample in samples)
            ):
                break
            time.sleep(0.5)
        assert status["alive"] is True, status
        assert status["sample_count"] >= 4, status

        rc = main(["soak-run", "stop", "--run-dir", str(paths.run_dir), "--wait-seconds", "30", "--format", "json"])
        assert rc == 0
        final = json.loads(paths.final_evidence_file.read_text(encoding="utf-8"))
        assert final["version"] == "pmfi-data-plane-scenario-run.v1"
        assert final["recommended_thresholds"]["mode"] == "recommend_only"
        assert final["measurements"]["events_processed"] > 0
        assert final["measurements"]["rss_mb"] is not None
        assert final["measurements"]["db_size_mb"] is not None
        assert final["measurements"]["dead_letters_created"] == 0
        assert final["measurements"]["stop_reason"] == "operator_stop_requested"
        samples = [
            json.loads(line)
            for line in paths.samples_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(sample["retention_rows_pruned"] > 0 for sample in samples)
    finally:
        asyncio.run(
            cleanup_soak_scratch_databases(
                {"source": database_name},
                db_url=os.environ.get("PMFI_DB_URL"),
            )
        )
    after = asyncio.run(_primary_counts())
    assert after == before
