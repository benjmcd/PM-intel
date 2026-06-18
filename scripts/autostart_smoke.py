from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import autostart

ROOT = SCRIPT_DIR.parents[0]

EXPECTED_CHECKS = (
    "dry_run_plan_shape",
    "absolute_path_safety",
    "repo_local_log_path",
    "db_recovery_instructions",
    "docker_postgres_dependency_warning",
    "missing_status_idempotent",
    "missing_uninstall_idempotent",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _pass(name: str, details: dict) -> dict:
    return {"name": name, "status": "pass", "details": details}


def _missing_runner(args: list[str]) -> tuple[int, str, str]:
    return 1, "", "ERROR: The system cannot find the file specified."


def _check_plan_shape(plan: dict) -> dict:
    required = (
        "task_name",
        "repo_root",
        "python_executable",
        "working_directory",
        "scheduled_task_command",
        "log_path",
        "recovery_commands",
        "dependency_warning",
    )
    missing = [name for name in required if not plan.get(name)]
    _require(not missing, "autostart plan missing field(s): " + ", ".join(missing))
    _require(plan.get("task_name") == autostart.TASK_NAME, "autostart plan task name mismatch")
    _require(plan.get("action") == "local_operator_ingest", "autostart plan must target local operator ingest")
    return _pass("dry_run_plan_shape", {"task_name": plan["task_name"], "action": plan["action"]})


def _check_absolute_paths(plan: dict) -> dict:
    path_fields = ("repo_root", "python_executable", "working_directory", "log_path")
    for field in path_fields:
        _require(Path(plan[field]).is_absolute(), f"autostart plan {field} must be absolute")
    command = plan["scheduled_task_command"]
    _require("cd /d" in command, "scheduled task command must set a working directory")
    _require(str(Path(plan["repo_root"])) in command, "scheduled task command missing absolute repo root")
    _require(str(Path(plan["python_executable"])) in command, "scheduled task command missing absolute Python path")
    _require(" -m pmfi.cli ingest" in command, "scheduled task command must launch pmfi.cli ingest")
    return _pass("absolute_path_safety", {"command": command})


def _check_log_path(plan: dict) -> dict:
    log_path = Path(plan["log_path"])
    expected_root = Path(plan["repo_root"]) / "reports" / "logs" / "autostart"
    _require(log_path.is_relative_to(expected_root), "autostart log path must stay under reports/logs/autostart")
    command = plan["scheduled_task_command"]
    _require(">>" in command and "2>&1" in command, "scheduled task command must redirect stdout and stderr")
    _require(str(log_path) in command, "scheduled task command missing log path")
    return _pass("repo_local_log_path", {"log_path": str(log_path)})


def _check_recovery(plan: dict) -> dict:
    commands = plan["recovery_commands"]
    _require(commands == autostart.RECOVERY_COMMANDS, "autostart recovery commands changed unexpectedly")
    return _pass("db_recovery_instructions", {"commands": commands})


def _check_dependency_warning(plan: dict) -> dict:
    warning = plan["dependency_warning"]
    _require("Docker Desktop" in warning, "autostart warning must mention Docker Desktop")
    _require("Postgres" in warning, "autostart warning must mention Postgres")
    return _pass("docker_postgres_dependency_warning", {"warning": warning})


def _check_missing_status(runner: Callable[[list[str]], tuple[int, str, str]]) -> dict:
    payload = autostart.status_task(runner=runner)
    _require(payload.get("ok") is True, "missing scheduled task status must be ok")
    _require(payload.get("installed") is False, "missing scheduled task status must be installed=false")
    _require(payload.get("status") == "missing", "missing scheduled task status must be missing")
    return _pass("missing_status_idempotent", {"status": payload["status"]})


def _check_missing_uninstall(runner: Callable[[list[str]], tuple[int, str, str]]) -> dict:
    payload = autostart.uninstall_task(runner=runner)
    _require(payload.get("ok") is True, "missing scheduled task uninstall must be ok")
    _require(payload.get("changed") is False, "missing scheduled task uninstall must be changed=false")
    _require(payload.get("status") == "missing", "missing scheduled task uninstall must be missing")
    return _pass("missing_uninstall_idempotent", {"status": payload["status"]})


def build_payload() -> dict:
    plan = autostart.build_plan(ROOT, Path(sys.executable))
    checks = [
        _check_plan_shape(plan),
        _check_absolute_paths(plan),
        _check_log_path(plan),
        _check_recovery(plan),
        _check_dependency_warning(plan),
        _check_missing_status(_missing_runner),
        _check_missing_uninstall(_missing_runner),
    ]
    return {
        "ok": True,
        "status": "pass",
        "source": "db_free_autostart_contracts",
        "checks": checks,
    }


def assert_autostart_smoke_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "autostart-smoke ok must be true")
    _require(payload.get("status") == "pass", "autostart-smoke status must be pass")
    _require(
        payload.get("source") == "db_free_autostart_contracts",
        "autostart-smoke source must be db_free_autostart_contracts",
    )
    checks = payload.get("checks")
    _require(isinstance(checks, list), "autostart-smoke checks must be a list")
    names = []
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"autostart-smoke check {index} must be an object")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"autostart-smoke check {index} missing name")
        names.append(name)
        _require(check.get("status") == "pass", f"autostart-smoke {name} check must pass")
        _require(isinstance(check.get("details"), dict), f"autostart-smoke {name} details must be an object")
    _require(
        tuple(names) == EXPECTED_CHECKS,
        "autostart-smoke checks must match exactly: " + ", ".join(EXPECTED_CHECKS),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate DB-free PMFI autostart plan contracts.")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args(argv)

    try:
        payload = build_payload()
        assert_autostart_smoke_payload(payload)
    except RuntimeError as exc:
        if args.format == "json":
            print(json.dumps({"ok": False, "status": "fail", "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"autostart smoke failed: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("autostart smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
