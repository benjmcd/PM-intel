from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts import handoff, task


def _result(command: list[str], stdout: str = "", returncode: int = 0) -> handoff.CommandResult:
    return handoff.CommandResult(command=command, returncode=returncode, stdout=stdout, stderr="")


def test_redact_db_url_masks_credentials():
    redacted = handoff.redact_db_url("postgresql://pmfi:secret-pass@localhost:5433/pmfi")

    assert redacted == "postgresql://pmfi:***@localhost:5433/pmfi"
    assert "secret-pass" not in redacted


def test_redact_db_url_handles_malformed_port_without_leaking_password():
    redacted = handoff.redact_db_url("postgresql://pmfi:secret-pass@localhost:notaport/pmfi")

    assert redacted == "postgresql://pmfi:***@localhost:notaport/pmfi"
    assert "secret-pass" not in redacted


def test_collect_snapshot_skips_heavy_checks_by_default(tmp_path, monkeypatch):
    (tmp_path / "WORKLOG.md").write_text(
        "# Worklog\n\n## 2026-06-17 handoff\n\n### Goal\nKeep local evidence current.\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, timeout: int = 30, root: Path = tmp_path) -> handoff.CommandResult:
        calls.append(command)
        if command == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return _result(command, "main\n")
        if command == ["git", "rev-parse", "HEAD"]:
            return _result(command, "abc123\n")
        if command == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return _result(command, "origin/main\n")
        if command[:2] == ["git", "rev-list"]:
            return _result(command, "0 34\n")
        if command[:2] == ["git", "status"]:
            return _result(command, " M scripts/task.py\n")
        if command[:2] == ["git", "log"]:
            return _result(command, "abc123 Add handoff\n")
        if command == [sys.executable, "scripts/repo_status.py"]:
            return _result(command, "High-priority commands:\n- python scripts\\verify.py\n")
        raise AssertionError(command)

    monkeypatch.setenv("PMFI_DB_URL", "postgresql://pmfi:topsecret@localhost:5433/pmfi")
    monkeypatch.setattr(handoff, "run_command", fake_run)

    args = argparse.Namespace(db_verify=False, run_verify=False, db_timeout=1, verify_timeout=1)
    snapshot = handoff.collect_snapshot(args, tmp_path)

    assert snapshot["publication_performed"] is False
    assert snapshot["git"]["branch"] == "main"
    assert snapshot["git"]["ahead"] == 34
    assert snapshot["git"]["behind"] == 0
    assert snapshot["git"]["dirty"] is True
    assert snapshot["environment"]["pmfi_db_url"] == "postgresql://pmfi:***@localhost:5433/pmfi"
    assert snapshot["verification"]["db_verify"]["skipped"] is True
    assert snapshot["verification"]["db_verify"]["returncode"] is None
    assert snapshot["verification"]["default_verify"]["skipped"] is True
    assert snapshot["verification"]["default_verify"]["returncode"] is None
    assert [sys.executable, "scripts/db_local.py", "verify"] not in calls
    assert [sys.executable, "scripts/verify.py"] not in calls


def test_latest_worklog_entry_uses_first_prepended_entry(tmp_path):
    (tmp_path / "WORKLOG.md").write_text(
        "# Worklog\n\n"
        "## newest entry\n\n"
        "new body\n\n"
        "## older entry\n\n"
        "old body\n",
        encoding="utf-8",
    )

    entry = handoff.latest_worklog_entry(tmp_path)

    assert entry["heading"] == "newest entry"
    assert entry["excerpt"] == "new body"
    assert "older entry" not in entry["excerpt"]


def test_write_snapshot_creates_json_and_markdown(tmp_path):
    snapshot = {
        "schema_version": 1,
        "created_at": "2026-06-18T010203Z",
        "local_only": True,
        "publication_performed": False,
        "git": {
            "branch": "main",
            "head": "abc123",
            "upstream": "origin/main",
            "ahead": 34,
            "behind": 0,
            "dirty": False,
            "dirty_entries": [],
            "recent_commits": ["abc123 Add handoff"],
        },
        "worklog": {"heading": "2026-06-17 handoff", "excerpt": "Facts here."},
        "status": {"command": "python scripts/repo_status.py", "returncode": 0, "excerpt": "High-priority commands:"},
        "runtime": {"python": "3.11", "executable": "python", "platform": "Windows"},
        "environment": {"pmfi_db_url": "not_set", "note": "No environment dump is included."},
        "verification": {
            "recommended_commands": ["python scripts\\verify.py"],
            "db_verify": {
                "command": ["python", "scripts/db_local.py", "verify"],
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "skipped": True,
                "reason": "skip",
            },
            "default_verify": {
                "command": ["python", "scripts/verify.py"],
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "skipped": True,
                "reason": "skip",
            },
        },
    }

    json_path, md_path = handoff.write_snapshot(snapshot, tmp_path)

    assert json_path.name == "handoff-20260618T010203Z.json"
    assert md_path.name == "handoff-20260618T010203Z.md"
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["git"]["ahead"] == 34
    markdown = md_path.read_text(encoding="utf-8")
    assert "PMFI Local Handoff Snapshot" in markdown
    assert "Publication performed: no" in markdown
    assert "Environment variables were not dumped." in markdown


def test_db_verify_flag_records_nonfatal_failure(tmp_path, monkeypatch):
    def fake_run(command: list[str], *, timeout: int = 30, root: Path = tmp_path) -> handoff.CommandResult:
        if command == [sys.executable, "scripts/db_local.py", "verify"]:
            return handoff.CommandResult(command, 1, "", "docker missing\n")
        if command == [sys.executable, "scripts/verify.py"]:
            raise AssertionError("default verify should not run")
        return _result(command)

    monkeypatch.setattr(handoff, "run_command", fake_run)
    args = argparse.Namespace(db_verify=True, run_verify=False, db_timeout=1, verify_timeout=1)

    verification = handoff.collect_verification(args, tmp_path)

    assert verification["db_verify"]["returncode"] == 1
    assert verification["db_verify"]["skipped"] is False
    assert "docker missing" in verification["db_verify"]["stderr"]
    assert verification["default_verify"]["skipped"] is True


def test_task_routes_handoff_arguments(monkeypatch):
    routed: list[tuple[str, tuple[str, ...]]] = []

    def fake_python_script(script: str, *args: str) -> None:
        routed.append((script, args))

    monkeypatch.setattr(task, "python_script", fake_python_script)

    rc = task.main(["handoff", "--no-db-verify", "--output-dir", "reports\\handoff-test"])

    assert rc == 0
    assert routed == [
        (
            "scripts/handoff.py",
            ("--output-dir", "reports\\handoff-test", "--no-db-verify"),
        )
    ]
