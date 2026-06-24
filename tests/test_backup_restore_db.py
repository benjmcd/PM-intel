from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest

from db_scratch import ScratchDatabase, create_test_scratch_database, drop_test_scratch_database
from pmfi.commands.backup import database_name_from_url

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


async def _counts(db_url: str) -> dict[str, int]:
    conn = await asyncpg.connect(db_url)
    try:
        return {
            table: int(await conn.fetchval(f"SELECT COUNT(*) FROM pmfi.{table}") or 0)
            for table in (
                "raw_events",
                "normalized_trades",
                "metric_windows",
                "alerts",
                "dead_letters",
                "venues",
            )
        }
    finally:
        await conn.close()


async def _seed_sentinel_raw_event(db_url: str, source_event_id: str) -> None:
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            INSERT INTO pmfi.raw_events (
                venue_code,
                source_channel,
                source_event_type,
                source_event_id,
                parser_version,
                payload,
                ingest_node
            )
            VALUES (
                'polymarket',
                'backup_restore_test',
                'scratch_source_sentinel',
                $1,
                'backup-test.v1',
                $2::jsonb,
                'test_backup_restore_db'
            )
            """,
            source_event_id,
            json.dumps({"sentinel": source_event_id, "source": "scratch"}),
        )
    finally:
        await conn.close()


async def _sentinel_payload(db_url: str, source_event_id: str) -> dict[str, str] | None:
    conn = await asyncpg.connect(db_url)
    try:
        row = await conn.fetchrow(
            """
            SELECT
                source_channel,
                source_event_type,
                payload ->> 'sentinel' AS sentinel,
                payload ->> 'source' AS source
            FROM pmfi.raw_events
            WHERE source_event_id = $1
            """,
            source_event_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


def _drop_scratch_databases(*scratches: ScratchDatabase | None) -> None:
    cleanup_errors: list[Exception] = []
    for scratch in scratches:
        if scratch is None:
            continue
        try:
            drop_test_scratch_database(scratch)
        except Exception as exc:  # pragma: no cover - cleanup failure should surface after all drops.
            cleanup_errors.append(exc)
    if cleanup_errors:
        raise cleanup_errors[0]


def test_backup_restore_round_trip_to_scratch_db(tmp_path: Path) -> None:
    from pmfi.commands.backup import create_backup
    from pmfi.commands.restore import restore_backup

    source_scratch: ScratchDatabase | None = None
    target_scratch: ScratchDatabase | None = None
    backup_dir = tmp_path / "backups"
    sentinel_event_id = "backup-source-scratch-sentinel"

    async def _run() -> None:
        assert source_scratch is not None
        assert target_scratch is not None
        await _seed_sentinel_raw_event(source_scratch.dsn, sentinel_event_id)
        source_counts = await _counts(source_scratch.dsn)
        assert source_counts["raw_events"] == 1

        backup_path = create_backup(
            backup_dir=backup_dir,
            source_db=source_scratch.name,
            configured_db_url=_dsn(),
        )
        assert backup_path.exists()
        assert backup_path.stat().st_size > 0
        assert source_scratch.name in backup_path.name
        dump_text = backup_path.read_text(encoding="utf-8", errors="ignore")
        assert "CREATE TABLE pmfi.raw_events" in dump_text
        assert sentinel_event_id in dump_text

        restore_backup(
            backup_file=backup_path,
            target_db=target_scratch.name,
            force=False,
            configured_db_url=_dsn(),
        )
        restored_counts = await _counts(target_scratch.dsn)
        restored_sentinel = await _sentinel_payload(target_scratch.dsn, sentinel_event_id)

        assert restored_counts == source_counts
        assert restored_sentinel == {
            "source_channel": "backup_restore_test",
            "source_event_type": "scratch_source_sentinel",
            "sentinel": sentinel_event_id,
            "source": "scratch",
        }

    try:
        source_scratch = create_test_scratch_database("backup_src")
        target_scratch = create_test_scratch_database("backup_tgt", init_schema=False)
        configured_primary = database_name_from_url(_dsn())
        assert source_scratch.name.startswith("pmfi_testiso_backup_src_")
        assert target_scratch.name.startswith("pmfi_testiso_backup_tgt_")
        assert source_scratch.name != configured_primary
        assert target_scratch.name != configured_primary
        assert source_scratch.name != target_scratch.name

        asyncio.run(_run())
    finally:
        _drop_scratch_databases(target_scratch, source_scratch)
