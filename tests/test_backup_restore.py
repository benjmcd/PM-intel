from __future__ import annotations

from pathlib import Path

import pytest


def test_backup_config_defaults_and_example_yaml() -> None:
    from pmfi.config import load_config

    cfg = load_config(Path("config/app.example.yaml"))

    assert cfg.backup.backup_dir == ".pmfi-backups"
    assert cfg.backup.retention_days is None


def test_cli_parses_backup_and_restore_commands(tmp_path: Path) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()
    backup_args = parser.parse_args(["backup", "--backup-dir", str(tmp_path)])
    restore_args = parser.parse_args([
        "restore",
        str(tmp_path / "pmfi.sql"),
        "--target-db",
        "pmfi_restore_test",
    ])

    assert backup_args.command == "backup"
    assert backup_args.backup_dir == str(tmp_path)
    assert restore_args.command == "restore"
    assert restore_args.backup_file == str(tmp_path / "pmfi.sql")
    assert restore_args.target_db == "pmfi_restore_test"


def test_backup_rejects_non_loopback_configured_db() -> None:
    from pmfi.commands.backup import BackupRestoreError, ensure_loopback_database_url

    try:
        ensure_loopback_database_url("postgresql://pmfi:secret@db.example.com:15433/pmfi")
    except BackupRestoreError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("expected non-loopback DB URL to be rejected")


def test_backup_rejects_unsafe_source_db_name() -> None:
    from pmfi.commands.backup import BackupRestoreError, validate_source_db

    assert validate_source_db(None, default="pmfi") == "pmfi"
    assert validate_source_db("pmfi_restore_safe", default="pmfi") == "pmfi_restore_safe"

    try:
        validate_source_db("pmfi;drop", default="pmfi")
    except BackupRestoreError as exc:
        assert "source database name" in str(exc)
    else:
        raise AssertionError("expected unsafe source DB name to be rejected")


def test_restore_requires_target_db_and_force_for_primary(tmp_path: Path) -> None:
    from pmfi.commands.restore import BackupRestoreError, validate_restore_target

    primary_url = "postgresql://pmfi:pw@localhost:5433/pmfi"
    backup_file = tmp_path / "pmfi.sql"
    backup_file.write_text("-- dump\n", encoding="utf-8")

    try:
        validate_restore_target(backup_file, target_db=None, force=False, primary_db_url=primary_url)
    except BackupRestoreError as exc:
        assert "--target-db" in str(exc)
    else:
        raise AssertionError("expected missing target DB to be rejected")

    try:
        validate_restore_target(backup_file, target_db="pmfi", force=False, primary_db_url=primary_url)
    except BackupRestoreError as exc:
        assert str(exc) == "refusing to restore over the configured primary DB without --force"
    else:
        raise AssertionError("expected primary restore without force to be rejected")

    assert validate_restore_target(
        backup_file,
        target_db="pmfi_restore_safe",
        force=False,
        primary_db_url=primary_url,
    ) == "pmfi_restore_safe"


def test_restore_force_help_warns_about_primary_overwrite(capsys) -> None:
    from pmfi.cli import _build_parser

    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["restore", "backup.sql", "--help"])

    out = capsys.readouterr().out

    assert "--force" in out
    assert "overwrite" in out.lower()
    assert "primary" in out.lower()


def test_backup_and_restore_commands_use_local_docker_compose() -> None:
    from pmfi.commands.backup import build_pg_dump_command
    from pmfi.commands.restore import build_psql_restore_command

    dump_cmd = build_pg_dump_command("pmfi")
    restore_cmd = build_psql_restore_command("pmfi_restore_safe")

    assert dump_cmd[:6] == ["docker", "compose", "-p", "pm-intel", "-f", "docker-compose.local.yml"]
    assert "pg_dump" in dump_cmd
    assert "--no-owner" in dump_cmd
    assert "--no-privileges" in dump_cmd
    assert dump_cmd[-7:] == ["-p", "5433", "-U", "pmfi", "-d", "pmfi", "--schema=pmfi"]
    assert restore_cmd[-8:] == [
        "-v",
        "ON_ERROR_STOP=1",
        "-p",
        "5433",
        "-U",
        "pmfi",
        "-d",
        "pmfi_restore_safe",
    ]
