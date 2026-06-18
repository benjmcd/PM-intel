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


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


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
        "soak",
        "handoff",
        "publish-ready",
        "live-smoke",
        "review-pass",
    ]:
        if name == "handoff":
            handoff = sub.add_parser(name)
            handoff.add_argument("--output-dir")
            handoff.add_argument("--db-verify", action="store_true")
            handoff.add_argument("--no-db-verify", action="store_true")
            handoff.add_argument("--run-verify", action="store_true")
            handoff.add_argument("--db-timeout")
            handoff.add_argument("--verify-timeout")
        elif name == "publish-ready":
            publish_ready = sub.add_parser(name)
            publish_ready.add_argument("--fetch", action="store_true")
        elif name == "soak":
            soak = sub.add_parser(name)
            soak_window = soak.add_mutually_exclusive_group()
            soak_window.add_argument("--since", default=None, help="Explicit timezone-aware ISO timestamp start for the window")
            soak_window.add_argument("--window", default="2h")
            soak.add_argument("--until", default=None, help="Explicit timezone-aware ISO timestamp end for the window")
            soak.add_argument("--min-duration-minutes", type=int, default=60)
            soak.add_argument("--min-required-venue-duration-minutes", type=_non_negative_int, default=None)
            soak.add_argument("--min-raw-events", type=int, default=1)
            soak.add_argument("--min-trades", type=int, default=1)
            soak.add_argument("--required-venue", action="append", default=[])
            soak.add_argument("--max-dead-letters", type=int, default=0)
            soak.add_argument("--max-incidents", type=int, default=0)
            soak.add_argument("--format", choices=["text", "json"], default="text")
        else:
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
    elif args.command == "soak":
        soak_args = []
        if args.since is not None:
            soak_args.extend(["--since", args.since])
        else:
            soak_args.extend(["--window", args.window])
        if args.until is not None:
            soak_args.extend(["--until", args.until])
        soak_args.extend([
            "--min-duration-minutes", str(args.min_duration_minutes),
        ])
        if args.min_required_venue_duration_minutes is not None:
            soak_args.extend([
                "--min-required-venue-duration-minutes",
                str(args.min_required_venue_duration_minutes),
            ])
        soak_args.extend([
            "--min-raw-events", str(args.min_raw_events),
            "--min-trades", str(args.min_trades),
            "--max-dead-letters", str(args.max_dead_letters),
            "--max-incidents", str(args.max_incidents),
            "--format", args.format,
        ])
        for venue in args.required_venue:
            soak_args.extend(["--required-venue", venue])
        module("pmfi.cli", "soak", *soak_args)
    elif args.command == "handoff":
        handoff_args = []
        for name in ["output_dir", "db_timeout", "verify_timeout"]:
            value = getattr(args, name)
            if value is not None:
                handoff_args.extend([f"--{name.replace('_', '-')}", value])
        if args.db_verify:
            handoff_args.append("--db-verify")
        if args.no_db_verify:
            handoff_args.append("--no-db-verify")
        if args.run_verify:
            handoff_args.append("--run-verify")
        python_script("scripts/handoff.py", *handoff_args)
    elif args.command == "publish-ready":
        publish_ready_args = []
        if args.fetch:
            publish_ready_args.append("--fetch")
        python_script("scripts/publish_ready.py", *publish_ready_args)
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
