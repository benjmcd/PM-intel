from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pmfi.commands._shared import ROOT, is_loopback_db_url
from pmfi.config import load_config

COMPOSE_PROJECT = os.environ.get("PMFI_COMPOSE_PROJECT", "pm-intel")
COMPOSE = ["docker", "compose", "-p", COMPOSE_PROJECT, "-f", "docker-compose.local.yml"]
POSTGRES_PORT = "5433"
POSTGRES_USER = os.environ.get("POSTGRES_USER", "pmfi")
DEFAULT_DB = os.environ.get("POSTGRES_DB", "pmfi")


class BackupRestoreError(RuntimeError):
    pass


def ensure_loopback_database_url(db_url: str | None) -> None:
    if not is_loopback_db_url(db_url):
        raise BackupRestoreError("backup/restore commands require a loopback database URL")


def database_name_from_url(db_url: str | None, *, default: str = DEFAULT_DB) -> str:
    if not db_url:
        return default
    parsed = urlsplit(db_url)
    name = parsed.path.lstrip("/").split("/", 1)[0]
    return name or default


def resolve_backup_dir(raw_dir: str | Path | None) -> Path:
    cfg = load_config()
    backup_dir = Path(raw_dir or cfg.backup.backup_dir)
    if not backup_dir.is_absolute():
        backup_dir = ROOT / backup_dir
    return backup_dir


def build_pg_dump_command(source_db: str) -> list[str]:
    return [
        *COMPOSE,
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "-p",
        POSTGRES_PORT,
        "-U",
        POSTGRES_USER,
        "-d",
        source_db,
        "--schema=pmfi",
    ]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def create_backup(
    *,
    backup_dir: str | Path | None = None,
    source_db: str | None = None,
    configured_db_url: str | None = None,
) -> Path:
    cfg = load_config()
    db_url = configured_db_url or cfg.database.url
    ensure_loopback_database_url(db_url)
    db_name = source_db or database_name_from_url(db_url)
    target_dir = resolve_backup_dir(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"pmfi-{db_name}-{_timestamp()}.sql"
    command = build_pg_dump_command(db_name)
    with target_path.open("wb") as out:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.PIPE,
            check=False,
        )
    if completed.returncode != 0:
        target_path.unlink(missing_ok=True)
        stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr else ""
        raise BackupRestoreError(f"pg_dump failed with exit {completed.returncode}: {stderr.strip()}")
    if target_path.stat().st_size <= 0:
        target_path.unlink(missing_ok=True)
        raise BackupRestoreError("pg_dump produced an empty backup file")
    return target_path


def cmd_backup(args) -> int:
    try:
        path = create_backup(
            backup_dir=getattr(args, "backup_dir", None),
            source_db=getattr(args, "source_db", None),
        )
    except BackupRestoreError as exc:
        print(f"backup refused: {exc}")
        return 1
    print(path)
    return 0
