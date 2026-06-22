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
        "clean-checkout-smoke",
        "db-up",
        "db-down",
        "db-init",
        "db-verify",
        "db-status",
        "db-replay",
        "fixture-replay",
        "volume-spike-calibration",
        "volume-spike-calibration-sweep",
        "calibration-packet-batch",
        "calibration-decision",
        "calibration-review-queue",
        "calibration-cluster-review",
        "calibration-cluster-review-summary",
        "volume-spike-floor-audit",
        "outcome-audit",
        "lineage-check",
        "health",
        "report",
        "raw-events",
        "dead-letters",
        "data-coverage",
        "backtest-analytics",
        "capacity-measure",
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
            handoff_publish_group = handoff.add_mutually_exclusive_group()
            handoff_publish_group.add_argument("--publish-ready", action="store_true")
            handoff_publish_group.add_argument("--publish-ready-fetch", action="store_true")
            handoff.add_argument("--publish-timeout")
        elif name == "publish-ready":
            publish_ready = sub.add_parser(name)
            publish_ready.add_argument("--fetch", action="store_true")
        elif name == "clean-checkout-smoke":
            clean_checkout_smoke = sub.add_parser(name)
            clean_checkout_smoke.add_argument("--ref")
            clean_checkout_smoke.add_argument("--worktree-dir")
            clean_checkout_smoke.add_argument("--report-dir")
            clean_checkout_smoke.add_argument("--timeout")
            clean_checkout_smoke.add_argument("--install-dev", action="store_true")
            clean_checkout_smoke.add_argument("--run-verify", action="store_true")
            clean_checkout_smoke.add_argument("--db-verify", action="store_true")
            clean_checkout_smoke.add_argument("--keep-worktree", action="store_true")
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
            volume_spike_calibration.add_argument("--low-notional-min-baseline-trades")
            volume_spike_calibration.add_argument("--low-notional-min-baseline-median-usd")
            volume_spike_calibration.add_argument("--low-notional-max-spike-multiplier")
            volume_spike_calibration.add_argument("--low-notional-threshold-usd")
            volume_spike_calibration.add_argument("--history-max")
            volume_spike_calibration.add_argument("--cold-start", action="store_true")
            volume_spike_calibration.add_argument("--export-packet", action="store_true")
            volume_spike_calibration.add_argument("--packet-output")
            volume_spike_calibration.add_argument("--packet-limit")
            volume_spike_calibration.add_argument("--format", choices=["text", "json"])
        elif name == "calibration-packet-batch":
            calibration_packet_batch = sub.add_parser(name)
            calibration_packet_batch.add_argument("--window", action="append", required=True)
            calibration_packet_batch.add_argument("--limit")
            calibration_packet_batch.add_argument("--venue")
            calibration_packet_batch.add_argument("--market")
            calibration_packet_batch.add_argument("--min-spike-multiplier")
            calibration_packet_batch.add_argument("--min-trade-usd")
            calibration_packet_batch.add_argument("--min-baseline-trades")
            calibration_packet_batch.add_argument("--low-notional-min-baseline-trades")
            calibration_packet_batch.add_argument("--low-notional-min-baseline-median-usd")
            calibration_packet_batch.add_argument("--low-notional-max-spike-multiplier")
            calibration_packet_batch.add_argument("--low-notional-threshold-usd")
            calibration_packet_batch.add_argument("--history-max")
            calibration_packet_batch.add_argument("--cold-start", action="store_true")
            calibration_packet_batch.add_argument("--packet-output-prefix")
            calibration_packet_batch.add_argument("--packet-limit")
            calibration_packet_batch.add_argument("--format", choices=["text", "json"])
        elif name == "volume-spike-calibration-sweep":
            calibration_sweep = sub.add_parser(name)
            calibration_sweep.add_argument("--window", action="append", required=True)
            calibration_sweep.add_argument("--limit")
            calibration_sweep.add_argument("--venue")
            calibration_sweep.add_argument("--market")
            calibration_sweep.add_argument("--low-notional-min-baseline-trades", action="append")
            calibration_sweep.add_argument("--low-notional-threshold-usd", action="append")
            calibration_sweep.add_argument("--low-notional-min-baseline-median-usd", action="append")
            calibration_sweep.add_argument("--low-notional-max-spike-multiplier", action="append")
            calibration_sweep.add_argument("--cold-start", action="store_true")
            calibration_sweep.add_argument("--format", choices=["text", "json"])
        elif name == "calibration-decision":
            calibration_decision = sub.add_parser(name)
            calibration_decision.add_argument("--packet", action="append", default=[])
            calibration_decision.add_argument(
                "--decision",
                required=True,
                choices=["no-change", "needs-more-evidence", "change-ready"],
            )
            calibration_decision.add_argument("--rationale", required=True)
            calibration_decision.add_argument(
                "--include-review-summary",
                action="store_true",
            )
            calibration_decision.add_argument(
                "--include-cluster-review-summary",
                action="store_true",
            )
            calibration_decision.add_argument("--review", action="append", default=[])
            calibration_decision.add_argument("--output")
            calibration_decision.add_argument("--format", choices=["text", "json"])
        elif name == "calibration-review-queue":
            calibration_review_queue = sub.add_parser(name)
            calibration_review_queue.add_argument("--packet", action="append", default=[])
            calibration_review_queue.add_argument(
                "--state",
                choices=["removed", "added", "all"],
            )
            calibration_review_queue.add_argument(
                "--review-group",
                choices=[
                    "matched_noise",
                    "matched_fp",
                    "matched_tp",
                    "matched_unreviewed",
                    "matched_other",
                    "unmatched_replay_only",
                    "all",
                ],
            )
            calibration_review_queue.add_argument("--market-cluster")
            calibration_review_queue.add_argument("--limit")
            calibration_review_queue.add_argument("--format", choices=["text", "json"])
        elif name == "calibration-cluster-review":
            calibration_cluster_review = sub.add_parser(name)
            calibration_cluster_review.add_argument("--packet", action="append", default=[])
            calibration_cluster_review.add_argument("--market-cluster", required=True)
            calibration_cluster_review.add_argument(
                "--state",
                choices=["removed", "added", "all"],
            )
            calibration_cluster_review.add_argument(
                "--review-group",
                choices=[
                    "matched_noise",
                    "matched_fp",
                    "matched_tp",
                    "matched_unreviewed",
                    "matched_other",
                    "unmatched_replay_only",
                    "all",
                ],
            )
            calibration_cluster_review.add_argument(
                "--assessment",
                required=True,
                choices=["noise", "false-positive", "true-positive-risk", "uncertain"],
            )
            calibration_cluster_review.add_argument("--rationale", required=True)
            calibration_cluster_review.add_argument("--reviewed-by")
            calibration_cluster_review.add_argument("--output")
            calibration_cluster_review.add_argument("--include-raw-events", action="store_true")
            calibration_cluster_review.add_argument("--include-raw-payload", action="store_true")
            calibration_cluster_review.add_argument("--format", choices=["text", "json"])
        elif name == "calibration-cluster-review-summary":
            cluster_review_summary = sub.add_parser(name)
            cluster_review_summary.add_argument("--packet", action="append", default=[])
            cluster_review_summary.add_argument("--review", action="append", default=[])
            cluster_review_summary.add_argument(
                "--state",
                choices=["removed", "added", "all"],
            )
            cluster_review_summary.add_argument(
                "--review-group",
                choices=[
                    "matched_noise",
                    "matched_fp",
                    "matched_tp",
                    "matched_unreviewed",
                    "matched_other",
                    "unmatched_replay_only",
                    "all",
                ],
            )
            cluster_review_summary.add_argument("--market-cluster")
            cluster_review_summary.add_argument("--format", choices=["text", "json"])
        elif name == "volume-spike-floor-audit":
            volume_spike_floor_audit = sub.add_parser(name)
            volume_spike_floor_audit.add_argument("--from", dest="audit_from")
            volume_spike_floor_audit.add_argument("--to", dest="audit_to")
            volume_spike_floor_audit.add_argument("--limit")
            volume_spike_floor_audit.add_argument("--venue")
            volume_spike_floor_audit.add_argument("--market")
            volume_spike_floor_audit.add_argument("--cold-start", action="store_true")
            volume_spike_floor_audit.add_argument("--format", choices=["text", "json"])
        elif name == "health":
            health = sub.add_parser(name)
            health.add_argument("--max-age-seconds")
            health.add_argument("--json", action="store_true")
            health.add_argument("--heartbeat-path")
            health.add_argument("--venue-stale-seconds")
        elif name == "lineage-check":
            lineage_check = sub.add_parser(name)
            lineage_check.add_argument("--since")
            lineage_check.add_argument("--limit")
            lineage_check.add_argument("--format", choices=["table", "json"])
            lineage_check.add_argument("--strict", action="store_true")
        elif name == "report":
            report = sub.add_parser(name)
            report.add_argument("--since")
            report.add_argument("--format", choices=["table", "json"])
        elif name == "raw-events":
            raw_events = sub.add_parser(name)
            raw_events.add_argument("--id", action="append", default=[])
            raw_events.add_argument("--include-payload", action="store_true")
            raw_events.add_argument("--format", choices=["text", "json"])
        elif name == "dead-letters":
            dead_letters = sub.add_parser(name)
            dead_letters.add_argument("--limit")
            dead_letters.add_argument("--format", choices=["table", "json"])
            dead_letters_sub = dead_letters.add_subparsers(dest="dead_letters_cmd", required=False)
            dead_letters_resolve = dead_letters_sub.add_parser("resolve")
            dead_letters_resolve.add_argument("dead_letter_id_or_prefix")
            dead_letters_resolve.add_argument("--dry-run", action="store_true")
        elif name == "data-coverage":
            data_coverage = sub.add_parser(name)
            data_coverage.add_argument("--since")
            data_coverage.add_argument("--until")
            data_coverage.add_argument("--venue", choices=["polymarket", "kalshi"])
            data_coverage.add_argument("--include-synthetic", action="store_true")
            data_coverage.add_argument("--format", choices=["text", "json"])
        elif name == "backtest-analytics":
            backtest_analytics = sub.add_parser(name)
            backtest_analytics.add_argument("--from", dest="backtest_from")
            backtest_analytics.add_argument("--to", dest="backtest_to")
            backtest_analytics.add_argument("--limit", type=int)
            backtest_analytics.add_argument("--venue", dest="backtest_venue")
            backtest_analytics.add_argument("--market", dest="backtest_market")
            backtest_analytics.add_argument("--volume-spike-min-trade-usd", type=float, action="append")
            backtest_analytics.add_argument("--cold-start", action="store_true")
            backtest_analytics.add_argument("--format", choices=["text", "json"])
        elif name == "capacity-measure":
            capacity_measure = sub.add_parser(name)
            capacity_measure.add_argument("--manifest")
            capacity_measure.add_argument("--format", choices=["json", "text"])
        elif name == "review-packet":
            review_packet = sub.add_parser(name)
            review_packet.add_argument("--since")
            review_packet.add_argument("--rule")
            review_packet.add_argument("--review-state", choices=["reviewed", "unreviewed"])
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
    elif args.command == "clean-checkout-smoke":
        clean_smoke_args = []
        for name in ["ref", "worktree_dir", "report_dir", "timeout"]:
            value = getattr(args, name)
            if value is not None:
                clean_smoke_args.extend([f"--{name.replace('_', '-')}", value])
        for name in ["install_dev", "run_verify", "db_verify", "keep_worktree"]:
            if getattr(args, name):
                clean_smoke_args.append(f"--{name.replace('_', '-')}")
        python_script("scripts/clean_checkout_smoke.py", *clean_smoke_args)
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
            "low_notional_min_baseline_trades",
            "low_notional_min_baseline_median_usd",
            "low_notional_max_spike_multiplier",
            "low_notional_threshold_usd",
            "history_max",
            "packet_output",
            "packet_limit",
            "format",
        ]:
            value = getattr(args, name)
            if value is not None:
                flag = f"--{name.removeprefix('calibration_').replace('_', '-')}"
                calibration_args.extend([flag, value])
        if getattr(args, "cold_start"):
            calibration_args.append("--cold-start")
        if getattr(args, "export_packet"):
            calibration_args.append("--export-packet")
        module("pmfi.cli", "volume-spike-calibration", *calibration_args)
    elif args.command == "calibration-packet-batch":
        batch_args = []
        for window in getattr(args, "window", None) or []:
            batch_args.extend(["--window", window])
        for name in [
            "limit",
            "venue",
            "market",
            "min_spike_multiplier",
            "min_trade_usd",
            "min_baseline_trades",
            "low_notional_min_baseline_trades",
            "low_notional_min_baseline_median_usd",
            "low_notional_max_spike_multiplier",
            "low_notional_threshold_usd",
            "history_max",
            "packet_output_prefix",
            "packet_limit",
            "format",
        ]:
            value = getattr(args, name)
            if value is not None:
                batch_args.extend([f"--{name.replace('_', '-')}", value])
        if getattr(args, "cold_start"):
            batch_args.append("--cold-start")
        module("pmfi.cli", "calibration-packet-batch", *batch_args)
    elif args.command == "volume-spike-calibration-sweep":
        sweep_args = []
        for window in getattr(args, "window", None) or []:
            sweep_args.extend(["--window", window])
        for name in [
            "limit",
            "venue",
            "market",
        ]:
            value = getattr(args, name)
            if value is not None:
                sweep_args.extend([f"--{name.replace('_', '-')}", value])
        for value in getattr(args, "low_notional_min_baseline_trades", None) or []:
            sweep_args.extend(["--low-notional-min-baseline-trades", value])
        for value in getattr(args, "low_notional_threshold_usd", None) or []:
            sweep_args.extend(["--low-notional-threshold-usd", value])
        for value in getattr(args, "low_notional_min_baseline_median_usd", None) or []:
            sweep_args.extend(["--low-notional-min-baseline-median-usd", value])
        for value in getattr(args, "low_notional_max_spike_multiplier", None) or []:
            sweep_args.extend(["--low-notional-max-spike-multiplier", value])
        if getattr(args, "format") is not None:
            sweep_args.extend(["--format", getattr(args, "format")])
        if getattr(args, "cold_start"):
            sweep_args.append("--cold-start")
        module("pmfi.cli", "volume-spike-calibration-sweep", *sweep_args)
    elif args.command == "calibration-decision":
        decision_args = []
        for packet in getattr(args, "packet", None) or []:
            decision_args.extend(["--packet", packet])
        for review in getattr(args, "review", None) or []:
            decision_args.extend(["--review", review])
        for name in ["decision", "rationale", "output", "format"]:
            value = getattr(args, name)
            if value is not None:
                decision_args.extend([f"--{name.replace('_', '-')}", value])
        if getattr(args, "include_review_summary"):
            decision_args.append("--include-review-summary")
        if getattr(args, "include_cluster_review_summary"):
            decision_args.append("--include-cluster-review-summary")
        module("pmfi.cli", "calibration-decision", *decision_args)
    elif args.command == "calibration-review-queue":
        queue_args = []
        for packet in getattr(args, "packet", None) or []:
            queue_args.extend(["--packet", packet])
        for name in ["state", "review_group", "market_cluster", "limit", "format"]:
            value = getattr(args, name)
            if value is not None:
                queue_args.extend([f"--{name.replace('_', '-')}", value])
        module("pmfi.cli", "calibration-review-queue", *queue_args)
    elif args.command == "calibration-cluster-review":
        review_args = []
        for packet in getattr(args, "packet", None) or []:
            review_args.extend(["--packet", packet])
        for name in [
            "market_cluster",
            "state",
            "review_group",
            "assessment",
            "rationale",
            "reviewed_by",
            "output",
            "format",
        ]:
            value = getattr(args, name)
            if value is not None:
                review_args.extend([f"--{name.replace('_', '-')}", value])
        if getattr(args, "include_raw_events"):
            review_args.append("--include-raw-events")
        if getattr(args, "include_raw_payload"):
            review_args.append("--include-raw-payload")
        module("pmfi.cli", "calibration-cluster-review", *review_args)
    elif args.command == "calibration-cluster-review-summary":
        summary_args = []
        for packet in getattr(args, "packet", None) or []:
            summary_args.extend(["--packet", packet])
        for review in getattr(args, "review", None) or []:
            summary_args.extend(["--review", review])
        for name in ["state", "review_group", "market_cluster", "format"]:
            value = getattr(args, name)
            if value is not None:
                summary_args.extend([f"--{name.replace('_', '-')}", value])
        module("pmfi.cli", "calibration-cluster-review-summary", *summary_args)
    elif args.command == "volume-spike-floor-audit":
        audit_args = []
        for name in [
            "audit_from",
            "audit_to",
            "limit",
            "venue",
            "market",
            "format",
        ]:
            value = getattr(args, name)
            if value is not None:
                flag = f"--{name.removeprefix('audit_').replace('_', '-')}"
                audit_args.extend([flag, value])
        if getattr(args, "cold_start"):
            audit_args.append("--cold-start")
        module("pmfi.cli", "volume-spike-floor-audit", *audit_args)
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
    elif args.command == "lineage-check":
        lineage_args = []
        for name in ["since", "limit", "format"]:
            value = getattr(args, name)
            if value is not None:
                lineage_args.extend([f"--{name.replace('_', '-')}", value])
        if args.strict:
            lineage_args.append("--strict")
        module("pmfi.cli", "alerts", "lineage-check", *lineage_args)
    elif args.command == "report":
        report_args = []
        if args.since is not None:
            report_args.extend(["--since", args.since])
        if args.format is not None:
            report_args.extend(["--format", args.format])
        module("pmfi.cli", "report", *report_args)
    elif args.command == "raw-events":
        raw_event_args = []
        for raw_event_id in getattr(args, "id", None) or []:
            raw_event_args.extend(["--id", raw_event_id])
        if args.include_payload:
            raw_event_args.append("--include-payload")
        if args.format is not None:
            raw_event_args.extend(["--format", args.format])
        module("pmfi.cli", "raw-events", *raw_event_args)
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
    elif args.command == "data-coverage":
        data_coverage_args = []
        for name in ["since", "until", "venue", "format"]:
            value = getattr(args, name)
            if value is not None:
                data_coverage_args.extend([f"--{name.replace('_', '-')}", value])
        if getattr(args, "include_synthetic"):
            data_coverage_args.append("--include-synthetic")
        module("pmfi.cli", "data-coverage", *data_coverage_args)
    elif args.command == "backtest-analytics":
        backtest_args = []
        for name in ["backtest_from", "backtest_to", "limit", "backtest_venue", "backtest_market", "format"]:
            value = getattr(args, name)
            if value is not None:
                flag = f"--{name.removeprefix('backtest_').replace('_', '-')}"
                backtest_args.extend([flag, str(value)])
        for min_trade_usd in getattr(args, "volume_spike_min_trade_usd", None) or []:
            backtest_args.extend(["--volume-spike-min-trade-usd", str(min_trade_usd)])
        if getattr(args, "cold_start"):
            backtest_args.append("--cold-start")
        module("pmfi.cli", "backtest-analytics", *backtest_args)
    elif args.command == "capacity-measure":
        capacity_args = []
        for name in ["manifest", "format"]:
            value = getattr(args, name)
            if value is not None:
                capacity_args.extend([f"--{name.replace('_', '-')}", value])
        module("pmfi.cli", "capacity-measure", *capacity_args)
    elif args.command == "review-packet":
        review_packet_args = []
        for name in ["since", "rule", "review_state", "review_label", "category", "limit", "output", "format"]:
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
        for name in ["output_dir", "db_timeout", "verify_timeout", "publish_timeout"]:
            value = getattr(args, name)
            if value is not None:
                handoff_args.extend([f"--{name.replace('_', '-')}", value])
        if args.db_verify:
            handoff_args.append("--db-verify")
        if args.no_db_verify:
            handoff_args.append("--no-db-verify")
        if args.run_verify:
            handoff_args.append("--run-verify")
        if args.publish_ready:
            handoff_args.append("--publish-ready")
        if args.publish_ready_fetch:
            handoff_args.append("--publish-ready-fetch")
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
