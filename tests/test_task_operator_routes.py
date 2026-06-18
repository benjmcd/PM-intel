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
