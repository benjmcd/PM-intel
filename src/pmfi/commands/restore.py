from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pmfi.commands._shared import ROOT
from pmfi.commands.backup import (
    COMPOSE,
    POSTGRES_PORT,
    POSTGRES_USER,
    BackupRestoreError,
    database_name_from_url,
    ensure_loopback_database_url,
)
from pmfi.config import load_config

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def build_psql_restore_command(target_db: str) -> list[str]:
    return [
        *COMPOSE,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-p",
        POSTGRES_PORT,
        "-U",
        POSTGRES_USER,
        "-d",
        target_db,
    ]


def validate_restore_target(
    backup_file: str | Path,
    *,
    target_db: str | None,
    force: bool,
    primary_db_url: str | None,
) -> str:
    path = Path(backup_file)
    if not path.exists() or not path.is_file():
        raise BackupRestoreError(f"backup file not found: {path}")
    if not target_db:
        raise BackupRestoreError("restore requires an explicit --target-db scratch database name")
    if not _DB_NAME_RE.fullmatch(target_db):
        raise BackupRestoreError("target database name must be a simple PostgreSQL identifier")
    primary_db = database_name_from_url(primary_db_url)
    if target_db == primary_db and not force:
        raise BackupRestoreError(
            "refusing to restore over the configured primary DB without --target-db and --force"
        )
    return target_db


def restore_backup(
    *,
    backup_file: str | Path,
    target_db: str | None,
    force: bool = False,
    configured_db_url: str | None = None,
) -> None:
    cfg = load_config()
    db_url = configured_db_url or cfg.database.url
    ensure_loopback_database_url(db_url)
    target = validate_restore_target(
        backup_file,
        target_db=target_db,
        force=force,
        primary_db_url=db_url,
    )
    command = build_psql_restore_command(target)
    with Path(backup_file).open("rb") as src:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdin=src,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr else ""
        raise BackupRestoreError(f"psql restore failed with exit {completed.returncode}: {stderr.strip()}")


def cmd_restore(args) -> int:
    try:
        restore_backup(
            backup_file=getattr(args, "backup_file", None),
            target_db=getattr(args, "target_db", None),
            force=bool(getattr(args, "force", False)),
        )
    except BackupRestoreError as exc:
        print(f"restore refused: {exc}")
        return 1
    print(f"restored {args.backup_file} into database {args.target_db}")
    return 0
