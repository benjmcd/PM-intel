from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _admin_dsn(db_url: str) -> str:
    parsed = urlsplit(db_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def _drop_database(db_name: str) -> None:
    conn = await asyncpg.connect(_admin_dsn(_dsn()))
    try:
        await conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1
              AND pid <> pg_backend_pid()
            """,
            db_name,
        )
        await conn.execute(f"DROP DATABASE IF EXISTS {_quote_ident(db_name)}")
    finally:
        await conn.close()


async def _create_database(db_name: str) -> None:
    conn = await asyncpg.connect(_admin_dsn(_dsn()))
    try:
        await conn.execute(f"CREATE DATABASE {_quote_ident(db_name)}")
    finally:
        await conn.close()


async def _counts(db_url: str) -> dict[str, int]:
    conn = await asyncpg.connect(db_url)
    try:
        return {
            table: int(await conn.fetchval(f"SELECT COUNT(*) FROM pmfi.{table}") or 0)
            for table in ("raw_events", "normalized_trades", "metric_windows", "alerts")
        }
    finally:
        await conn.close()


def test_backup_restore_round_trip_to_scratch_db(tmp_path: Path) -> None:
    from pmfi.commands.backup import create_backup
    from pmfi.commands.restore import restore_backup

    scratch_db = "pmfi_restore_test_wave1"
    backup_dir = tmp_path / "backups"

    async def _run() -> None:
        await _drop_database(scratch_db)
        await _create_database(scratch_db)
        try:
            source_counts = await _counts(_dsn())
            backup_path = create_backup(
                backup_dir=backup_dir,
                source_db="pmfi",
                configured_db_url=_dsn(),
            )
            assert backup_path.exists()
            assert backup_path.stat().st_size > 0
            dump_text = backup_path.read_text(encoding="utf-8", errors="ignore")
            assert "CREATE TABLE pmfi.raw_events" in dump_text

            restore_backup(
                backup_file=backup_path,
                target_db=scratch_db,
                force=False,
                configured_db_url=_dsn(),
            )
            restored_counts = await _counts(_dsn().rsplit("/", 1)[0] + f"/{scratch_db}")

            assert restored_counts == source_counts
        finally:
            await _drop_database(scratch_db)

    asyncio.run(_run())
