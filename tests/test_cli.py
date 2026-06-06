import argparse
from pmfi.cli import main


def test_fixture_replay_runs(capsys):
    rc = main(["replay"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "replay" in captured.out.lower() or "fixture" in captured.out.lower()


def test_review_pass_prints_windows_path_without_control_chars(capsys):
    rc = main(["review-pass"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "python scripts\\verify.py" in captured.out
    assert "\x0b" not in captured.out


def test_review_pass_prints_windows_command(capsys):
    rc = main(["review-pass"])
    captured = capsys.readouterr()
    assert rc == 0
    assert r"python scripts\verify.py" in captured.out
    assert "\x0b" not in captured.out


# --- argparser contract tests for filter flags ---

def _make_parser():
    from pmfi.cli import main as _main
    import sys
    # Build the parser by importing the build function or calling main with --help
    # Simpler: use argparse directly via the parser built in main()
    # We test arg parsing by constructing a Namespace the same way argparse would.
    from argparse import ArgumentParser, Namespace
    return None  # parser is internal; test via the parsed Namespace shape instead


def test_alerts_list_accepts_filter_flags():
    """alerts list argparser must accept --rule, --venue, --severity, --since."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["alerts", "list", "--rule", "large_trade_absolute_v1",
                            "--venue", "polymarket", "--severity", "high", "--since", "24h"])
    assert ns.rule == "large_trade_absolute_v1"
    assert ns.venue == "polymarket"
    assert ns.severity == "high"
    assert ns.since == "24h"


def test_alerts_list_accepts_format_json():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["alerts", "list", "--format", "json"])
    assert args.format == "json"


def test_alerts_list_accepts_filters():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["alerts", "list", "--venue", "polymarket", "--severity", "high", "--market", "BTC", "--since", "24h"])
    assert args.venue == "polymarket"
    assert args.severity == "high"
    assert args.market == "BTC"
    assert args.since == "24h"


def test_watch_accepts_filter_flags():
    """watch argparser must accept --rule, --venue, --severity."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    ns = parser.parse_args(["watch", "--rule", "open_interest_shock_v1",
                            "--venue", "kalshi", "--severity", "medium"])
    assert ns.rule == "open_interest_shock_v1"
    assert ns.venue == "kalshi"
    assert ns.severity == "medium"


def test_status_runs_without_db(capsys):
    """pmfi status must exit 0 even when DB is unreachable."""
    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    # Either rich panel or plain text output; DB error is expected without running DB.
    assert len(out) > 0  # something was printed


def test_baselines_compute_cli_args():
    """baselines compute CLI args parse correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["baselines", "compute", "--days", "14", "--min-samples", "5", "--save"])
    assert args.days == 14
    assert args.min_samples == 5
    assert args.save is True


def test_baselines_show_cli_args():
    """baselines show CLI args parse correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["baselines", "show"])
    assert args.baselines_cmd == "show"


def test_report_cli_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["report", "--since", "7d", "--format", "json"])
    assert args.since == "7d"
    assert args.format == "json"


def test_report_cli_default_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["report"])
    assert args.since == "24h"
    assert args.format == "table"
