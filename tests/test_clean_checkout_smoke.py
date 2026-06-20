from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from scripts import clean_checkout_smoke


def _args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    values = {
        "ref": "HEAD",
        "worktree_dir": tmp_path / "worktrees" / "smoke",
        "report_dir": tmp_path / "reports",
        "timeout": 1,
        "install_dev": False,
        "run_verify": False,
        "db_verify": False,
        "keep_worktree": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_resolve_worktree_path_rejects_outside_repo_worktrees(tmp_path):
    with pytest.raises(ValueError):
        clean_checkout_smoke._resolve_inside_worktrees(tmp_path / "outside", root=tmp_path)


def test_smoke_commands_adds_heavy_checks_only_when_requested():
    basic = clean_checkout_smoke.smoke_commands(run_verify=False, db_verify=False)
    full = clean_checkout_smoke.smoke_commands(run_verify=True, db_verify=True, python_executable="venv-python")

    assert [sys.executable, "scripts/verify.py"] not in basic
    assert [sys.executable, "scripts/db_local.py", "verify"] not in basic
    assert ["venv-python", "scripts/verify.py"] in full
    assert ["venv-python", "scripts/db_local.py", "verify"] in full


def test_run_smoke_fails_before_touching_existing_worktree(tmp_path):
    target = tmp_path / "worktrees" / "smoke"
    target.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        clean_checkout_smoke.run_smoke(_args(tmp_path), root=tmp_path)


def test_run_smoke_writes_report_and_cleans_up_created_worktree(tmp_path, monkeypatch):
    calls: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path, timeout: int) -> clean_checkout_smoke.CommandResult:
        calls.append((command, cwd))
        return clean_checkout_smoke.CommandResult(command, str(cwd), 0, "", "")

    monkeypatch.setattr(clean_checkout_smoke, "_run", fake_run)

    payload, report_path = clean_checkout_smoke.run_smoke(
        _args(tmp_path, run_verify=True, db_verify=True),
        root=tmp_path,
    )

    assert payload["schema_version"] == "clean_checkout_smoke.v1"
    assert payload["success"] is True
    assert payload["run_verify"] is True
    assert payload["db_verify"] is True
    assert payload["cleanup"] is not None
    assert calls[0][0][:3] == ["git", "worktree", "add"]
    assert calls[-1][0][:3] == ["git", "worktree", "remove"]
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["success"] is True


def test_run_smoke_install_dev_uses_fresh_venv_and_forced_temp_cleanup(tmp_path, monkeypatch):
    calls: list[tuple[list[str], Path]] = []

    def fake_venv_python(target: Path) -> Path:
        return target / ".venv" / "Scripts" / "python.exe"

    def fake_run(command: list[str], *, cwd: Path, timeout: int) -> clean_checkout_smoke.CommandResult:
        calls.append((command, cwd))
        return clean_checkout_smoke.CommandResult(command, str(cwd), 0, "", "")

    monkeypatch.setattr(clean_checkout_smoke, "_run", fake_run)
    monkeypatch.setattr(clean_checkout_smoke, "_venv_python", fake_venv_python)

    payload, _report_path = clean_checkout_smoke.run_smoke(
        _args(tmp_path, install_dev=True, run_verify=True, db_verify=True),
        root=tmp_path,
    )

    target = tmp_path / "worktrees" / "smoke"
    venv_python = str(target / ".venv" / "Scripts" / "python.exe")
    commands = [command for command, _cwd in calls]
    assert [sys.executable, "-m", "venv", ".venv"] in commands
    assert [venv_python, "-m", "pip", "install", "-e", ".[dev]"] in commands
    assert [venv_python, "scripts/verify.py"] in commands
    assert [venv_python, "scripts/db_local.py", "verify"] in commands
    assert commands[-1][:4] == ["git", "worktree", "remove", "--force"]
    assert payload["install_dev"] is True
    assert payload["success"] is True
