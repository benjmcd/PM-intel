from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_autostart():
    path = ROOT / "scripts" / "autostart.py"
    spec = importlib.util.spec_from_file_location("autostart", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, *, status_code: int = 1, uninstall_code: int = 1):
        self.calls: list[list[str]] = []
        self.status_code = status_code
        self.uninstall_code = uninstall_code

    def __call__(self, args: list[str]):
        self.calls.append(args)
        text = "ERROR: The system cannot find the file specified."
        code = self.status_code if "/Query" in args else self.uninstall_code
        return code, "", text


def test_build_plan_is_absolute_local_and_recovery_oriented():
    autostart = _load_autostart()
    plan = autostart.build_plan(ROOT, Path(sys.executable))

    assert plan["task_name"] == r"\PMFI\Lane12LocalIngest"
    assert Path(plan["repo_root"]).is_absolute()
    assert Path(plan["python_executable"]).is_absolute()
    assert Path(plan["working_directory"]).resolve() == ROOT.resolve()
    assert Path(plan["log_path"]).is_absolute()
    assert Path(plan["log_path"]).is_relative_to(ROOT / "reports" / "logs" / "autostart")
    assert plan["action"] == "local_operator_ingest"
    assert "Docker Desktop" in plan["dependency_warning"]
    assert "Postgres" in plan["dependency_warning"]
    assert plan["recovery_commands"] == [
        r"python scripts\db_local.py up",
        r"python scripts\db_local.py verify",
        r"python scripts\task.py db-smoke",
    ]

    command = plan["scheduled_task_command"]
    assert str(ROOT.resolve()) in command
    assert str(Path(sys.executable).resolve()) in command
    assert " -m pmfi.cli ingest" in command
    assert "cd /d" in command
    assert ">>" in command
    assert str(Path(plan["log_path"])) in command


def test_status_and_uninstall_missing_task_are_idempotent():
    autostart = _load_autostart()
    runner = FakeRunner()

    status = autostart.status_task(runner=runner)
    uninstall = autostart.uninstall_task(runner=runner)

    assert status["ok"] is True
    assert status["installed"] is False
    assert status["status"] == "missing"
    assert uninstall["ok"] is True
    assert uninstall["changed"] is False
    assert uninstall["status"] == "missing"
    assert runner.calls == [
        ["schtasks.exe", "/Query", "/TN", r"\PMFI\Lane12LocalIngest", "/FO", "LIST"],
        ["schtasks.exe", "/Delete", "/TN", r"\PMFI\Lane12LocalIngest", "/F"],
    ]


def test_plan_command_prints_json_without_runner_mutation(capsys):
    autostart = _load_autostart()
    runner = FakeRunner()

    rc = autostart.main(["plan", "--format", "json"], runner=runner)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["mutation"] == "none"
    assert payload["plan"]["task_name"] == r"\PMFI\Lane12LocalIngest"
    assert runner.calls == []


def test_install_requires_explicit_mutation_flag(capsys):
    autostart = _load_autostart()

    rc = autostart.main(["install", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 2
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert "confirm-mutation" in payload["error"]
