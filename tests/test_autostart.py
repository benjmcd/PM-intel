"""Offline tests for scripts/autostart.py.

All tests use --dry-run semantics: they assert the constructed schtasks command
shape without executing any subprocess.  The subprocess module is patched to
assert it is never called during dry-run mode.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Load the script as a module (same pattern as test_consistency_audit.py)
# ---------------------------------------------------------------------------

def _load_autostart():
    spec = importlib.util.spec_from_file_location(
        "autostart", ROOT / "scripts" / "autostart.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


autostart = _load_autostart()


# ---------------------------------------------------------------------------
# _build_install_cmd
# ---------------------------------------------------------------------------

class TestBuildInstallCmd:
    def _cmd(self, trigger="onlogon", task_name="PMFI Ingest"):
        pmfi = Path(r"C:\repo\.venv\Scripts\pmfi.exe")
        log = Path(r"C:\repo\reports\logs\pmfi.log")
        return autostart._build_install_cmd(task_name, pmfi, log, trigger)

    def test_contains_schtasks(self):
        cmd = self._cmd()
        assert cmd[0] == "schtasks"

    def test_contains_create(self):
        cmd = self._cmd()
        assert "/Create" in cmd

    def test_contains_task_name(self):
        cmd = self._cmd()
        assert "PMFI Ingest" in cmd

    def test_contains_onlogon_trigger(self):
        cmd = self._cmd(trigger="onlogon")
        assert "ONLOGON" in cmd

    def test_contains_force_flag(self):
        cmd = self._cmd()
        assert "/F" in cmd

    def test_contains_pmfi_exe_path(self):
        cmd = self._cmd()
        # The exe path must appear inside the /TR value
        tr_idx = cmd.index("/TR")
        tr_value = cmd[tr_idx + 1]
        assert r"C:\repo\.venv\Scripts\pmfi.exe" in tr_value

    def test_contains_ingest_subcommand(self):
        cmd = self._cmd()
        tr_idx = cmd.index("/TR")
        tr_value = cmd[tr_idx + 1]
        assert "ingest" in tr_value

    def test_contains_log_file_flag(self):
        cmd = self._cmd()
        tr_idx = cmd.index("/TR")
        tr_value = cmd[tr_idx + 1]
        assert "--log-file" in tr_value

    def test_onstart_trigger_variant(self):
        cmd = self._cmd(trigger="onstart")
        assert "ONSTART" in cmd
        assert "ONLOGON" not in cmd

    def test_custom_task_name_respected(self):
        cmd = self._cmd(task_name="My Custom Task")
        assert "My Custom Task" in cmd
        assert "PMFI Ingest" not in cmd


# ---------------------------------------------------------------------------
# _build_uninstall_cmd
# ---------------------------------------------------------------------------

class TestBuildUninstallCmd:
    def _cmd(self, task_name="PMFI Ingest"):
        return autostart._build_uninstall_cmd(task_name)

    def test_contains_schtasks(self):
        assert self._cmd()[0] == "schtasks"

    def test_contains_delete(self):
        assert "/Delete" in self._cmd()

    def test_contains_force_flag(self):
        assert "/F" in self._cmd()

    def test_contains_task_name(self):
        assert "PMFI Ingest" in self._cmd()

    def test_custom_task_name_respected(self):
        cmd = self._cmd(task_name="Other Task")
        assert "Other Task" in cmd
        assert "PMFI Ingest" not in cmd


# ---------------------------------------------------------------------------
# dry-run: subprocess is never called
# ---------------------------------------------------------------------------

class TestDryRunNoSubprocess:
    """When --dry-run is passed, subprocess.run must never be called."""

    def _run_install_dry(self, trigger="onlogon", task_name="PMFI Ingest"):
        pmfi = ROOT / ".venv" / "Scripts" / "pmfi.exe"
        log = ROOT / "reports" / "logs" / "pmfi.log"
        with patch("subprocess.run") as mock_run:
            rc = autostart._cmd_install(
                task_name=task_name,
                pmfi_exe=pmfi,
                log_file=log,
                trigger=trigger,
                dry_run=True,
            )
            mock_run.assert_not_called()
        return rc

    def test_install_dry_run_returns_zero(self):
        assert self._run_install_dry() == 0

    def test_install_dry_run_subprocess_not_called(self):
        self._run_install_dry()  # assertion is inside _run_install_dry

    def test_uninstall_dry_run_subprocess_not_called(self):
        with patch("subprocess.run") as mock_run:
            rc = autostart._cmd_uninstall(task_name="PMFI Ingest", dry_run=True)
            mock_run.assert_not_called()
        assert rc == 0

    def test_onstart_dry_run_no_subprocess(self):
        self._run_install_dry(trigger="onstart")

    def test_custom_task_name_dry_run_no_subprocess(self):
        self._run_install_dry(task_name="Custom Task Name")


# ---------------------------------------------------------------------------
# main() CLI integration (dry-run path, no subprocess)
# ---------------------------------------------------------------------------

class TestMainCLI:
    def _invoke(self, argv: list[str]) -> int:
        with patch("subprocess.run") as mock_run:
            rc = autostart.main(argv)
            return rc, mock_run

    def test_install_dry_run_via_main(self, capsys):
        rc, mock_run = self._invoke(["install", "--dry-run"])
        assert rc == 0
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "schtasks" in out
        assert "/Create" in out
        assert "ONLOGON" in out
        assert "/F" in out
        assert "ingest" in out
        assert "--log-file" in out

    def test_uninstall_dry_run_via_main(self, capsys):
        rc, mock_run = self._invoke(["uninstall", "--dry-run"])
        assert rc == 0
        mock_run.assert_not_called()
        out = capsys.readouterr().out
        assert "/Delete" in out
        assert "/F" in out
        assert "PMFI Ingest" in out

    def test_install_onstart_via_main(self, capsys):
        rc, mock_run = self._invoke(["install", "--trigger", "onstart", "--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ONSTART" in out
        assert "ONLOGON" not in out

    def test_install_custom_task_name_via_main(self, capsys):
        rc, mock_run = self._invoke(
            ["install", "--task-name", "My PMFI Task", "--dry-run"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "My PMFI Task" in out

    def test_uninstall_custom_task_name_via_main(self, capsys):
        rc, mock_run = self._invoke(
            ["uninstall", "--task-name", "My PMFI Task", "--dry-run"]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "My PMFI Task" in out
