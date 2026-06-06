r"""Windows-native task router for local development.

This is the canonical command surface for agents and humans working from a
Windows local directory. It avoids Unix-only wrappers and automatic agent-side command triggers.
Use either:

    python scripts\task.py verify
    .\pmfi.cmd verify
    .\pmfi.ps1 verify
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env_with_src(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    src = str(ROOT / "src")
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not current else src + os.pathsep + current
    return env


def run(args: list[str], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(args)
    print(f"== {printable} ==", flush=True)
    completed = subprocess.run(args, cwd=ROOT, env=env, check=False, text=True)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def python_script(script: str, *args: str) -> None:
    run([sys.executable, script, *args])


def module(module_name: str, *args: str, env: dict[str, str] | None = None) -> None:
    run([sys.executable, "-m", module_name, *args], env=env_with_src(env))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmfi-task")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in [
        "verify",
        "status",
        "context-check",
        "clean",
        "db-up",
        "db-down",
        "db-init",
        "db-verify",
        "db-status",
        "fixture-replay",
        "live-smoke",
        "review-pass",
    ]:
        sub.add_parser(name)
    args = parser.parse_args(argv)

    if args.command == "verify":
        python_script("scripts/verify.py")
    elif args.command == "status":
        python_script("scripts/repo_status.py")
    elif args.command == "context-check":
        python_script("scripts/agent_context_check.py")
    elif args.command == "clean":
        python_script("scripts/clean.py")
    elif args.command == "db-up":
        python_script("scripts/db_local.py", "up")
    elif args.command == "db-down":
        python_script("scripts/db_local.py", "down")
    elif args.command == "db-init":
        python_script("scripts/db_local.py", "init")
    elif args.command == "db-verify":
        python_script("scripts/db_local.py", "verify")
    elif args.command == "db-status":
        python_script("scripts/db_local.py", "status")
    elif args.command == "fixture-replay":
        module("pmfi.cli", "replay-fixtures")
    elif args.command == "live-smoke":
        env = os.environ.copy()
        env.setdefault("PMFI_ENABLE_LIVE", "1")
        module("pmfi.cli", "live-smoke", env=env)
    elif args.command == "review-pass":
        module("pmfi.cli", "review-pass")
    else:  # pragma: no cover
        raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
