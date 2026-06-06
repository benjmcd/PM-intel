"""Local CLI scaffold."""

from __future__ import annotations

import argparse
from pathlib import Path

from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_kalshi_fixture, normalize_polymarket_fixture
from pmfi.scoring import score_large_trade


ROOT = Path(__file__).resolve().parents[2]


def replay_fixtures() -> int:
    fixture_dir = ROOT / "tests" / "fixtures" / "raw"
    alerts = []
    for path in sorted(fixture_dir.glob("*.json")):
        raw = load_raw_event(path)
        if raw.venue_code == "polymarket":
            trade = normalize_polymarket_fixture(raw)
        elif raw.venue_code == "kalshi":
            trade = normalize_kalshi_fixture(raw)
        else:
            continue
        decision = score_large_trade(trade)
        if decision.emit_alert:
            alerts.append(decision)
            print(f"ALERT {decision.rule_id} {decision.severity} {decision.evidence}")
    print(f"fixture replay complete: {len(alerts)} alert(s)")
    return 0


def live_smoke() -> int:
    print("live-smoke is intentionally a stub until M5 implements opt-in live adapters")
    return 0


def review_pass() -> int:
    print(r"review-pass scaffold: run python scripts\verify.py and inspect governance checklist")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pmfi")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("replay-fixtures")
    sub.add_parser("live-smoke")
    sub.add_parser("review-pass")
    args = parser.parse_args(argv)
    if args.command == "replay-fixtures":
        return replay_fixtures()
    if args.command == "live-smoke":
        return live_smoke()
    if args.command == "review-pass":
        return review_pass()
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
