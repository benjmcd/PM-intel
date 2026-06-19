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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "outcome-audit":
        module("pmfi.cli", "alerts", "outcome-audit", *raw_argv[1:])
        return 0

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
        "db-replay",
        "fixture-replay",
        "volume-spike-calibration",
        "outcome-audit",
        "health",
        "report",
        "dead-letters",
        "review-packet",
        "refresh-watchlist",
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
        elif name == "db-replay":
            db_replay = sub.add_parser(name)
            db_replay.add_argument("--from", dest="replay_from")
            db_replay.add_argument("--to", dest="replay_to")
            db_replay.add_argument("--limit")
            db_replay.add_argument("--venue")
            db_replay.add_argument("--market")
            db_replay.add_argument("--persist", action="store_true")
            db_replay.add_argument("--report", action="store_true")
            db_replay.add_argument("--verbose", action="store_true")
        elif name == "volume-spike-calibration":
            volume_spike_calibration = sub.add_parser(name)
            volume_spike_calibration.add_argument("--from", dest="calibration_from")
            volume_spike_calibration.add_argument("--to", dest="calibration_to")
            volume_spike_calibration.add_argument("--limit")
            volume_spike_calibration.add_argument("--venue")
            volume_spike_calibration.add_argument("--market")
            volume_spike_calibration.add_argument("--min-spike-multiplier")
            volume_spike_calibration.add_argument("--min-trade-usd")
            volume_spike_calibration.add_argument("--min-baseline-trades")
            volume_spike_calibration.add_argument("--history-max")
            volume_spike_calibration.add_argument("--cold-start", action="store_true")
            volume_spike_calibration.add_argument("--format", choices=["text", "json"])
        elif name == "health":
            health = sub.add_parser(name)
            health.add_argument("--max-age-seconds")
            health.add_argument("--json", action="store_true")
            health.add_argument("--heartbeat-path")
            health.add_argument("--venue-stale-seconds")
        elif name == "report":
            report = sub.add_parser(name)
            report.add_argument("--since")
            report.add_argument("--format", choices=["table", "json"])
        elif name == "dead-letters":
            dead_letters = sub.add_parser(name)
            dead_letters.add_argument("--limit")
            dead_letters.add_argument("--format", choices=["table", "json"])
            dead_letters_sub = dead_letters.add_subparsers(dest="dead_letters_cmd", required=False)
            dead_letters_resolve = dead_letters_sub.add_parser("resolve")
            dead_letters_resolve.add_argument("dead_letter_id_or_prefix")
            dead_letters_resolve.add_argument("--dry-run", action="store_true")
        elif name == "review-packet":
            review_packet = sub.add_parser(name)
            review_packet.add_argument("--since")
            review_packet.add_argument("--rule")
            review_packet.add_argument("--review-label", choices=["tp", "fp", "noise"])
            review_packet.add_argument("--category")
            review_packet.add_argument("--limit")
            review_packet.add_argument("--output")
            review_packet.add_argument("--format", choices=["json"])
        elif name == "refresh-watchlist":
            refresh_watchlist = sub.add_parser(name)
            refresh_watchlist.add_argument("--limit")
            refresh_watchlist.add_argument("--since-minutes")
            refresh_watchlist.add_argument("--top")
            refresh_watchlist.add_argument("--format", choices=["table", "json"])
            refresh_watchlist.add_argument("--force", action="store_true")
            refresh_watchlist.add_argument("--sync", action="store_true")
            refresh_watchlist.add_argument("--watch", action="store_true")
            refresh_watchlist.add_argument("--replace-watch", action="store_true")
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
    args = parser.parse_args(raw_argv)

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
    elif args.command == "db-replay":
        db_replay_args = ["--from-db"]
        for name in ["replay_from", "replay_to", "limit", "venue", "market"]:
            value = getattr(args, name)
            if value is not None:
                db_replay_args.extend([f"--{name.removeprefix('replay_')}", value])
        for name in ["persist", "report", "verbose"]:
            if getattr(args, name):
                db_replay_args.append(f"--{name}")
        module("pmfi.cli", "replay", *db_replay_args)
    elif args.command == "fixture-replay":
        module("pmfi.cli", "replay-fixtures")
    elif args.command == "volume-spike-calibration":
        calibration_args = []
        for name in [
            "calibration_from",
            "calibration_to",
            "limit",
            "venue",
            "market",
            "min_spike_multiplier",
            "min_trade_usd",
            "min_baseline_trades",
            "history_max",
            "format",
        ]:
            value = getattr(args, name)
            if value is not None:
                flag = f"--{name.removeprefix('calibration_').replace('_', '-')}"
                calibration_args.extend([flag, value])
        if getattr(args, "cold_start"):
            calibration_args.append("--cold-start")
        module("pmfi.cli", "volume-spike-calibration", *calibration_args)
    elif args.command == "health":
        health_args = []
        if args.max_age_seconds is not None:
            health_args.extend(["--max-age-seconds", args.max_age_seconds])
        if args.json:
            health_args.append("--json")
        if args.heartbeat_path is not None:
            health_args.extend(["--heartbeat-path", args.heartbeat_path])
        if args.venue_stale_seconds is not None:
            health_args.extend(["--venue-stale-seconds", args.venue_stale_seconds])
        module("pmfi.cli", "health", *health_args)
    elif args.command == "report":
        report_args = []
        if args.since is not None:
            report_args.extend(["--since", args.since])
        if args.format is not None:
            report_args.extend(["--format", args.format])
        module("pmfi.cli", "report", *report_args)
    elif args.command == "dead-letters":
        dead_letters_args = []
        if args.limit is not None:
            dead_letters_args.extend(["--limit", args.limit])
        if args.format is not None:
            dead_letters_args.extend(["--format", args.format])
        if args.dead_letters_cmd == "resolve":
            dead_letters_args.extend(["resolve", args.dead_letter_id_or_prefix])
            if args.dry_run:
                dead_letters_args.append("--dry-run")
        module("pmfi.cli", "dead-letters", *dead_letters_args)
    elif args.command == "review-packet":
        review_packet_args = []
        for name in ["since", "rule", "review_label", "category", "limit", "output", "format"]:
            value = getattr(args, name)
            if value is not None:
                review_packet_args.extend([f"--{name.replace('_', '-')}", value])
        module("pmfi.cli", "alerts", "review-packet", *review_packet_args)
    elif args.command == "refresh-watchlist":
        refresh_watchlist_args = []
        for name in ["limit", "since_minutes", "top", "format"]:
            value = getattr(args, name)
            if value is not None:
                refresh_watchlist_args.extend([f"--{name.replace('_', '-')}", value])
        for name in ["force", "sync", "watch"]:
            if getattr(args, name):
                refresh_watchlist_args.append(f"--{name}")
        if getattr(args, "replace_watch"):
            refresh_watchlist_args.append("--replace-watch")
        module("pmfi.cli", "markets", "refresh-watchlist", *refresh_watchlist_args)
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
