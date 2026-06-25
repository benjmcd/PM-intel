from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from db_scratch import (
    TESTISO_DB_PREFIX,
    ScratchDatabase,
    create_test_scratch_database,
    drop_test_scratch_database,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq2_semantics_manifest.yaml"
_SCRATCH_DB: ScratchDatabase | None = None


def _dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("DQ2 semantics matrix scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _dq2_semantics_matrix_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("dq2_semantics_matrix")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_dq2_semantics_matrix_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(
        f"{TESTISO_DB_PREFIX}dq2_semantics_matrix_"
    )
    assert _SCRATCH_DB.name in _dsn()


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
                "pinned_canonical_hashes_checked": 13,
                "reprocessed_fixture_inputs": 24,
                "raw_payloads_rechecked_after_reprocess": 23,
                "prior_canonical_hashes_rechecked_after_reprocess": 13,
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


def test_dq2_evidence_sanitizes_remote_and_secret_invariant_fires(monkeypatch) -> None:
    from pmfi.db import create_pool
    from pmfi.qualification import dq2_semantics
    from pmfi.qualification.evidence import evidence_contains_secret

    raw_remote = "https://user:ghp_TOKEN@example.com/org/repo.git"

    def fake_git_value(args: list[str]) -> str | None:
        if args == ["config", "--get", "remote.origin.url"]:
            return raw_remote
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "codex/review-cleanup-fix"
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        return None

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            monkeypatch.setattr(dq2_semantics, "_git_value", fake_git_value)
            await dq2_semantics.cleanup_dq2_semantics_rows(pool)
            evidence = await dq2_semantics.run_dq2_semantics_matrix(pool, MANIFEST)

            dumped = str(evidence)
            assert raw_remote not in dumped
            assert "ghp_TOKEN" not in dumped
            assert evidence["repository"]["remote"] == "https://example.com/org/repo.git"
            assert evidence["pass_invariants"]["no_secrets_in_fixtures_logs_or_evidence"] is True

            planted = dict(evidence)
            planted["repository"] = {
                **evidence["repository"],
                "remote": raw_remote,
            }
            assert evidence_contains_secret(MANIFEST, planted) is True
        finally:
            await dq2_semantics.cleanup_dq2_semantics_rows(pool)
            await pool.close()

    asyncio.run(_run())
