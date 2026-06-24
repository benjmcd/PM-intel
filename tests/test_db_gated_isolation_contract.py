from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve()

SCRATCH_ISOLATED = "scratch_isolated"
READ_ONLY_CONFIGURED_DB = "read_only_configured_db"
CLEANUP_GUARDED_CONFIGURED_DB = "cleanup_guarded_configured_db"
NEEDS_FIX = "needs_fix"

ALLOWED_CATEGORIES = {
    SCRATCH_ISOLATED,
    READ_ONLY_CONFIGURED_DB,
    CLEANUP_GUARDED_CONFIGURED_DB,
    NEEDS_FIX,
}

DB_SURFACE_RE = re.compile(
    r"PMFI_DB_URL|asyncpg\.connect|asyncpg\.create_pool|PoolManager|"
    r"create_backup|restore_backup|drop_database|DROP\s+DATABASE",
    re.IGNORECASE,
)
WRITE_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|TRUNCATE|ALTER|COPY)\b|"
    r"\b(execute|executemany|copy_records_to_table)\s*\(|"
    r"\b(create_backup|restore_backup)\s*\(",
    re.IGNORECASE,
)
CONFIGURED_DB_CLEANUP_RE = re.compile(
    r"finally:|addfinalizer|yield|cleanup_|DELETE\s+FROM|TRUNCATE|"
    r"DROP\s+TABLE|drop_test_scratch_database|drop_database|ROLLBACK|rollback\(",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ManifestEntry:
    category: str
    rationale: str
    allow_configured_writes: bool = False
    scratch_markers: tuple[str, ...] = ()


DB_GATED_TEST_MANIFEST: dict[str, ManifestEntry] = {
    "tests/test_advisory_lock_db.py": ManifestEntry(
        READ_ONLY_CONFIGURED_DB,
        "Exercises transaction-scoped advisory locking against the configured DB; no persisted table rows are written.",
    ),
    "tests/test_alert_dedupe_window_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes synthetic alert dedupe-window writes through a guarded pmfi_testiso_* scratch database.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_", "alert_dedupe"),
    ),
    "tests/test_alert_lineage_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes synthetic alert-lineage writes through a guarded pmfi_testiso_* scratch database.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_", "alert_lineage"),
    ),
    "tests/test_alert_precision_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Uses bounded synthetic alert/review rows and explicit teardown instead of changing operator data.",
        allow_configured_writes=True,
    ),
    "tests/test_alerts_schema_contract.py": ManifestEntry(
        READ_ONLY_CONFIGURED_DB,
        "Introspects the configured schema only; no data-changing SQL is issued.",
    ),
    "tests/test_backup_restore_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Backs up a seeded pmfi_testiso_* source DB and restores into a separate pmfi_testiso_* target DB.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_backup_src_"),
    ),
    "tests/test_baseline_idempotency_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Exercises baseline idempotency with synthetic rows and explicit cleanup around the configured DB.",
        allow_configured_writes=True,
    ),
    "tests/test_baselines_store_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes baseline storage fixture rows through a guarded pmfi_testiso_* scratch database.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_", "baselines_store"),
    ),
    "tests/test_capacity_measure_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Runs the capacity scenario through guarded pmfi_capacity_* scratch databases and asserts cleanup.",
        scratch_markers=("pmfi_capacity_", "list_capacity_scratch_databases"),
    ),
    "tests/test_dashboard_alerts_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Seeds dashboard alert rows for query coverage and removes the inserted synthetic records.",
        allow_configured_writes=True,
    ),
    "tests/test_dashboard_alerts_persistence_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Persists synthetic dashboard alert-review rows and cleans them by generated IDs.",
        allow_configured_writes=True,
    ),
    "tests/test_dashboard_queries_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes dashboard query fixture writes through the shared pmfi_testiso_* scratch database helper.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_"),
    ),
    "tests/test_dead_letters_dedupe_guard_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes dead-letter dedupe fixture writes through a guarded pmfi_testiso_* scratch database.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_", "dead_letters"),
    ),
    "tests/test_decimal_roundtrip.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Checks numeric round-trip persistence with synthetic rows and FK-safe cleanup.",
        allow_configured_writes=True,
    ),
    "tests/test_dq1_capture_gauntlet_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Runs the DQ1 capture scenario on synthetic data with the qualification cleanup helpers before and after.",
        allow_configured_writes=True,
    ),
    "tests/test_dq2_semantics_matrix_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Runs the DQ2 semantics matrix after clearing only its synthetic scenario rows, then repeats cleanup.",
        allow_configured_writes=True,
    ),
    "tests/test_dq3_recovery_trial_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Uses generated DQ3 scenario identifiers with repo cleanup helpers around each configured-DB run.",
        allow_configured_writes=True,
    ),
    "tests/test_dq4_live_trial_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Local DB parts use synthetic DQ4 rows with cleanup; the bounded live subtest has a separate opt-in env gate.",
        allow_configured_writes=True,
    ),
    "tests/test_dq5_restore_trial_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Mutates restored/rebuilt DQ5 scratch databases and verifies the DQ5 scratch DB cleanup helper.",
        scratch_markers=("_scratch_databases", "list_dq5_scratch_databases"),
    ),
    "tests/test_e2e_pipeline_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Runs synthetic end-to-end pipeline writes inside a shared pmfi_testiso_* scratch DB.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_"),
    ),
    "tests/test_kalshi_ingest_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Persists synthetic Kalshi ingest rows and removes the scoped fixture records after assertions.",
        allow_configured_writes=True,
    ),
    "tests/test_market_title_backfill_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Exercises title backfill writes against generated fixture markets with explicit teardown.",
        allow_configured_writes=True,
    ),
    "tests/test_operational_deadletter_guards_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Writes operational dead-letter guard fixtures and deletes only those generated rows.",
        allow_configured_writes=True,
    ),
    "tests/test_polymarket_ingest_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Persists synthetic Polymarket ingest rows and cleans the scoped fixture records.",
        allow_configured_writes=True,
    ),
    "tests/test_raw_dedup_atomic_db.py": ManifestEntry(
        CLEANUP_GUARDED_CONFIGURED_DB,
        "Tests raw-event dedupe persistence with generated IDs and explicit configured-DB cleanup.",
        allow_configured_writes=True,
    ),
    "tests/test_replay_backtest_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Creates per-module pmfi_replaybt_* databases and drops them after replay/backtest assertions.",
        scratch_markers=("pmfi_replaybt_", "_list_replaybt_scratch_databases"),
    ),
    "tests/test_replay_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Routes replay persistence fixtures through the shared pmfi_testiso_* scratch database helper.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_"),
    ),
    "tests/test_soak_runner_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Requires explicit PMFI_RUN_SOAK_RUN_E2E opt-in and cleans dedicated soak scratch databases.",
        scratch_markers=("PMFI_RUN_SOAK_RUN_E2E", "cleanup_soak_scratch_databases"),
    ),
    "tests/test_soak_stability_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Runs soak-stability workload in dedicated scratch DBs and asserts the scratch inventory is empty.",
        scratch_markers=("list_soak_scratch_databases", "SCRATCH_DB"),
    ),
    "tests/test_storage_hardening_db.py": ManifestEntry(
        SCRATCH_ISOLATED,
        "Runs storage-hardening writes inside the shared pmfi_testiso_* scratch database helper.",
        scratch_markers=("create_test_scratch_database", "pmfi_testiso_"),
    ),
}

MOCKED_OR_LITERAL_DB_SURFACE_TESTS: dict[str, str] = {
    "tests/test_alerts_review.py": "Patches asyncpg pools with AsyncMock and asserts generated SQL only.",
    "tests/test_cli.py": "Covers CLI/PoolManager wiring with fakes; no PMFI_DB_URL-backed connection.",
    "tests/test_cmd_reporting.py": "Mocks asyncpg/create_pool and asserts reporting SQL behavior offline.",
    "tests/test_cmd_watch.py": "Mocks asyncpg.create_pool and rich loop behavior; no real DB connection.",
    "tests/test_daemon_logging.py": "Uses fake PoolManager instances for logging behavior.",
    "tests/test_daemon_observability.py": "Uses fake PoolManager/daemon state to verify telemetry behavior.",
    "tests/test_ingest_single_active.py": "Tests ingest active-surface behavior with faked PoolManager state.",
    "tests/test_ingest_supervisor.py": "Fakes PoolManager, pools, and asyncpg errors for supervisor control flow.",
    "tests/test_pool_acquire_wait_guard.py": "Wraps a fake pool to test acquire-wait metrics offline.",
    "tests/test_review_cleanup_a.py": "Monkeypatches asyncpg.connect and uses literal fake DSNs for retry logic.",
    "tests/test_soak_runner.py": "Tests soak command helpers and PoolManager construction with monkeypatched DB creation.",
    "tests/test_subscription_refresh.py": "Runs subscription refresh against fake pools and patched repo calls.",
    "tests/test_supervise_generic_exception.py": "Uses fake PoolManager state for supervisor exception behavior.",
    "tests/test_task_handoff.py": "Uses PMFI_DB_URL as a redaction fixture value only; it never opens a DB connection.",
}


class _SurfaceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.pmfi_db_url = False
        self.asyncpg_connect = False
        self.asyncpg_create_pool = False
        self.pool_manager = False
        self.backup_restore = False
        self.admin_helper = False

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            if node.value == "PMFI_DB_URL":
                self.pmfi_db_url = True
            if "DROP DATABASE" in node.value.upper():
                self.admin_helper = True
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "asyncpg":
            if node.attr == "connect":
                self.asyncpg_connect = True
            elif node.attr == "create_pool":
                self.asyncpg_create_pool = True
        if node.attr in {"PoolManager", "create_backup", "restore_backup", "drop_database"}:
            self._mark_name(node.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self._mark_name(node.id)

    def _mark_name(self, name: str) -> None:
        if name == "PoolManager":
            self.pool_manager = True
        elif name in {"create_backup", "restore_backup"}:
            self.backup_restore = True
        elif name == "drop_database":
            self.admin_helper = True

    @property
    def has_db_surface(self) -> bool:
        return any(
            (
                self.pmfi_db_url,
                self.asyncpg_connect,
                self.asyncpg_create_pool,
                self.pool_manager,
                self.backup_restore,
                self.admin_helper,
            )
        )


def _source(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8-sig")


def _scan_db_surface(path: Path) -> bool:
    source = path.read_text(encoding="utf-8-sig")
    visitor = _SurfaceVisitor()
    visitor.visit(ast.parse(source, filename=str(path)))
    return visitor.has_db_surface or bool(DB_SURFACE_RE.search(source))


def _db_surface_test_files() -> set[str]:
    files: set[str] = set()
    for path in (ROOT / "tests").glob("test*.py"):
        if path.resolve() == THIS_FILE:
            continue
        if _scan_db_surface(path):
            files.add(path.relative_to(ROOT).as_posix())
    return files


def _is_write_capable(source: str) -> bool:
    return bool(WRITE_SQL_RE.search(source))


def _has_configured_db_cleanup_guard(source: str) -> bool:
    return bool(CONFIGURED_DB_CLEANUP_RE.search(source))


def test_db_surface_tests_are_explicitly_classified() -> None:
    discovered = _db_surface_test_files()
    classified = set(DB_GATED_TEST_MANIFEST) | set(MOCKED_OR_LITERAL_DB_SURFACE_TESTS)

    assert discovered - classified == set()
    assert classified - discovered == set()
    assert set(DB_GATED_TEST_MANIFEST).isdisjoint(MOCKED_OR_LITERAL_DB_SURFACE_TESTS)

    for rel_path, entry in DB_GATED_TEST_MANIFEST.items():
        assert entry.category in ALLOWED_CATEGORIES, rel_path
        assert entry.rationale.strip(), rel_path
        assert entry.category != NEEDS_FIX, rel_path

    for rel_path, rationale in MOCKED_OR_LITERAL_DB_SURFACE_TESTS.items():
        assert rationale.strip(), rel_path


def test_db_gated_isolation_contract_matches_manifest() -> None:
    for rel_path, entry in DB_GATED_TEST_MANIFEST.items():
        source = _source(rel_path)

        if entry.category == SCRATCH_ISOLATED:
            assert entry.scratch_markers, rel_path
            assert any(marker in source for marker in entry.scratch_markers), rel_path
            continue

        if entry.category == READ_ONLY_CONFIGURED_DB:
            assert not _is_write_capable(source), rel_path
            continue

        if entry.category == CLEANUP_GUARDED_CONFIGURED_DB:
            assert entry.allow_configured_writes, rel_path
            assert _has_configured_db_cleanup_guard(source), rel_path
            continue

        raise AssertionError(f"{rel_path} is marked {NEEDS_FIX}")


def test_mocked_or_literal_db_surface_tests_do_not_use_pmfi_db_url_for_runtime_db_access() -> None:
    for rel_path in MOCKED_OR_LITERAL_DB_SURFACE_TESTS:
        source = _source(rel_path)
        if "PMFI_DB_URL" not in source:
            continue
        assert "monkeypatch.setenv" in source or "redact_db_url" in source, rel_path
