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
]
POSTGRES_PORT = "5433"


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


def init() -> None:
    wait()
    for rel in SQL_FILES:
        path = ROOT / rel
        print(f"applying {rel}")
        psql_stdin(path.read_text(encoding="utf-8"))


def verify() -> None:
    wait()
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
