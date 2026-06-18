r"""Local Postgres helper for Windows + Docker Desktop.

This script intentionally uses Python subprocess calls instead of Unix wrappers.
It can initialize the local Docker Postgres instance without requiring a native
`psql` installation by streaming SQL into `docker compose exec`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.local.yml"]
SQL_FILES = [
    "sql/001_init.sql",
    "sql/002_partitions_indexes.sql",
    "sql/003_views_and_queries.sql",
    "sql/004_seed_dev.sql",
    "sql/005_add_watched_flag.sql",
    "sql/006_metric_windows_unique_constraint.sql",
    "sql/007_venue_trade_id_index.sql",
    "sql/008_market_outcome_kind.sql",
    "sql/009_alert_lineage.sql",
    "sql/010_market_baselines_unique.sql",
    "sql/011_metric_windows_index.sql",
    "sql/012_market_volume_column.sql",
    "sql/013_normalized_trade_dedupe_guard.sql",
]
POSTGRES_PORT = "5433"

REQUIRED_SCHEMA_RELATIONS = [
    ("table", "venues", "rp"),
    ("table", "markets", "rp"),
    ("table", "market_outcomes", "rp"),
    ("table", "raw_events", "rp"),
    ("table", "normalized_trades", "rp"),
    ("table", "metric_windows", "rp"),
    ("table", "normalized_trade_dedupe_keys", "r"),
    ("table", "market_baselines", "rp"),
    ("table", "alerts", "rp"),
    ("table", "alert_reviews", "rp"),
    ("table", "dead_letters", "rp"),
    ("table", "data_quality_incidents", "rp"),
    ("table", "orderbook_snapshots", "rp"),
    ("table", "orderbook_levels", "rp"),
    ("view", "v_recent_raw_event_counts", "v"),
    ("view", "v_recent_large_trades", "v"),
    ("view", "v_open_data_quality_incidents", "v"),
    ("view", "v_alert_summary_24h", "v"),
    ("index", "idx_markets_volume", "iI"),
    ("index", "idx_markets_venue_volume", "iI"),
    ("index", "idx_metric_windows_market_window", "iI"),
    ("index", "idx_normalized_trades_venue_trade_id", "iI"),
    ("index", "idx_normalized_trade_dedupe_venue_id", "iI"),
    ("index", "idx_normalized_trade_dedupe_fingerprint", "iI"),
    ("index", "idx_dead_letters_unresolved", "iI"),
    ("index", "idx_data_quality_open", "iI"),
]


def postgres_user() -> str:
    return os.environ.get("POSTGRES_USER", "pmfi")


def postgres_db() -> str:
    return os.environ.get("POSTGRES_DB", "pmfi")


def require_docker() -> None:
    if not shutil.which("docker"):
        print("docker was not found. Install Docker Desktop for Windows or add docker.exe to PATH.", file=sys.stderr)
        raise SystemExit(1)


def run(args: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    require_docker()
    print("==", " ".join(args), "==", flush=True)
    completed = subprocess.run(args, cwd=ROOT, text=True, input=input_text, check=False)
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed


def compose(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([*COMPOSE, *args], input_text=input_text, check=check)


def up() -> None:
    compose("up", "-d", "postgres")
    wait()


def down() -> None:
    compose("down")


def wait(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        completed = compose(
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-p",
            POSTGRES_PORT,
            "-U",
            postgres_user(),
            "-d",
            postgres_db(),
            check=False,
        )
        if completed.returncode == 0:
            print("Postgres is ready")
            return
        time.sleep(2)
    print("Postgres did not become ready before timeout", file=sys.stderr)
    raise SystemExit(1)


def psql_stdin(sql: str) -> None:
    compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-p",
        POSTGRES_PORT,
        "-U",
        postgres_user(),
        "-d",
        postgres_db(),
        "-v",
        "ON_ERROR_STOP=1",
        input_text=sql,
    )


def psql_command(sql: str) -> None:
    compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-p",
        POSTGRES_PORT,
        "-U",
        postgres_user(),
        "-d",
        postgres_db(),
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def schema_readiness_sql() -> str:
    values = ",\n        ".join(
        f"({_sql_literal(kind)}, {_sql_literal(name)}, {_sql_literal(relkinds)})"
        for kind, name, relkinds in REQUIRED_SCHEMA_RELATIONS
    )
    return f"""
DO $$
DECLARE
    missing text;
BEGIN
    WITH expected(kind, relname, relkinds) AS (
        VALUES
        {values}
    ),
    missing_rows AS (
        SELECT expected.kind || ':' || expected.relname AS object_name
        FROM expected
        WHERE NOT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'pmfi'
              AND c.relname = expected.relname
              AND expected.relkinds LIKE '%' || c.relkind::text || '%'
        )
    )
    SELECT string_agg(object_name, ', ' ORDER BY object_name)
    INTO missing
    FROM missing_rows;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing required schema objects: %', missing;
    END IF;
END
$$;
"""


def init() -> None:
    wait()
    for rel in SQL_FILES:
        path = ROOT / rel
        print(f"applying {rel}")
        psql_stdin(path.read_text(encoding="utf-8"))


def verify() -> None:
    wait()
    psql_command(schema_readiness_sql())
    print("Schema readiness check passed")
    psql_command("select venue_code from pmfi.venues order by venue_code;")


def status() -> None:
    compose("ps", check=False)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["up"]:
        up(); return 0
    if argv == ["down"]:
        down(); return 0
    if argv == ["init"]:
        init(); return 0
    if argv == ["verify"]:
        verify(); return 0
    if argv == ["status"]:
        status(); return 0
    print("usage: python scripts\\db_local.py {up|down|init|verify|status}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
