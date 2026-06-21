from __future__ import annotations


def test_task_health_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "health",
        "--max-age-seconds",
        "300",
        "--json",
        "--heartbeat-path",
        "reports\\health\\heartbeat.json",
        "--venue-stale-seconds",
        "900",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "health",
        "--max-age-seconds",
        "300",
        "--json",
        "--heartbeat-path",
        "reports\\health\\heartbeat.json",
        "--venue-stale-seconds",
        "900",
    )]


def test_task_report_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["report", "--since", "7d", "--format", "json"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "report",
        "--since",
        "7d",
        "--format",
        "json",
    )]


def test_task_raw_events_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "raw-events",
        "--id",
        "200053",
        "--id",
        "204986",
        "--include-payload",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "raw-events",
        "--id",
        "200053",
        "--id",
        "204986",
        "--include-payload",
        "--format",
        "json",
    )]


def test_task_review_packet_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "review-packet",
        "--since",
        "24h",
        "--rule",
        "volume_spike_v1",
        "--review-state",
        "reviewed",
        "--review-label",
        "noise",
        "--category",
        "low_notional_thin_baseline",
        "--limit",
        "10",
        "--output",
        "reports\\review-packets\\noise.json",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "alerts",
        "review-packet",
        "--since",
        "24h",
        "--rule",
        "volume_spike_v1",
        "--review-state",
        "reviewed",
        "--review-label",
        "noise",
        "--category",
        "low_notional_thin_baseline",
        "--limit",
        "10",
        "--output",
        "reports\\review-packets\\noise.json",
        "--format",
        "json",
    )]


def test_task_lineage_check_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "lineage-check",
        "--since",
        "2026-06-20T12:00:00+00:00",
        "--limit",
        "25",
        "--format",
        "json",
        "--strict",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "alerts",
        "lineage-check",
        "--since",
        "2026-06-20T12:00:00+00:00",
        "--limit",
        "25",
        "--format",
        "json",
        "--strict",
    )]


def test_task_dead_letters_forwards_default_cli_command(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["dead-letters"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "dead-letters",
    )]


def test_task_dead_letters_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["dead-letters", "--limit", "3", "--format", "json"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "dead-letters",
        "--limit",
        "3",
        "--format",
        "json",
    )]


def test_task_dead_letters_resolve_forwards_supported_cli_args(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["dead-letters", "resolve", "abcdef12", "--dry-run"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "dead-letters",
        "resolve",
        "abcdef12",
        "--dry-run",
    )]


def test_task_data_coverage_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "data-coverage",
        "--since",
        "24h",
        "--until",
        "2026-06-20T12:00:00+00:00",
        "--venue",
        "polymarket",
        "--include-synthetic",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "data-coverage",
        "--since",
        "24h",
        "--until",
        "2026-06-20T12:00:00+00:00",
        "--venue",
        "polymarket",
        "--format",
        "json",
        "--include-synthetic",
    )]


def test_task_backtest_analytics_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "backtest-analytics",
        "--from",
        "24h",
        "--to",
        "1h",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD",
        "--volume-spike-min-trade-usd",
        "850",
        "--volume-spike-min-trade-usd",
        "1000",
        "--cold-start",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "backtest-analytics",
        "--from",
        "24h",
        "--to",
        "1h",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD",
        "--format",
        "json",
        "--volume-spike-min-trade-usd",
        "850.0",
        "--volume-spike-min-trade-usd",
        "1000.0",
        "--cold-start",
    )]


def test_task_db_replay_defaults_to_from_db_only(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["db-replay"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "replay",
        "--from-db",
    )]


def test_task_db_replay_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "db-replay",
        "--from",
        "2026-06-18T17:08:08Z",
        "--to",
        "2026-06-18T17:38:11Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--persist",
        "--report",
        "--verbose",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "replay",
        "--from-db",
        "--from",
        "2026-06-18T17:08:08Z",
        "--to",
        "2026-06-18T17:38:11Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--persist",
        "--report",
        "--verbose",
    )]


def test_task_volume_spike_calibration_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "volume-spike-calibration",
        "--from",
        "24h",
        "--to",
        "1h",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--min-spike-multiplier",
        "6.5",
        "--min-trade-usd",
        "750",
        "--min-baseline-trades",
        "25",
        "--low-notional-min-baseline-trades",
        "30",
        "--low-notional-min-baseline-median-usd",
        "150",
        "--low-notional-max-spike-multiplier",
        "24",
        "--low-notional-threshold-usd",
        "5000",
        "--history-max",
        "300",
        "--export-packet",
        "--packet-output",
        "reports\\calibration-packets\\candidate.json",
        "--packet-limit",
        "50",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "volume-spike-calibration",
        "--from",
        "24h",
        "--to",
        "1h",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--min-spike-multiplier",
        "6.5",
        "--min-trade-usd",
        "750",
        "--min-baseline-trades",
        "25",
        "--low-notional-min-baseline-trades",
        "30",
        "--low-notional-min-baseline-median-usd",
        "150",
        "--low-notional-max-spike-multiplier",
        "24",
        "--low-notional-threshold-usd",
        "5000",
        "--history-max",
        "300",
        "--packet-output",
        "reports\\calibration-packets\\candidate.json",
        "--packet-limit",
        "50",
        "--format",
        "json",
        "--export-packet",
    )]


def test_task_calibration_packet_batch_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "calibration-packet-batch",
        "--window",
        "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
        "--window",
        "beta:2026-06-18T14:00:00Z:2026-06-18T15:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--low-notional-min-baseline-trades",
        "50",
        "--low-notional-min-baseline-median-usd",
        "150",
        "--low-notional-max-spike-multiplier",
        "24",
        "--cold-start",
        "--packet-output-prefix",
        "independent",
        "--packet-limit",
        "25",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "calibration-packet-batch",
        "--window",
        "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
        "--window",
        "beta:2026-06-18T14:00:00Z:2026-06-18T15:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--low-notional-min-baseline-trades",
        "50",
        "--low-notional-min-baseline-median-usd",
        "150",
        "--low-notional-max-spike-multiplier",
        "24",
        "--packet-output-prefix",
        "independent",
        "--packet-limit",
        "25",
        "--format",
        "json",
        "--cold-start",
    )]


def test_task_volume_spike_calibration_sweep_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "volume-spike-calibration-sweep",
        "--window",
        "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
        "--window",
        "beta:2026-06-18T14:00:00Z:2026-06-18T15:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--low-notional-min-baseline-trades",
        "30",
        "--low-notional-min-baseline-trades",
        "50",
        "--low-notional-threshold-usd",
        "5000",
        "--low-notional-threshold-usd",
        "7500",
        "--low-notional-min-baseline-median-usd",
        "100",
        "--low-notional-min-baseline-median-usd",
        "250",
        "--low-notional-max-spike-multiplier",
        "12",
        "--low-notional-max-spike-multiplier",
        "24",
        "--cold-start",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "volume-spike-calibration-sweep",
        "--window",
        "alpha:2026-06-18T12:00:00Z:2026-06-18T13:00:00Z",
        "--window",
        "beta:2026-06-18T14:00:00Z:2026-06-18T15:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--low-notional-min-baseline-trades",
        "30",
        "--low-notional-min-baseline-trades",
        "50",
        "--low-notional-threshold-usd",
        "5000",
        "--low-notional-threshold-usd",
        "7500",
        "--low-notional-min-baseline-median-usd",
        "100",
        "--low-notional-min-baseline-median-usd",
        "250",
        "--low-notional-max-spike-multiplier",
        "12",
        "--low-notional-max-spike-multiplier",
        "24",
        "--format",
        "json",
        "--cold-start",
    )]


def test_task_calibration_decision_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "calibration-decision",
        "--packet",
        "first.json",
        "--packet",
        "second.json",
        "--decision",
        "no-change",
        "--rationale",
        "comparison removes only unmatched replay emissions",
        "--include-review-summary",
        "--include-cluster-review-summary",
        "--review",
        "tie.json",
        "--output",
        "reports\\calibration-decisions\\decision.json",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "calibration-decision",
        "--packet",
        "first.json",
        "--packet",
        "second.json",
        "--review",
        "tie.json",
        "--decision",
        "no-change",
        "--rationale",
        "comparison removes only unmatched replay emissions",
        "--output",
        "reports\\calibration-decisions\\decision.json",
        "--format",
        "json",
        "--include-review-summary",
        "--include-cluster-review-summary",
    )]


def test_task_calibration_review_queue_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(task, "module", fake_module)
    rc = task.main([
        "calibration-review-queue",
        "--packet",
        "m20-no.json",
        "--packet",
        "m20-p800.json",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--market-cluster",
        "KXBTCD",
        "--limit",
        "25",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "calibration-review-queue",
        "--packet",
        "m20-no.json",
        "--packet",
        "m20-p800.json",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--market-cluster",
        "KXBTCD",
        "--limit",
        "25",
        "--format",
        "json",
    )]


def test_task_calibration_cluster_review_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(task, "module", fake_module)
    rc = task.main([
        "calibration-cluster-review",
        "--packet",
        "m20-no.json",
        "--packet",
        "m20-p800.json",
        "--market-cluster",
        "KXBTCD",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--assessment",
        "uncertain",
        "--rationale",
        "needs packet/raw-event inspection",
        "--reviewed-by",
        "operator",
        "--output",
        "reports\\calibration-cluster-reviews\\cluster.json",
        "--include-raw-events",
        "--include-raw-payload",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "calibration-cluster-review",
        "--packet",
        "m20-no.json",
        "--packet",
        "m20-p800.json",
        "--market-cluster",
        "KXBTCD",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--assessment",
        "uncertain",
        "--rationale",
        "needs packet/raw-event inspection",
        "--reviewed-by",
        "operator",
        "--output",
        "reports\\calibration-cluster-reviews\\cluster.json",
        "--format",
        "json",
        "--include-raw-events",
        "--include-raw-payload",
    )]


def test_task_calibration_cluster_review_summary_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*argv):
        calls.append(argv)
        return 0

    monkeypatch.setattr(task, "module", fake_module)
    rc = task.main([
        "calibration-cluster-review-summary",
        "--packet",
        "m20-no.json",
        "--review",
        "cluster.json",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--market-cluster",
        "KXBTCD",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "calibration-cluster-review-summary",
        "--packet",
        "m20-no.json",
        "--review",
        "cluster.json",
        "--state",
        "removed",
        "--review-group",
        "unmatched_replay_only",
        "--market-cluster",
        "KXBTCD",
        "--format",
        "json",
    )]


def test_task_volume_spike_floor_audit_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "volume-spike-floor-audit",
        "--from",
        "24h",
        "--to",
        "2026-06-18T17:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--cold-start",
        "--format",
        "json",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "volume-spike-floor-audit",
        "--from",
        "24h",
        "--to",
        "2026-06-18T17:00:00Z",
        "--limit",
        "0",
        "--venue",
        "kalshi",
        "--market",
        "KXBTCD-26JUN1817-T63749.99",
        "--format",
        "json",
        "--cold-start",
    )]


def test_task_refresh_watchlist_forwards_supported_cli_flags(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main([
        "refresh-watchlist",
        "--limit",
        "50",
        "--since-minutes",
        "30",
        "--top",
        "5",
        "--format",
        "json",
        "--force",
        "--sync",
        "--watch",
        "--replace-watch",
    ])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "markets",
        "refresh-watchlist",
        "--limit",
        "50",
        "--since-minutes",
        "30",
        "--top",
        "5",
        "--format",
        "json",
        "--force",
        "--sync",
        "--watch",
        "--replace-watch",
    )]


def test_task_refresh_watchlist_defaults_to_cli_gate(monkeypatch):
    from scripts import task

    calls: list[tuple] = []

    def fake_module(*args, env=None):
        calls.append(args)

    monkeypatch.setattr(task, "module", fake_module)

    rc = task.main(["refresh-watchlist"])

    assert rc == 0
    assert calls == [(
        "pmfi.cli",
        "markets",
        "refresh-watchlist",
    )]


def test_task_clean_checkout_smoke_forwards_supported_flags(monkeypatch):
    from scripts import task

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_python_script(script: str, *args: str) -> None:
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", fake_python_script)

    rc = task.main([
        "clean-checkout-smoke",
        "--ref",
        "origin/main",
        "--worktree-dir",
        "worktrees\\smoke",
        "--report-dir",
        "reports\\clean-checkout",
        "--timeout",
        "7",
        "--install-dev",
        "--run-verify",
        "--db-verify",
        "--keep-worktree",
    ])

    assert rc == 0
    assert calls == [(
        "scripts/clean_checkout_smoke.py",
        (
            "--ref",
            "origin/main",
            "--worktree-dir",
            "worktrees\\smoke",
            "--report-dir",
            "reports\\clean-checkout",
            "--timeout",
            "7",
            "--install-dev",
            "--run-verify",
            "--db-verify",
            "--keep-worktree",
        ),
    )]
