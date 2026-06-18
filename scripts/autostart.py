from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
TASK_NAME = r"\PMFI\Lane12LocalIngest"
LOG_RELATIVE = Path("reports") / "logs" / "autostart" / "lane12-ingest.log"
RECOVERY_COMMANDS = [
    r"python scripts\db_local.py up",
    r"python scripts\db_local.py verify",
    r"python scripts\task.py db-smoke",
]
DEPENDENCY_WARNING = (
    "This autostart action is local-only and depends on Docker Desktop local "
    "Postgres being available before ingest can run successfully."
)

Runner = Callable[[list[str]], tuple[int, str, str]]


def _quote_cmd(value: Path | str) -> str:
    text = str(value)
    return f'""{text.replace(chr(34), chr(34) + chr(34))}""'


def _default_runner(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _missing_task(returncode: int, stdout: str, stderr: str) -> bool:
    if returncode == 0:
        return False
    output = f"{stdout}\n{stderr}".lower()
    return (
        "cannot find" in output
        or "does not exist" in output
        or "not found" in output
        or "no scheduled task" in output
    )


def build_plan(repo_root: Path | None = None, python_executable: Path | None = None) -> dict:
    root = (repo_root or ROOT).resolve()
    python_path = (python_executable or Path(sys.executable)).resolve()
    log_path = (root / LOG_RELATIVE).resolve()
    command = (
        "cmd.exe /d /s /c "
        f'"cd /d {_quote_cmd(root)} && {_quote_cmd(python_path)} '
        f'-m pmfi.cli ingest >> {_quote_cmd(log_path)} 2>&1"'
    )
    return {
        "task_name": TASK_NAME,
        "repo_root": str(root),
        "python_executable": str(python_path),
        "working_directory": str(root),
        "scheduled_task_command": command,
        "log_path": str(log_path),
        "action": "local_operator_ingest",
        "recovery_commands": RECOVERY_COMMANDS,
        "dependency_warning": DEPENDENCY_WARNING,
        "windows_only": True,
    }


def status_task(*, runner: Runner = _default_runner) -> dict:
    args = ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/FO", "LIST"]
    returncode, stdout, stderr = runner(args)
    if returncode == 0:
        return {
            "ok": True,
            "installed": True,
            "status": "installed",
            "task_name": TASK_NAME,
            "stdout": stdout,
        }
    if _missing_task(returncode, stdout, stderr):
        return {
            "ok": True,
            "installed": False,
            "status": "missing",
            "task_name": TASK_NAME,
        }
    return {
        "ok": False,
        "installed": None,
        "status": "error",
        "task_name": TASK_NAME,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def uninstall_task(*, runner: Runner = _default_runner) -> dict:
    args = ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"]
    returncode, stdout, stderr = runner(args)
    if returncode == 0:
        return {
            "ok": True,
            "changed": True,
            "status": "removed",
            "task_name": TASK_NAME,
            "stdout": stdout,
        }
    if _missing_task(returncode, stdout, stderr):
        return {
            "ok": True,
            "changed": False,
            "status": "missing",
            "task_name": TASK_NAME,
        }
    return {
        "ok": False,
        "changed": None,
        "status": "error",
        "task_name": TASK_NAME,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def install_task(*, runner: Runner = _default_runner, plan: dict | None = None) -> dict:
    plan = build_plan() if plan is None else plan
    Path(plan["log_path"]).parent.mkdir(parents=True, exist_ok=True)
    args = [
        "schtasks.exe",
        "/Create",
        "/TN",
        str(plan["task_name"]),
        "/SC",
        "ONLOGON",
        "/TR",
        str(plan["scheduled_task_command"]),
        "/RL",
        "LIMITED",
        "/F",
    ]
    returncode, stdout, stderr = runner(args)
    if returncode == 0:
        return {
            "ok": True,
            "changed": True,
            "status": "installed",
            "task_name": plan["task_name"],
            "plan": plan,
            "stdout": stdout,
        }
    return {
        "ok": False,
        "changed": False,
        "status": "error",
        "task_name": plan["task_name"],
        "plan": plan,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _print_payload(payload: dict, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    status = payload.get("status", "ok" if payload.get("ok") else "error")
    print(f"PMFI autostart {status}")
    if "plan" in payload:
        plan = payload["plan"]
        print(f"task: {plan['task_name']}")
        print(f"command: {plan['scheduled_task_command']}")
        print(f"log: {plan['log_path']}")
        print("recovery:")
        for command in plan["recovery_commands"]:
            print(f"  {command}")
        print(plan["dependency_warning"])


def main(argv: list[str] | None = None, *, runner: Runner = _default_runner) -> int:
    parser = argparse.ArgumentParser(description="Plan and manage PMFI Windows autostart.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_format(p: argparse.ArgumentParser) -> None:
        p.add_argument("--format", choices=["table", "json"], default="table")

    add_format(sub.add_parser("plan", help="Print the non-mutating autostart plan."))
    add_format(sub.add_parser("status", help="Check the scheduled task without changing it."))
    install = sub.add_parser("install", help="Create or update the scheduled task.")
    add_format(install)
    install.add_argument(
        "--confirm-mutation",
        action="store_true",
        help="Required before registering the Windows scheduled task.",
    )
    add_format(sub.add_parser("uninstall", help="Remove the scheduled task if present."))

    args = parser.parse_args(argv)
    output_format = args.format

    if args.command == "plan":
        payload = {"ok": True, "mutation": "none", "plan": build_plan()}
        _print_payload(payload, output_format)
        return 0
    if args.command == "status":
        payload = status_task(runner=runner)
        _print_payload(payload, output_format)
        return 0 if payload["ok"] else 1
    if args.command == "uninstall":
        payload = uninstall_task(runner=runner)
        _print_payload(payload, output_format)
        return 0 if payload["ok"] else 1
    if args.command == "install":
        if not args.confirm_mutation:
            payload = {
                "ok": False,
                "status": "blocked",
                "error": "install requires --confirm-mutation because it registers a Windows scheduled task",
                "mutation": "blocked",
                "plan": build_plan(),
            }
            _print_payload(payload, output_format)
            return 2
        payload = install_task(runner=runner)
        _print_payload(payload, output_format)
        return 0 if payload["ok"] else 1
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
