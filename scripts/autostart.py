r"""Windows Scheduled Task manager for the PMFI ingest daemon.

Registers, removes, and queries a Windows Scheduled Task named "PMFI Ingest"
(default) that runs ``pmfi ingest --log-file <log>`` on user logon.

Usage
-----
  python scripts\autostart.py install
  python scripts\autostart.py install --dry-run
  python scripts\autostart.py uninstall
  python scripts\autostart.py uninstall --dry-run
  python scripts\autostart.py status

Why absolute paths everywhere
------------------------------
Windows Scheduled Tasks do not inherit a working directory from the terminal
that registers them.  ``schtasks /Create`` has no reliable /WorkingDirectory
flag across all Windows editions.

Strategy: pass absolute paths for both the pmfi executable and the log file so
the task never depends on a cwd.  The executable resolves to
``.venv\Scripts\pmfi.exe`` inside this repo and the default log file resolves
to ``reports\logs\pmfi.log`` inside the same repo root.

ONSTART vs ONLOGON
-------------------
* ONLOGON (default) -- fires when *any* user logs on; does not need elevated
  privileges to register.
* ONSTART -- fires when Windows boots, before any logon; requires the task to
  be registered from an **elevated** (Administrator) prompt.  The daemon will
  also run in SYSTEM context unless /RU is overridden.  Note it in the
  /Create output.

Re-running install is idempotent: /F overwrites the existing task definition.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo layout
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PMFI_EXE = ROOT / ".venv" / "Scripts" / "pmfi.exe"
DEFAULT_LOG_FILE = ROOT / "reports" / "logs" / "pmfi.log"
DEFAULT_TASK_NAME = "PMFI Ingest"


# ---------------------------------------------------------------------------
# Command builders
# ---------------------------------------------------------------------------

def _build_install_cmd(
    task_name: str,
    pmfi_exe: Path,
    log_file: Path,
    trigger: str,
) -> list[str]:
    """Return the schtasks /Create argument list (no shell=True needed)."""
    sched_trigger = "ONLOGON" if trigger == "onlogon" else "ONSTART"
    run_cmd = f'"{pmfi_exe}" ingest --log-file "{log_file}"'
    return [
        "schtasks",
        "/Create",
        "/TN", task_name,
        "/TR", run_cmd,
        "/SC", sched_trigger,
        "/F",
    ]


def _build_uninstall_cmd(task_name: str) -> list[str]:
    return [
        "schtasks",
        "/Delete",
        "/TN", task_name,
        "/F",
    ]


def _build_status_cmd(task_name: str) -> list[str]:
    return [
        "schtasks",
        "/Query",
        "/TN", task_name,
        "/FO", "LIST",
    ]


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def _cmd_install(
    task_name: str,
    pmfi_exe: Path,
    log_file: Path,
    trigger: str,
    dry_run: bool,
) -> int:
    cmd = _build_install_cmd(task_name, pmfi_exe, log_file, trigger)
    _print_cmd(cmd)

    if trigger == "onstart":
        print(
            "NOTE: ONSTART tasks run at Windows boot before any user logon.\n"
            "      This requires the task to be registered from an elevated\n"
            "      (Administrator) prompt."
        )

    if dry_run:
        print("[dry-run] Command printed above; not executed.")
        return 0

    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        print(f"SUCCESS: Task '{task_name}' registered.")
        if result.stdout.strip():
            print(result.stdout.strip())
    else:
        print(f"FAILED (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
    return result.returncode


def _cmd_uninstall(task_name: str, dry_run: bool) -> int:
    cmd = _build_uninstall_cmd(task_name)
    _print_cmd(cmd)

    if dry_run:
        print("[dry-run] Command printed above; not executed.")
        return 0

    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        print(f"SUCCESS: Task '{task_name}' removed.")
    else:
        # schtasks /Delete exits nonzero when the task does not exist.
        # That is not a crash — report it cleanly.
        combined = (result.stderr.strip() or result.stdout.strip()).lower()
        if "cannot find" in combined or "does not exist" in combined:
            print(f"INFO: Task '{task_name}' was not registered; nothing to remove.")
            return 0
        print(f"FAILED (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}")
    return result.returncode


def _cmd_status(task_name: str) -> int:
    cmd = _build_status_cmd(task_name)
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode == 0:
        print(result.stdout.strip())
    else:
        combined = (result.stderr.strip() or result.stdout.strip()).lower()
        if "cannot find" in combined or "does not exist" in combined:
            print(f"INFO: Task '{task_name}' is not registered.")
        else:
            print(f"schtasks exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    return result.returncode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_cmd(cmd: list[str]) -> None:
    printable = " ".join(
        f'"{part}"' if " " in part and not (part.startswith('"')) else part
        for part in cmd
    )
    print(f"Command: {printable}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts\\autostart.py",
        description=(
            "Manage a Windows Scheduled Task that runs `pmfi ingest` on logon.\n\n"
            "Absolute paths are used throughout so the task is not sensitive to\n"
            "a working directory when triggered by the scheduler."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--task-name",
        default=DEFAULT_TASK_NAME,
        help=f"Scheduled task name (default: '{DEFAULT_TASK_NAME}').",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # install
    p_install = sub.add_parser(
        "install",
        parents=[common],
        help="Register the scheduled task.",
        description=(
            "Register a Windows Scheduled Task that runs\n"
            "  pmfi ingest --log-file <abs-log-path>\n"
            "on the chosen trigger.  Idempotent: /F overwrites any existing\n"
            "task with the same name.\n\n"
            "ONLOGON (default): fires when any user logs on; no elevation needed.\n"
            "ONSTART: fires at boot before logon; requires an elevated prompt."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_install.add_argument(
        "--pmfi-exe",
        type=Path,
        default=DEFAULT_PMFI_EXE,
        help=f"Absolute path to pmfi.exe (default: {DEFAULT_PMFI_EXE}).",
    )
    p_install.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help=f"Absolute path for the rotating log file (default: {DEFAULT_LOG_FILE}).",
    )
    p_install.add_argument(
        "--trigger",
        choices=["onlogon", "onstart"],
        default="onlogon",
        help="When the task fires (default: onlogon).  onstart requires admin.",
    )
    p_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the schtasks command without executing it.",
    )

    # uninstall
    p_uninstall = sub.add_parser(
        "uninstall",
        parents=[common],
        help="Remove the scheduled task.",
    )
    p_uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the schtasks command without executing it.",
    )

    # status
    sub.add_parser(
        "status",
        parents=[common],
        help="Query whether the scheduled task is registered and its last-run state.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "install":
        return _cmd_install(
            task_name=args.task_name,
            pmfi_exe=args.pmfi_exe.resolve(),
            log_file=args.log_file.resolve(),
            trigger=args.trigger,
            dry_run=args.dry_run,
        )
    elif args.command == "uninstall":
        return _cmd_uninstall(task_name=args.task_name, dry_run=args.dry_run)
    elif args.command == "status":
        return _cmd_status(task_name=args.task_name)
    else:  # pragma: no cover
        raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
