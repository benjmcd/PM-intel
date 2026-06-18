import argparse
import asyncio
import json
import builtins
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from pmfi.cli import main


def test_fixture_replay_runs(capsys):
    rc = main(["replay"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "replay" in captured.out.lower() or "fixture" in captured.out.lower()


def test_replay_persist_output_says_processed_not_wrote(tmp_path, monkeypatch, capsys):
    async def create_pool(_url):
        return object()

    async def close_pool(_pool):
        return None

    async def ensure_current_partitions(_pool):
        return None

    async def replay_fixtures_persist(_fixture_dir, _pool, **_kwargs):
        return []

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr("pmfi.db.migrations.ensure_current_partitions", ensure_current_partitions)
    monkeypatch.setattr("pmfi.replay.replay_fixtures_persist", replay_fixtures_persist)

    rc = main(["replay", "--persist", "--fixture-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "[persist] processed 0 normalized fixture(s) through DB pipeline" in captured.out
    assert "[persist] wrote" not in captured.out


def test_replay_persist_reports_db_unavailable_without_traceback(tmp_path, monkeypatch, capsys):
    async def create_pool(_url):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["replay", "--persist", "--fixture-dir", str(tmp_path)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[persist] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_replay_from_db_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    async def create_pool(_url):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["replay", "--from-db", "--limit", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[from-db] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def _assert_db_unavailable_cli(monkeypatch, capsys, argv, prefix):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url="postgresql://local/db"),
            ingestion=SimpleNamespace(raw_retention_days=30),
        ),
    )
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(argv)
    captured = capsys.readouterr()

    assert rc == 1
    assert f"[{prefix}] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_stats_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(monkeypatch, capsys, ["stats"], "stats")


def test_markets_list_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url="postgresql://local/db"),
            ingestion=SimpleNamespace(raw_retention_days=30),
        ),
    )
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["markets", "list", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert "db offline for test" in payload["error"]
    assert any("db_local.py" in action or "local Postgres" in action for action in payload["next_actions"])
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "[markets] DB unavailable" not in captured.out


def test_markets_list_json_empty_result_is_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))

    class _EmptyMarketListPool:
        async def fetch(self, *_args):
            return []

        async def close(self):
            return None

    async def create_pool(_url):
        return _EmptyMarketListPool()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["markets", "list", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload == {"ok": True, "count": 0, "markets": []}
    assert "No markets in DB" not in captured.out


def test_markets_discover_reports_pool_failure_as_db_unavailable(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name == "pmfi.markets":
            raise AssertionError("markets discover imported live sync before DB pool was available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")),
    )
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["markets", "discover", "--limit", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[markets discover] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Discover failed" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_markets_watch_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(monkeypatch, capsys, ["markets", "watch", "dummy"], "markets")


def test_markets_unwatch_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(monkeypatch, capsys, ["markets", "unwatch", "dummy"], "markets")


def test_baseline_list_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(monkeypatch, capsys, ["baseline", "list"], "baseline")


def test_baseline_compute_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(monkeypatch, capsys, ["baseline", "compute"], "baseline")


def test_baselines_compute_reports_pool_failure_as_db_unavailable(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")),
    )
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["baselines", "compute", "--days", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[baselines compute] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Failed" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_db_maintenance_reports_db_unavailable_without_traceback(monkeypatch, capsys):
    _assert_db_unavailable_cli(
        monkeypatch,
        capsys,
        ["db-maintenance", "--create-partitions"],
        "db-maintenance",
    )


def test_monitor_fixture_replay_skips_malformed_and_streams_valid_fixture(tmp_path, monkeypatch, capsys):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    source_dir = Path(__file__).resolve().parent / "fixtures" / "raw"
    shutil.copy(source_dir / "polymarket_last_trade_price.json", fixture_dir / "01_valid.json")
    shutil.copy(source_dir / "malformed_payload.json", fixture_dir / "02_malformed.json")

    async def create_pool(_url):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["monitor", "--fixture-replay", "--fixture-dir", str(fixture_dir), "--delay", "0"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "Stream complete" in captured.out
    assert "normalization skipped:" in captured.out
    assert "invalid decimal for price" in captured.out
    assert '"alert": true' in captured.out
    assert '"rule_id": "large_trade_absolute_v1"' in captured.out


def test_monitor_fixture_replay_without_asyncpg_uses_empty_baselines(tmp_path, monkeypatch, capsys):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    source_dir = Path(__file__).resolve().parent / "fixtures" / "raw"
    shutil.copy(source_dir / "polymarket_last_trade_price.json", fixture_dir / "01_valid.json")

    calls = {"create_pool": 0}

    async def create_pool(_url):
        calls["create_pool"] += 1
        raise RuntimeError("postgres unavailable")

    for module_name in ("pmfi.baseline", "pmfi.db.repos.baselines", "asyncpg"):
        sys.modules.pop(module_name, None)

    real_import = builtins.__import__

    def fail_asyncpg_import(name, *args, **kwargs):
        if name == "asyncpg":
            raise ModuleNotFoundError("No module named 'asyncpg'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr(builtins, "__import__", fail_asyncpg_import)

    rc = main(["monitor", "--fixture-replay", "--fixture-dir", str(fixture_dir), "--delay", "0"])
    captured = capsys.readouterr()

    assert rc == 0
    assert calls == {"create_pool": 1}
    assert "Loaded" not in captured.out
    assert "Stream complete" in captured.out
    assert '"alert": true' in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


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


def test_review_pass_parser_accepts_json_and_fixture_dir():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["review-pass", "--format", "json", "--fixture-dir", "fixtures"])
    assert args.command == "review-pass"
    assert args.format == "json"
    assert args.fixture_dir == "fixtures"


def test_review_pass_json_uses_fixtures_without_db(tmp_path, monkeypatch, capsys):
    fixture_dir = _copy_report_fixtures(tmp_path)
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name == "pmfi.config" or name.startswith("pmfi.db") or name.startswith("pmfi.adapters"):
            raise AssertionError(f"review-pass imported DB/config/live module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)
    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["source"] == "fixtures"
    assert payload["fixture_files"] == 2
    assert payload["normalized_trades"] == 2
    assert payload["alerts"] == 4
    assert any(check["name"] == "alert_required_fields" and check["status"] == "pass" for check in payload["checks"])
    assert any(check["name"] == "alert_data_quality" and check["status"] == "pass" for check in payload["checks"])
    assert any(r"python scripts\verify.py" in action for action in payload["next_actions"])


def test_review_pass_classifies_default_malformed_fixture_as_expected_dead_letter(capsys):
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "raw"

    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    fixture_skips = next(check for check in payload["checks"] if check["name"] == "fixture_skips")
    assert fixture_skips["status"] == "pass"
    assert fixture_skips["details"]["expected_dead_letter_count"] == 2
    assert fixture_skips["details"]["benign_non_trade_count"] == 0
    expected = fixture_skips["details"]["expected"]
    malformed = next(item for item in expected if item["source_event_id"] == "pm-malformed-1")
    assert malformed["classification"] == "expected_dead_letter"
    assert malformed["dead_letter_error_class"] == "invalid_price_or_size"
    assert malformed["dead_letter_stage"] == "normalization"
    assert malformed["dead_letter_expected"] is True
    assert malformed["no_derived_records_expected"] is True
    assert malformed["raw_event_expected"] is True
    assert malformed["venue_market_id"] == "pm-bad-market"
    kalshi_malformed = next(item for item in expected if item["source_event_id"] == "ks-rest-malformed-1")
    assert kalshi_malformed["classification"] == "expected_dead_letter"
    assert kalshi_malformed["dead_letter_error_class"] == "invalid_price_or_size"
    assert kalshi_malformed["dead_letter_stage"] == "normalization"
    assert kalshi_malformed["dead_letter_expected"] is True
    assert kalshi_malformed["no_derived_records_expected"] is True
    assert kalshi_malformed["raw_event_expected"] is True
    assert kalshi_malformed["venue_code"] == "kalshi"
    assert kalshi_malformed["source_channel"] == "rest_trades"
    assert kalshi_malformed["source_event_type"] == "trade"
    assert kalshi_malformed["venue_market_id"] == "KXBTCD-23DEC3100"
    assert "not-a-count" in kalshi_malformed["error"]
    data_quality = next(check for check in payload["checks"] if check["name"] == "alert_data_quality")
    assert data_quality["status"] == "pass"


def test_review_pass_classifies_benign_non_trade_skip_without_dead_letter(tmp_path, capsys):
    fixture_dir = _copy_report_fixtures(tmp_path)
    (fixture_dir / "polymarket_subscription_ack.json").write_text(
        json.dumps(
            {
                "venue_code": "polymarket",
                "source_channel": "market_ws",
                "source_event_type": "subscription_ack",
                "source_event_id": "pm-ack-1",
                "venue_market_id": "pm-ack-market",
                "payload": {
                    "status": "subscribed",
                    "market": "pm-ack-market",
                },
            }
        ),
        encoding="utf-8",
    )

    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    fixture_skips = next(check for check in payload["checks"] if check["name"] == "fixture_skips")
    assert fixture_skips["status"] == "pass"
    assert fixture_skips["details"]["expected_dead_letter_count"] == 0
    assert fixture_skips["details"]["benign_non_trade_count"] == 1
    benign = fixture_skips["details"]["expected"][0]
    assert benign["classification"] == "benign_non_trade"
    assert benign["reason"] == "benign_non_trade"
    assert benign["dead_letter_expected"] is False
    assert benign["no_derived_records_expected"] is True
    assert benign["raw_event_expected"] is True
    assert benign["source_event_id"] == "pm-ack-1"
    assert benign["source_event_type"] == "subscription_ack"
    assert benign["source_channel"] == "market_ws"
    assert benign["venue_code"] == "polymarket"
    assert benign["venue_market_id"] == "pm-ack-market"


def test_review_pass_classifies_unsupported_venue_as_expected_dead_letter(tmp_path, capsys):
    fixture_dir = _copy_report_fixtures(tmp_path)
    (fixture_dir / "unsupported_venue.json").write_text(
        json.dumps(
            {
                "venue_code": "predictit",
                "source_channel": "fixture",
                "source_event_type": "trade",
                "source_event_id": "unsupported-venue-1",
                "venue_market_id": "predictit-market",
                "payload": {
                    "price": "0.50",
                    "size": "10",
                },
            }
        ),
        encoding="utf-8",
    )

    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    fixture_skips = next(check for check in payload["checks"] if check["name"] == "fixture_skips")
    assert fixture_skips["status"] == "pass"
    assert fixture_skips["details"]["expected_dead_letter_count"] == 1
    assert fixture_skips["details"]["benign_non_trade_count"] == 0
    unsupported = fixture_skips["details"]["expected"][0]
    assert unsupported["classification"] == "expected_dead_letter"
    assert unsupported["dead_letter_error_class"] == "unsupported_venue"
    assert unsupported["dead_letter_stage"] == "normalization"
    assert unsupported["dead_letter_expected"] is True
    assert unsupported["no_derived_records_expected"] is True
    assert unsupported["raw_event_expected"] is True
    assert unsupported["source_event_id"] == "unsupported-venue-1"
    assert unsupported["venue_code"] == "predictit"


def test_review_pass_fails_unclassified_skipped_fixture(tmp_path, capsys):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "broken.json").write_text("{", encoding="utf-8")

    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    fixture_skips = next(check for check in payload["checks"] if check["name"] == "fixture_skips")
    assert fixture_skips["status"] == "fail"
    assert fixture_skips["details"]["unexpected"][0]["classification"] == "load_error"


def test_review_pass_rejects_missing_fixture_dir(tmp_path, capsys):
    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(tmp_path / "missing")])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert any(check["name"] == "fixture_directory" and check["status"] == "fail" for check in payload["checks"])


def test_review_pass_fails_missing_alert_explainability(tmp_path, monkeypatch, capsys):
    from pmfi.domain import AlertDecision, NormalizedTrade
    from pmfi.replay import ReplayResult

    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "bad.json").write_text("{}", encoding="utf-8")

    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="pm-bad",
        outcome_key="yes",
        price=Decimal("0.5"),
        contracts=Decimal("10"),
        capital_at_risk_usd=Decimal("5"),
        payout_notional_usd=Decimal("10"),
        source_payload={"raw": True},
    )
    decision = AlertDecision(
        emit_alert=True,
        rule_id="bad_rule",
        rule_version="v1",
        severity="medium",
        confidence="low",
        score=Decimal("0.5"),
        reason_codes=(),
        evidence={},
        data_quality="",
    )

    def fake_replay_fixtures(_fixture_dir):
        return [ReplayResult(fixture_path=str(fixture_dir / "bad.json"), trade=trade, alerts=[decision])]

    monkeypatch.setattr("pmfi.replay.replay_fixtures", fake_replay_fixtures)
    rc = main(["review-pass", "--format", "json", "--fixture-dir", str(fixture_dir)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    required = next(check for check in payload["checks"] if check["name"] == "alert_required_fields")
    assert required["status"] == "fail"
    assert required["details"]["missing_fields"]["reason_codes"] == 1
    assert required["details"]["missing_fields"]["evidence"] == 1
    assert required["details"]["missing_fields"]["data_quality"] == 1


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


def test_alerts_serve_rejects_non_loopback_host(capsys):
    rc = main(["alerts", "serve", "--host", "0.0.0.0"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid host" in captured.out
    assert "0.0.0.0" in captured.out


def test_alerts_serve_parser_accepts_loopback_hosts():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["alerts", "serve", "--host", "::1"])
    assert args.host == "::1"


def test_ingest_rejects_unsupported_delivery_mode(monkeypatch, capsys):
    from pmfi.cli import cmd_ingest

    alerts = SimpleNamespace(default_delivery="external_receiver", allowed_delivery_modes=["external_receiver"])
    features = SimpleNamespace(enable_polymarket_live=False, enable_kalshi_live=False)
    cfg = SimpleNamespace(alerts=alerts, features=features)
    monkeypatch.setattr("pmfi.config.load_config", lambda: cfg)

    rc = cmd_ingest(SimpleNamespace(venue=["polymarket"], dry_run=False))
    captured = capsys.readouterr()
    assert rc == 1
    assert "unsupported alerts.default_delivery" in captured.out
    assert "external_receiver" in captured.out


def _ingest_check_config():
    alerts = SimpleNamespace(default_delivery="console", allowed_delivery_modes=["console", "file", "localhost_http_receiver"])
    features = SimpleNamespace(enable_polymarket_live=False, enable_kalshi_live=False)
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(alerts=alerts, features=features, database=database)


def _ingest_runtime_config():
    ingestion = SimpleNamespace(
        reconnect_initial_backoff=0.01,
        reconnect_max_backoff=0.01,
    )
    alerts = SimpleNamespace(
        default_delivery="console",
        allowed_delivery_modes=["console", "file", "localhost_http_receiver"],
        suppression_window_seconds=300,
    )
    features = SimpleNamespace(
        enable_polymarket_live=False,
        enable_kalshi_live=False,
        enable_orderbook_reconstruction=False,
    )
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(ingestion=ingestion, alerts=alerts, features=features, database=database)


class _IngestCheckAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _IngestCheckPool:
    def __init__(self):
        self.conn = object()
        self.closed = False

    def acquire(self):
        return _IngestCheckAcquire(self.conn)

    async def close(self):
        self.closed = True


def _patch_ingest_check_dependencies(monkeypatch, *, watched, baselines=None, asset_map=None, integrity_ok=True):
    pool = _IngestCheckPool()
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    async def verify_database_integrity(pool_arg):
        return SimpleNamespace(
            ok=integrity_ok,
            status="ready" if integrity_ok else "blocked",
        )

    async def load_baselines(pool_arg):
        return baselines if baselines is not None else {"kalshi:KXEXAMPLE-26JUN03": {"sample_size": 2}}

    async def fetch_watched_markets(conn):
        return watched

    async def load_asset_id_mapping(pool_arg):
        return asset_map or {}

    monkeypatch.setitem(sys.modules, "pmfi.baseline", SimpleNamespace(load_baselines=load_baselines))
    monkeypatch.setitem(sys.modules, "pmfi.db.repos.markets", SimpleNamespace(fetch_watched_markets=fetch_watched_markets))
    monkeypatch.setitem(sys.modules, "pmfi.db.verify", SimpleNamespace(verify_database_integrity=verify_database_integrity))
    monkeypatch.setitem(sys.modules, "pmfi.markets", SimpleNamespace(load_asset_id_mapping=load_asset_id_mapping))
    monkeypatch.setattr("pmfi.config.load_config", _ingest_check_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    return pool, calls


def _patch_ingest_runtime_dependencies(monkeypatch, *, watched, engine_cls, asset_map=None, baselines=None):
    pool = _LiveSmokePool([])
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    async def startup_maintenance(pool_arg):
        return None

    async def load_baselines(pool_arg):
        return baselines if baselines is not None else {}

    async def fetch_watched_markets(conn):
        return watched

    async def load_asset_id_mapping(pool_arg):
        return asset_map or {}

    monkeypatch.setattr("pmfi.config.load_config", _ingest_runtime_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr("pmfi.db.migrations.startup_maintenance", startup_maintenance)
    monkeypatch.setattr("pmfi.baseline.load_baselines", load_baselines)
    monkeypatch.setattr("pmfi.db.repos.markets.fetch_watched_markets", fetch_watched_markets)
    monkeypatch.setattr("pmfi.markets.load_asset_id_mapping", load_asset_id_mapping)
    monkeypatch.setattr("pmfi.pipeline.engine.AlertEngine", engine_cls)
    return pool, calls


def _guard_live_adapter_imports(monkeypatch):
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters"):
            raise AssertionError(f"ingest --check imported live adapter {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)


class _RuntimeRecorder:
    def __init__(self):
        self.calls = []

    async def record_connection_start(self, pool, *, venue_code, source_channel, metadata=None):
        connection_id = f"conn-{venue_code}-{source_channel}"
        self.calls.append(("start", connection_id, venue_code, source_channel, metadata or {}))
        return connection_id

    async def record_connection_message(self, pool, connection_id):
        self.calls.append(("message", connection_id))

    async def record_connection_stop(self, pool, connection_id, *, metadata=None):
        self.calls.append(("stop", connection_id, metadata or {}))

    async def record_connection_error(self, pool, connection_id, error, *, metadata=None):
        self.calls.append(("error", connection_id, str(error), metadata or {}))

    async def record_heartbeat(self, pool, *, worker_name, worker_type, status="healthy", metadata=None):
        self.calls.append(("heartbeat", worker_name, worker_type, status, metadata or {}))


def _patch_ingest_runtime_recorder(monkeypatch):
    from pmfi.db.repos import ingestion_runtime

    recorder = _RuntimeRecorder()
    monkeypatch.setattr(ingestion_runtime, "record_connection_start", recorder.record_connection_start)
    monkeypatch.setattr(ingestion_runtime, "record_connection_message", recorder.record_connection_message)
    monkeypatch.setattr(ingestion_runtime, "record_connection_stop", recorder.record_connection_stop)
    monkeypatch.setattr(ingestion_runtime, "record_connection_error", recorder.record_connection_error)
    monkeypatch.setattr(ingestion_runtime, "record_heartbeat", recorder.record_heartbeat)
    return recorder


def test_ingest_parser_accepts_check_json():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["ingest", "--venue", "kalshi", "--check", "--format", "json"])
    assert args.check is True
    assert args.format == "json"


def test_ingest_parser_accepts_bounded_proof_flags():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["ingest", "--venue", "kalshi", "--max-events", "3", "--max-seconds", "2.5"])
    assert args.max_events == 3
    assert args.max_seconds == 2.5


def test_ingest_parser_accepts_fixture_source():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "ingest",
        "--venue",
        "kalshi",
        "--fixture-source",
        "tests/fixtures/live-smoke/kalshi_persist.json",
    ])
    assert args.venue == ["kalshi"]
    assert args.fixture_source == "tests/fixtures/live-smoke/kalshi_persist.json"


def test_ingest_reports_create_pool_failure_as_db_unavailable(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _ingest_runtime_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["ingest", "--venue", "kalshi", "--max-events", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[ingest] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "fatal error" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_ingest_check_json_reports_pool_failure_as_db_unavailable(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _ingest_check_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "kalshi", "--check", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert any("DB unavailable: db offline for test" in check["message"] for check in payload["checks"])
    assert any("python scripts\\db_local.py up" in action for action in payload["next_actions"])
    assert any("python scripts\\db_local.py verify" in action for action in payload["next_actions"])
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_ingest_check_table_reports_pool_failure_as_db_unavailable(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _ingest_check_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "kalshi", "--check"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "DB unavailable: db offline for test" in captured.out
    assert "python scripts\\db_local.py up" in captured.out
    assert "python scripts\\db_local.py verify" in captured.out
    assert "ingest readiness failed" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_ingest_check_json_ready_without_live_adapter_imports(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEXAMPLE-26JUN03",
            "title": "Example",
            "category": None,
            "status": "active",
        }
    ]
    pool, calls = _patch_ingest_check_dependencies(monkeypatch, watched=watched)
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "kalshi", "--check", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["subscriptions"]["kalshi_tickers"] == 1
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["live_connections"]["status"] == "pass"
    assert checks["kalshi_subscriptions"]["status"] == "pass"
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True


def test_ingest_check_json_ready_kalshi_includes_ticker_plan_details(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi-b",
            "venue_code": "kalshi",
            "venue_market_id": "KXZZ-26JUN03",
            "title": "Later example",
            "category": None,
            "status": "active",
        },
        {
            "market_id": "market-kalshi-a",
            "venue_code": "kalshi",
            "venue_market_id": "KXAA-26JUN03",
            "title": "Earlier example",
            "category": None,
            "status": "open",
        },
    ]
    _patch_ingest_check_dependencies(monkeypatch, watched=watched)
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "kalshi", "--check", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["subscriptions"]["kalshi_tickers"] == 2
    assert payload["subscriptions"]["kalshi_markets"] == [
        {
            "market_id": "market-kalshi-a",
            "venue_market_id": "KXAA-26JUN03",
            "ticker": "KXAA-26JUN03",
            "title": "Earlier example",
            "status": "open",
        },
        {
            "market_id": "market-kalshi-b",
            "venue_market_id": "KXZZ-26JUN03",
            "ticker": "KXZZ-26JUN03",
            "title": "Later example",
            "status": "active",
        },
    ]


def test_ingest_check_json_ready_polymarket_groups_asset_ids_by_watched_market(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-poly-b",
            "venue_code": "polymarket",
            "venue_market_id": "0xcondition-b",
            "title": "B market",
            "category": None,
            "status": "active",
        },
        {
            "market_id": "market-poly-a",
            "venue_code": "polymarket",
            "venue_market_id": "0xcondition-a",
            "title": "A market",
            "category": None,
            "status": "open",
        },
    ]
    asset_map = {
        "token-z": {
            "market_id": "market-poly-a",
            "venue_market_id": "0xcondition-a",
            "venue_code": "polymarket",
            "outcome_key": "yes",
            "outcome_label": "Yes",
        },
        "token-a": {
            "market_id": "market-poly-a",
            "venue_market_id": "0xcondition-a",
            "venue_code": "polymarket",
            "outcome_key": "no",
            "outcome_label": "No",
        },
        "token-b": {
            "market_id": "market-poly-b",
            "venue_market_id": "0xcondition-b",
            "venue_code": "polymarket",
            "outcome_key": "yes",
            "outcome_label": "Yes",
        },
    }
    _patch_ingest_check_dependencies(monkeypatch, watched=watched, asset_map=asset_map)
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "polymarket", "--check", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["subscriptions"]["polymarket_asset_ids"] == 3
    assert payload["subscriptions"]["polymarket_markets"] == [
        {
            "market_id": "market-poly-a",
            "venue_market_id": "0xcondition-a",
            "title": "A market",
            "status": "open",
            "asset_id_count": 2,
            "asset_ids": ["token-a", "token-z"],
        },
        {
            "market_id": "market-poly-b",
            "venue_market_id": "0xcondition-b",
            "title": "B market",
            "status": "active",
            "asset_id_count": 1,
            "asset_ids": ["token-b"],
        },
    ]


def test_ingest_check_blocks_polymarket_without_token_ids(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-poly",
            "venue_code": "polymarket",
            "venue_market_id": "0xcondition",
            "title": "Example",
            "category": None,
            "status": "active",
        }
    ]
    _patch_ingest_check_dependencies(monkeypatch, watched=watched, asset_map={})
    _guard_live_adapter_imports(monkeypatch)

    rc = main(["ingest", "--venue", "polymarket", "--check", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["polymarket_subscriptions"]["status"] == "fail"
    assert checks["live_connections"]["status"] == "pass"
    assert payload["subscriptions"]["polymarket_asset_ids"] == 0
    assert payload["subscriptions"]["polymarket_markets"] == [
        {
            "market_id": "market-poly",
            "venue_market_id": "0xcondition",
            "title": "Example",
            "status": "active",
            "asset_id_count": 0,
            "asset_ids": [],
        }
    ]


def _live_smoke_config():
    ingestion = SimpleNamespace(
        live_api_timeout_seconds=1,
        reconnect_initial_backoff=0.01,
        reconnect_max_backoff=0.01,
    )
    alerts = SimpleNamespace(suppression_window_seconds=300)
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(ingestion=ingestion, alerts=alerts, database=database)


class _LiveSmokeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _LiveSmokePool:
    def __init__(self, rows):
        self.rows = rows
        self.conn = AsyncMock()
        self.closed = False
        self.sql: list[str] = []

    def acquire(self):
        return _LiveSmokeAcquire(self.conn)

    async def fetch(self, sql):
        self.sql.append(sql)
        return self.rows

    async def close(self):
        self.closed = True


def _patch_live_smoke_config_and_db(monkeypatch, rows):
    pool = _LiveSmokePool(rows)
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    return pool, calls


def _fake_live_adapter_class(venue_code):
    from pmfi.domain import RawEvent

    class _FakeLiveAdapter:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connected = False
            self.disconnected = False
            self.__class__.instances.append(self)

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.disconnected = True
            self.connected = False

        async def events(self):
            if venue_code == "kalshi":
                payload = {
                    "ticker": "KXEXAMPLE-26JUN03",
                    "trade_id": "kalshi-live-smoke-1",
                    "price": "0.37",
                    "count": "100",
                    "taker_side": "yes",
                }
                venue_market_id = "KXEXAMPLE-26JUN03"
            else:
                payload = {
                    "market": "0xcondition",
                    "trade_id": "poly-live-smoke-1",
                    "price": "0.5",
                    "size": "1",
                    "side": "buy",
                    "outcome": "yes",
                }
                venue_market_id = "0xcondition"
            yield RawEvent(
                venue_code=venue_code,
                source_channel="ws_test",
                source_event_type="trade",
                source_event_id=f"{venue_code}-event-1",
                venue_market_id=venue_market_id,
                payload=payload,
            )

        async def __aenter__(self):
            await self.connect()
            return self

        async def __aexit__(self, *args):
            await self.disconnect()

    return _FakeLiveAdapter


def test_ingest_bounded_persisted_summary_runs_pipeline_and_closes_resources(monkeypatch, capsys):
    from pmfi.domain import AlertDecision
    from pmfi.pipeline import runner

    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEXAMPLE-26JUN03",
            "title": "Example",
            "category": None,
            "status": "active",
        }
    ]
    created_engines = []

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines
            self.trades = []
            created_engines.append(self)

        def evaluate(self, trade):
            self.trades.append(trade)
            return [
                AlertDecision(
                    emit_alert=True,
                    rule_id="large_trade_absolute_v1",
                    rule_version="alert_rules.v1",
                    severity="medium",
                    confidence="medium",
                    score=Decimal("0.6"),
                    reason_codes=("capital_at_risk_threshold",),
                    evidence={"capital_at_risk_usd": str(trade.capital_at_risk_usd)},
                    data_quality="verified",
                )
            ]

    baselines = {"kalshi:KXEXAMPLE-26JUN03": {"sample_size": 10, "p99_trade_usd": Decimal("25")}}
    pool, calls = _patch_ingest_runtime_dependencies(
        monkeypatch,
        watched=watched,
        engine_cls=_FakeAlertEngine,
        baselines=baselines,
    )
    fake_adapter = _fake_live_adapter_class("kalshi")
    deliver_stdout = AsyncMock()
    load_suppression_cache = AsyncMock(return_value={})
    insert_raw_event = AsyncMock(return_value=(901, False))
    upsert_market = AsyncMock(return_value="cccccccc-cccc-cccc-cccc-cccccccccccc")
    insert_trade = AsyncMock(return_value="trade-db-1")
    upsert_metric_window = AsyncMock()
    insert_alert = AsyncMock(return_value="alert-db-1")

    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=fake_adapter))
    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", deliver_stdout)
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", load_suppression_cache)
    monkeypatch.setattr(runner, "insert_raw_event", insert_raw_event)
    monkeypatch.setattr(runner, "upsert_market", upsert_market)
    monkeypatch.setattr(runner, "insert_trade", insert_trade)
    monkeypatch.setattr(runner, "upsert_metric_window", upsert_metric_window)
    monkeypatch.setattr(runner, "insert_alert", insert_alert)

    rc = main(["ingest", "--venue", "kalshi", "--max-events", "1", "--max-seconds", "5"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[ingest] bounded run complete: raw_events_seen=1" in out
    assert "[ingest] persisted summary:" in out
    assert "raw_events_seen=1" in out
    assert "raw_events_inserted=1" in out
    assert "normalized_trades_inserted=1" in out
    assert "alerts_inserted=1" in out
    assert "alerts_delivered=1" in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert fake_adapter.instances[0].kwargs["tickers"] == ["KXEXAMPLE-26JUN03"]
    assert fake_adapter.instances[0].disconnected is True
    load_suppression_cache.assert_awaited_once_with(pool.conn, window_seconds=300)
    insert_raw_event.assert_awaited_once()
    upsert_market.assert_awaited_once()
    insert_trade.assert_awaited_once()
    upsert_metric_window.assert_awaited_once()
    insert_alert.assert_awaited_once()
    deliver_stdout.assert_awaited_once()
    assert created_engines[0].baselines == baselines
    assert created_engines[0].trades[0].venue_market_id == "KXEXAMPLE-26JUN03"


def test_ingest_fixture_source_runs_pipeline_without_adapter_imports_and_closes_resources(monkeypatch, capsys):
    from pmfi.domain import AlertDecision
    from pmfi.pipeline import runner

    created_engines = []

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines
            self.trades = []
            created_engines.append(self)

        def evaluate(self, trade):
            self.trades.append(trade)
            return [
                AlertDecision(
                    emit_alert=True,
                    rule_id="large_trade_absolute_v1",
                    rule_version="alert_rules.v1",
                    severity="medium",
                    confidence="medium",
                    score=Decimal("0.6"),
                    reason_codes=("capital_at_risk_threshold",),
                    evidence={"capital_at_risk_usd": str(trade.capital_at_risk_usd)},
                    data_quality="verified",
                )
            ]

    baselines = {"kalshi:KXLIVE-SMOKE-26JUN03": {"sample_size": 10, "p99_trade_usd": Decimal("25")}}
    pool, calls = _patch_ingest_runtime_dependencies(
        monkeypatch,
        watched=[],
        engine_cls=_FakeAlertEngine,
        baselines=baselines,
    )
    deliver_stdout = AsyncMock()
    load_suppression_cache = AsyncMock(return_value={})
    insert_raw_event = AsyncMock(return_value=(1001, False))
    upsert_market = AsyncMock(return_value="dddddddd-dddd-dddd-dddd-dddddddddddd")
    insert_trade = AsyncMock(return_value="trade-db-1")
    upsert_metric_window = AsyncMock()
    insert_alert = AsyncMock(return_value="alert-db-1")

    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", deliver_stdout)
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", load_suppression_cache)
    monkeypatch.setattr(runner, "insert_raw_event", insert_raw_event)
    monkeypatch.setattr(runner, "upsert_market", upsert_market)
    monkeypatch.setattr(runner, "insert_trade", insert_trade)
    monkeypatch.setattr(runner, "upsert_metric_window", upsert_metric_window)
    monkeypatch.setattr(runner, "insert_alert", insert_alert)
    _guard_live_adapter_imports(monkeypatch)

    rc = main([
        "ingest",
        "--fixture-source",
        "tests/fixtures/live-smoke/kalshi_persist.json",
        "--venue",
        "kalshi",
        "--max-events",
        "1",
        "--max-seconds",
        "5",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "[ingest] fixture_source=1 file(s)" in out
    assert "venue=kalshi" in out
    assert "[ingest] bounded run complete: raw_events_seen=1" in out
    assert "[ingest] persisted summary:" in out
    assert "raw_events_seen=1 raw_events_inserted=1 raw_event_duplicates=0" in out
    assert "normalized_trades_inserted=1 duplicate_trades=0 non_trade_skips=0" in out
    assert "alerts_inserted=1 alerts_delivered=1 alerts_suppressed=0" in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    load_suppression_cache.assert_awaited_once_with(pool.conn, window_seconds=300)
    insert_raw_event.assert_awaited_once()
    upsert_market.assert_awaited_once()
    insert_trade.assert_awaited_once()
    upsert_metric_window.assert_awaited_once()
    insert_alert.assert_awaited_once()
    deliver_stdout.assert_awaited_once()
    assert created_engines[0].baselines == baselines
    assert created_engines[0].trades[0].venue_market_id == "KXLIVE-SMOKE-26JUN03"


def test_ingest_fixture_source_records_runtime_state(monkeypatch, capsys):
    from pmfi.pipeline import runner

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines

        def evaluate(self, trade):
            return []

    _patch_ingest_runtime_dependencies(monkeypatch, watched=[], engine_cls=_FakeAlertEngine)
    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", AsyncMock())
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", AsyncMock(return_value={}))
    monkeypatch.setattr(runner, "insert_raw_event", AsyncMock(return_value=(1002, False)))
    monkeypatch.setattr(runner, "upsert_market", AsyncMock(return_value="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"))
    monkeypatch.setattr(runner, "insert_trade", AsyncMock(return_value="trade-db-2"))
    monkeypatch.setattr(runner, "upsert_metric_window", AsyncMock())
    recorder = _patch_ingest_runtime_recorder(monkeypatch)
    _guard_live_adapter_imports(monkeypatch)

    rc = main([
        "ingest",
        "--fixture-source",
        "tests/fixtures/live-smoke/kalshi_persist.json",
        "--venue",
        "kalshi",
        "--max-events",
        "1",
        "--max-seconds",
        "5",
    ])

    assert rc == 0
    assert ("message", "conn-kalshi-fixture_source") in recorder.calls
    assert any(call[:4] == ("start", "conn-kalshi-fixture_source", "kalshi", "fixture_source") for call in recorder.calls)
    assert any(call[:2] == ("stop", "conn-kalshi-fixture_source") for call in recorder.calls)
    heartbeat_statuses = [call[3] for call in recorder.calls if call[0] == "heartbeat"]
    assert "running" in heartbeat_statuses
    assert "stopped" in heartbeat_statuses
    assert not any(call[0] == "error" for call in recorder.calls)


def test_ingest_bounded_zero_event_run_fails_closed_and_closes_resources(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEMPTY-26JUN03",
            "title": "Empty",
            "category": None,
            "status": "active",
        }
    ]

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines

        def evaluate(self, trade):
            return []

    class _EmptyKalshiAdapter:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connected = False
            self.disconnected = False
            self.__class__.instances.append(self)

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.disconnected = True
            self.connected = False

        async def events(self):
            while True:
                await asyncio.sleep(1)
                if False:
                    yield None

    pool, calls = _patch_ingest_runtime_dependencies(
        monkeypatch,
        watched=watched,
        engine_cls=_FakeAlertEngine,
    )
    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=_EmptyKalshiAdapter))
    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", AsyncMock())
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", AsyncMock(return_value={}))

    rc = main(["ingest", "--venue", "kalshi", "--max-seconds", "0.01"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "[ingest] reached max_seconds=0.01" in out
    assert "[ingest] bounded run complete: raw_events_seen=0" in out
    assert "[ingest] persisted summary:" in out
    assert "raw_events_seen=0" in out
    assert "bounded ingest proof saw zero raw events" in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert _EmptyKalshiAdapter.instances[0].disconnected is True


def test_ingest_bounded_zero_event_timeout_records_terminal_runtime_state(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEMPTY-26JUN03",
            "title": "Empty",
            "category": None,
            "status": "active",
        }
    ]

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines

        def evaluate(self, trade):
            return []

    class _EmptyKalshiAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def events(self):
            while True:
                await asyncio.sleep(1)
                if False:
                    yield None

    _patch_ingest_runtime_dependencies(monkeypatch, watched=watched, engine_cls=_FakeAlertEngine)
    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=_EmptyKalshiAdapter))
    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", AsyncMock())
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", AsyncMock(return_value={}))
    recorder = _patch_ingest_runtime_recorder(monkeypatch)

    rc = main(["ingest", "--venue", "kalshi", "--max-seconds", "0.01"])

    assert rc == 1
    assert any(call[:4] == ("start", "conn-kalshi-websocket", "kalshi", "websocket") for call in recorder.calls)
    terminal_calls = [call for call in recorder.calls if call[0] in {"stop", "error"}]
    assert terminal_calls
    assert terminal_calls[-1][0] in {"stop", "error"}


def test_ingest_runtime_start_failure_disconnects_connected_adapter(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEXAMPLE-26JUN03",
            "title": "Example",
            "category": None,
            "status": "active",
        }
    ]

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines

    class _ConnectedKalshiAdapter:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connected = False
            self.disconnected = False
            self.__class__.instances.append(self)

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.disconnected = True
            self.connected = False

        async def events(self):
            if False:
                yield None

    _patch_ingest_runtime_dependencies(monkeypatch, watched=watched, engine_cls=_FakeAlertEngine)
    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=_ConnectedKalshiAdapter))
    monkeypatch.setattr("pmfi.delivery.stdout.deliver_stdout", AsyncMock())
    recorder = _patch_ingest_runtime_recorder(monkeypatch)

    async def failing_start(*args, **kwargs):
        raise RuntimeError("runtime state unavailable")

    from pmfi.db.repos import ingestion_runtime

    monkeypatch.setattr(ingestion_runtime, "record_connection_start", failing_start)

    rc = main(["ingest", "--venue", "kalshi", "--max-events", "1", "--max-seconds", "5"])

    assert rc == 1
    assert _ConnectedKalshiAdapter.instances[0].disconnected is True
    assert not any(call[0] == "stop" for call in recorder.calls)
    assert "runtime state unavailable" in capsys.readouterr().out


def test_ingest_check_avoids_runtime_state_writes_and_live_adapter_imports(monkeypatch, capsys):
    watched = [
        {
            "market_id": "market-kalshi",
            "venue_code": "kalshi",
            "venue_market_id": "KXEXAMPLE-26JUN03",
            "title": "Example",
            "category": None,
            "status": "active",
        }
    ]
    _patch_ingest_check_dependencies(monkeypatch, watched=watched)
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name == "pmfi.db.repos.ingestion_runtime" or name.startswith("pmfi.adapters"):
            raise AssertionError(f"ingest --check imported runtime/live module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["ingest", "--venue", "kalshi", "--check", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True


def test_live_smoke_requires_live_gate_without_importing_adapters(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters"):
            raise AssertionError(f"live-smoke imported adapter before opt-in gate: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["live-smoke", "--venue", "polymarket", "--max-events", "1"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "PMFI_ENABLE_LIVE" in out


def test_live_smoke_fixture_source_runs_without_live_gate_or_adapter_imports(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters") or name == "pmfi.db" or name.startswith("pmfi.db."):
            raise AssertionError(f"fixture-source live-smoke imported network/DB module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main([
        "live-smoke",
        "--fixture-source",
        "tests/fixtures/raw/kalshi_live_ws_trade.json",
        "--max-events",
        "1",
        "--max-seconds",
        "5",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "fixture_source=1 file(s)" in out
    assert "done: 1 event(s) processed, 1 captured" in out


def test_live_smoke_fixture_source_persist_raw_reports_db_pool_failure(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)

    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setitem(sys.modules, "pmfi.db.migrations", SimpleNamespace(ensure_current_partitions=AsyncMock()))
    monkeypatch.setitem(sys.modules, "pmfi.pipeline.engine", SimpleNamespace(AlertEngine=MagicMock()))
    monkeypatch.setitem(sys.modules, "pmfi.pipeline.runner", SimpleNamespace(PipelineStats=MagicMock, run_adapter_pipeline=AsyncMock()))
    monkeypatch.setitem(sys.modules, "pmfi.baseline", SimpleNamespace(load_baselines=AsyncMock(return_value={})))

    rc = main([
        "live-smoke",
        "--fixture-source",
        "tests/fixtures/live-smoke/kalshi_persist.json",
        "--persist-raw",
        "--force",
        "--venue",
        "kalshi",
        "--max-events",
        "1",
    ])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[live-smoke] DB unavailable: db offline for test" in captured.out
    assert "python scripts\\db_local.py up" in captured.out
    assert "python scripts\\db_local.py verify" in captured.out
    assert "[live-smoke] fatal error" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_live_smoke_polymarket_watched_subscription_db_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    monkeypatch.setattr(
        "pmfi.cli._load_live_smoke_polymarket_asset_ids",
        AsyncMock(side_effect=RuntimeError("db offline for test")),
    )

    rc = main(["live-smoke", "--force", "--venue", "polymarket", "--max-events", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[live-smoke] DB unavailable: db offline for test" in captured.out
    assert "python scripts\\db_local.py up" in captured.out
    assert "python scripts\\db_local.py verify" in captured.out
    assert "requires asset IDs" not in captured.out
    assert "requires market tickers" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_live_smoke_kalshi_watched_subscription_db_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    monkeypatch.setattr(
        "pmfi.cli._load_live_smoke_kalshi_tickers",
        AsyncMock(side_effect=RuntimeError("db offline for test")),
    )

    rc = main(["live-smoke", "--force", "--venue", "kalshi", "--max-events", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[live-smoke] DB unavailable: db offline for test" in captured.out
    assert "python scripts\\db_local.py up" in captured.out
    assert "python scripts\\db_local.py verify" in captured.out
    assert "requires asset IDs" not in captured.out
    assert "requires market tickers" not in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_live_smoke_polymarket_loads_watched_outcomes_and_closes_adapter(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    pool, calls = _patch_live_smoke_config_and_db(
        monkeypatch,
        [{"venue_outcome_id": "asset-no"}, {"venue_outcome_id": "asset-yes"}],
    )
    fake_adapter = _fake_live_adapter_class("polymarket")
    monkeypatch.setitem(sys.modules, "pmfi.adapters.polymarket", SimpleNamespace(PolymarketAdapter=fake_adapter))

    rc = main(["live-smoke", "--venue", "polymarket", "--force", "--max-events", "1", "--max-seconds", "5"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "done: 1 event(s) processed, 1 captured" in out
    assert "persisted summary:" not in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert "market_outcomes" in pool.sql[0]
    assert fake_adapter.instances[0].kwargs["asset_ids"] == ["asset-no", "asset-yes"]
    assert fake_adapter.instances[0].disconnected is True


def test_live_smoke_kalshi_loads_watched_tickers_and_closes_adapter(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    pool, calls = _patch_live_smoke_config_and_db(
        monkeypatch,
        [{"venue_market_id": "KXEXAMPLE-26JUN03"}],
    )
    fake_adapter = _fake_live_adapter_class("kalshi")
    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=fake_adapter))

    rc = main(["live-smoke", "--venue", "kalshi", "--force", "--max-events", "1", "--max-seconds", "5"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "done: 1 event(s) processed, 1 captured" in out
    assert "persisted summary:" not in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert "venue_code='kalshi'" in pool.sql[0]
    assert fake_adapter.instances[0].kwargs["tickers"] == ["KXEXAMPLE-26JUN03"]
    assert fake_adapter.instances[0].disconnected is True


def test_live_smoke_returns_nonzero_on_adapter_startup_failure(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)

    class _FailingKalshiAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def connect(self):
            raise RuntimeError("socket unavailable")

        async def disconnect(self):
            return None

        async def events(self):
            if False:
                yield None

    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=_FailingKalshiAdapter))

    rc = main([
        "live-smoke",
        "--venue",
        "kalshi",
        "--tickers",
        "KXEXAMPLE-26JUN03",
        "--force",
        "--max-events",
        "1",
    ])
    out = capsys.readouterr().out

    assert rc == 1
    assert "fatal error: socket unavailable" in out


def test_live_smoke_returns_nonzero_on_empty_runtime_with_adapter_diagnostics(monkeypatch, capsys):
    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)

    class _EmptyKalshiAdapter:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def events(self):
            if False:
                yield None

        def diagnostics(self):
            return {
                "connect_attempts": 4,
                "connection_error_count": 4,
                "last_connection_error": "401 invalid response",
                "connected_once": False,
            }

    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=_EmptyKalshiAdapter))

    rc = main([
        "live-smoke",
        "--venue",
        "kalshi",
        "--tickers",
        "KXEXAMPLE-26JUN03",
        "--force",
        "--max-events",
        "1",
        "--max-seconds",
        "1",
    ])
    out = capsys.readouterr().out

    assert rc == 1
    assert "no live events were captured" in out
    assert "connection_errors=4" in out
    assert "401 invalid response" in out


def test_live_smoke_persist_raw_runs_pipeline_and_closes_resources(monkeypatch, capsys):
    from pmfi.domain import AlertDecision
    from pmfi.pipeline import runner

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    fake_adapter = _fake_live_adapter_class("kalshi")
    monkeypatch.setitem(sys.modules, "pmfi.adapters.kalshi", SimpleNamespace(KalshiAdapter=fake_adapter))

    pool = _LiveSmokePool([])
    baselines = {"kalshi:KXEXAMPLE-26JUN03": {"sample_size": 10, "p99_trade_usd": Decimal("25")}}
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    async def ensure_current_partitions(pool_arg):
        return None

    async def load_baselines(pool_arg):
        return baselines

    async def load_asset_id_mapping(pool_arg):
        return {}

    created_engines = []

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines
            self.trades = []
            created_engines.append(self)

        def evaluate(self, trade):
            self.trades.append(trade)
            return [
                AlertDecision(
                    emit_alert=True,
                    rule_id="large_trade_absolute_v1",
                    rule_version="alert_rules.v1",
                    severity="medium",
                    confidence="medium",
                    score=Decimal("0.6"),
                    reason_codes=("capital_at_risk_threshold",),
                    evidence={"capital_at_risk_usd": str(trade.capital_at_risk_usd)},
                    data_quality="verified",
                )
            ]

    deliver_stdout = AsyncMock()
    ensure_current_partitions_mock = AsyncMock(side_effect=ensure_current_partitions)
    load_baselines_mock = AsyncMock(side_effect=load_baselines)
    load_asset_id_mapping_mock = AsyncMock(side_effect=load_asset_id_mapping)
    load_suppression_cache = AsyncMock(return_value={})
    insert_raw_event = AsyncMock(return_value=(501, False))
    upsert_market = AsyncMock(return_value="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    insert_trade = AsyncMock(return_value="trade-db-1")
    upsert_metric_window = AsyncMock()
    insert_alert = AsyncMock(return_value="alert-db-1")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setitem(sys.modules, "pmfi.db.migrations", SimpleNamespace(ensure_current_partitions=ensure_current_partitions_mock))
    monkeypatch.setitem(sys.modules, "pmfi.baseline", SimpleNamespace(load_baselines=load_baselines_mock))
    monkeypatch.setitem(sys.modules, "pmfi.markets", SimpleNamespace(load_asset_id_mapping=load_asset_id_mapping_mock))
    monkeypatch.setitem(sys.modules, "pmfi.pipeline.engine", SimpleNamespace(AlertEngine=_FakeAlertEngine))
    monkeypatch.setitem(sys.modules, "pmfi.delivery.stdout", SimpleNamespace(deliver_stdout=deliver_stdout))
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", load_suppression_cache)
    monkeypatch.setattr(runner, "insert_raw_event", insert_raw_event)
    monkeypatch.setattr(runner, "upsert_market", upsert_market)
    monkeypatch.setattr(runner, "insert_trade", insert_trade)
    monkeypatch.setattr(runner, "upsert_metric_window", upsert_metric_window)
    monkeypatch.setattr(runner, "insert_alert", insert_alert)

    rc = main([
        "live-smoke",
        "--venue",
        "kalshi",
        "--tickers",
        "KXEXAMPLE-26JUN03",
        "--force",
        "--max-events",
        "1",
        "--max-seconds",
        "5",
        "--persist-raw",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "done: 1 event(s) processed, 1 captured" in out
    assert "persisted summary:" in out
    assert "raw_events_seen=1" in out
    assert "raw_events_inserted=1" in out
    assert "normalized_trades_inserted=1" in out
    assert "non_trade_skips=0" in out
    assert "alerts_inserted=1" in out
    assert "alerts_delivered=1" in out
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert fake_adapter.instances[0].kwargs["tickers"] == ["KXEXAMPLE-26JUN03"]
    assert fake_adapter.instances[0].disconnected is True
    ensure_current_partitions_mock.assert_awaited_once_with(pool)
    load_baselines_mock.assert_awaited_once_with(pool)
    load_asset_id_mapping_mock.assert_awaited_once_with(pool)
    load_suppression_cache.assert_awaited_once_with(pool.conn, window_seconds=300)
    insert_raw_event.assert_awaited_once()
    upsert_market.assert_awaited_once()
    insert_trade.assert_awaited_once()
    upsert_metric_window.assert_awaited_once()
    insert_alert.assert_awaited_once()
    deliver_stdout.assert_awaited_once()
    assert created_engines[0].baselines == baselines
    assert created_engines[0].trades[0].venue_market_id == "KXEXAMPLE-26JUN03"


def test_live_smoke_persist_raw_reports_non_trade_as_raw_persisted_skip(monkeypatch, tmp_path, capsys):
    from pmfi.pipeline import runner

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    fixture_path = tmp_path / "polymarket_new_market.json"
    fixture_path.write_text(
        json.dumps(
            {
                "venue_code": "polymarket",
                "source_channel": "ws_clob",
                "source_event_type": "new_market",
                "source_event_id": "poly-new-market-1",
                "venue_market_id": "poly-market-1",
                "exchange_ts": None,
                "payload": {"market": "poly-market-1", "event_type": "new_market"},
            }
        ),
        encoding="utf-8",
    )
    pool = _LiveSmokePool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setitem(sys.modules, "pmfi.db.migrations", SimpleNamespace(ensure_current_partitions=AsyncMock()))
    monkeypatch.setitem(sys.modules, "pmfi.baseline", SimpleNamespace(load_baselines=AsyncMock(return_value={})))
    monkeypatch.setitem(sys.modules, "pmfi.markets", SimpleNamespace(load_asset_id_mapping=AsyncMock(return_value={})))
    monkeypatch.setitem(sys.modules, "pmfi.pipeline.engine", SimpleNamespace(AlertEngine=lambda **kwargs: MagicMock()))
    monkeypatch.setitem(sys.modules, "pmfi.delivery.stdout", SimpleNamespace(deliver_stdout=AsyncMock()))
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", AsyncMock(return_value={}))
    monkeypatch.setattr(runner, "insert_raw_event", AsyncMock(return_value=(701, False)))
    monkeypatch.setattr(runner, "upsert_market", AsyncMock())
    monkeypatch.setattr(runner, "insert_trade", AsyncMock())
    monkeypatch.setattr(runner, "upsert_metric_window", AsyncMock())
    monkeypatch.setattr(runner, "insert_alert", AsyncMock())

    rc = main([
        "live-smoke",
        "--fixture-source",
        str(fixture_path),
        "--max-events",
        "1",
        "--max-seconds",
        "5",
        "--persist-raw",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "done: 1 event(s) processed, 1 captured" in out
    assert "raw_events_seen=1 raw_events_inserted=1 raw_event_duplicates=0" in out
    assert "normalized_trades_inserted=0 duplicate_trades=0 non_trade_skips=1" in out
    assert "dead_letters_inserted=0" in out
    assert "alerts_inserted=0 alerts_delivered=0 alerts_suppressed=0" in out
    runner.upsert_market.assert_not_awaited()
    runner.insert_trade.assert_not_awaited()
    runner.insert_alert.assert_not_awaited()


def test_live_smoke_fixture_source_persist_raw_reports_trade_and_alert_counts(monkeypatch, capsys):
    from pmfi.domain import AlertDecision
    from pmfi.pipeline import runner

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    monkeypatch.setattr("pmfi.config.load_config", _live_smoke_config)
    pool = _LiveSmokePool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    class _FakeAlertEngine:
        def __init__(self, *, baselines=None):
            self.baselines = baselines

        def evaluate(self, trade):
            return [
                AlertDecision(
                    emit_alert=True,
                    rule_id="large_trade_absolute_v1",
                    rule_version="alert_rules.v1",
                    severity="medium",
                    confidence="medium",
                    score=Decimal("0.6"),
                    reason_codes=("capital_at_risk_threshold",),
                    evidence={"capital_at_risk_usd": str(trade.capital_at_risk_usd)},
                    data_quality="verified",
                )
            ]

    deliver_stdout = AsyncMock()
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setitem(sys.modules, "pmfi.db.migrations", SimpleNamespace(ensure_current_partitions=AsyncMock()))
    monkeypatch.setitem(sys.modules, "pmfi.baseline", SimpleNamespace(load_baselines=AsyncMock(return_value={})))
    monkeypatch.setitem(sys.modules, "pmfi.markets", SimpleNamespace(load_asset_id_mapping=AsyncMock(return_value={})))
    monkeypatch.setitem(sys.modules, "pmfi.pipeline.engine", SimpleNamespace(AlertEngine=_FakeAlertEngine))
    monkeypatch.setitem(sys.modules, "pmfi.delivery.stdout", SimpleNamespace(deliver_stdout=deliver_stdout))
    monkeypatch.setattr("pmfi.db.repos.alerts.load_suppression_cache", AsyncMock(return_value={}))
    monkeypatch.setattr(runner, "insert_raw_event", AsyncMock(return_value=(801, False)))
    monkeypatch.setattr(runner, "upsert_market", AsyncMock(return_value="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"))
    monkeypatch.setattr(runner, "insert_trade", AsyncMock(return_value="trade-db-1"))
    monkeypatch.setattr(runner, "upsert_metric_window", AsyncMock())
    monkeypatch.setattr(runner, "insert_alert", AsyncMock(return_value="alert-db-1"))

    rc = main([
        "live-smoke",
        "--fixture-source",
        "tests/fixtures/live-smoke/kalshi_persist.json",
        "--max-events",
        "1",
        "--max-seconds",
        "5",
        "--persist-raw",
    ])
    out = capsys.readouterr().out

    assert rc == 0
    assert "done: 1 event(s) processed, 1 captured" in out
    assert "raw_events_seen=1 raw_events_inserted=1 raw_event_duplicates=0" in out
    assert "normalized_trades_inserted=1 duplicate_trades=0 non_trade_skips=0" in out
    assert "dead_letters_inserted=0" in out
    assert "alerts_inserted=1 alerts_delivered=1 alerts_suppressed=0" in out
    runner.insert_trade.assert_awaited_once()
    runner.insert_alert.assert_awaited_once()
    deliver_stdout.assert_awaited_once()


def test_alerts_list_accepts_format_json():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["alerts", "list", "--format", "json"])
    assert args.format == "json"


def test_alerts_list_json_db_failure_is_machine_readable(monkeypatch, capsys):
    async def create_pool(*_args, **_kwargs):
        raise RuntimeError("db offline for test")

    asyncpg_mod = ModuleType("asyncpg")
    asyncpg_mod.create_pool = create_pool

    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_mod)
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))

    rc = main(["alerts", "list", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert "db offline for test" in payload["error"]
    assert any("db_local.py up" in action for action in payload["next_actions"])
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "DB query failed" not in captured.out


def test_markets_list_accepts_venue_and_json_format():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "list", "--venue", "polymarket", "--format", "json", "--limit", "5"])

    assert args.markets_cmd == "list"
    assert args.venue == "polymarket"
    assert args.format == "json"
    assert args.limit == 5


def test_markets_list_json_exposes_watch_ids_and_token_counts(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))

    class _MarketListPool:
        def __init__(self):
            self.sql = []
            self.params = []
            self.closed = False

        async def fetch(self, sql, *params):
            self.sql.append(sql)
            self.params.append(params)
            return [
                {
                    "venue_code": "polymarket",
                    "venue_market_id": "0xmarket",
                    "title": "Will this market be watchable?",
                    "status": "active",
                    "watched": False,
                    "trade_count": 0,
                    "active_outcomes": 2,
                    "last_trade_at": datetime(2026, 6, 17, 3, 0, tzinfo=timezone.utc),
                }
            ]

        async def close(self):
            self.closed = True

    pool = _MarketListPool()

    async def create_pool(_url):
        return pool

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["markets", "list", "--venue", "polymarket", "--format", "json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == 0
    assert payload["count"] == 1
    assert payload["markets"][0]["venue_market_id"] == "0xmarket"
    assert payload["markets"][0]["active_outcomes"] == 2
    assert payload["markets"][0]["last_trade_at"] == "2026-06-17T03:00:00+00:00"
    assert pool.closed is True
    assert "market_outcomes" in pool.sql[0]
    assert "m.venue_code=$1" in pool.sql[0]
    assert pool.params == [("polymarket", 20)]


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


def test_watch_db_connect_failure_returns_rc1_without_traceback(monkeypatch, capsys):
    class FakeConsole:
        def print(self, *parts, **_kwargs):
            print(*parts)

    async def create_pool(*_args, **_kwargs):
        raise RuntimeError("db offline for test")

    rich_mod = ModuleType("rich")
    console_mod = ModuleType("rich.console")
    console_mod.Console = FakeConsole
    console_mod.Group = lambda *items: items
    table_mod = ModuleType("rich.table")
    table_mod.Table = object
    panel_mod = ModuleType("rich.panel")
    panel_mod.Panel = object
    live_mod = ModuleType("rich.live")
    live_mod.Live = object
    layout_mod = ModuleType("rich.layout")
    layout_mod.Layout = object
    asyncpg_mod = ModuleType("asyncpg")
    asyncpg_mod.create_pool = create_pool

    monkeypatch.setitem(sys.modules, "rich", rich_mod)
    monkeypatch.setitem(sys.modules, "rich.console", console_mod)
    monkeypatch.setitem(sys.modules, "rich.table", table_mod)
    monkeypatch.setitem(sys.modules, "rich.panel", panel_mod)
    monkeypatch.setitem(sys.modules, "rich.live", live_mod)
    monkeypatch.setitem(sys.modules, "rich.layout", layout_mod)
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_mod)
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))

    rc = main(["watch", "--limit", "1"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[watch] DB unavailable: db offline for test" in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


def test_status_runs_without_db(capsys):
    """pmfi status must exit 0 even when DB is unreachable."""
    rc = main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    # Either rich panel or plain text output; DB error is expected without running DB.
    assert len(out) > 0  # something was printed


def test_status_parser_accepts_json_format():
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["status", "--format", "json"])

    assert args.command == "status"
    assert args.format == "json"


def test_status_json_runs_without_db_and_hides_credentials(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["status", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["ok"] is True
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "secret" not in captured.out
    assert "pmfi:secret" not in captured.out
    assert payload["database"]["target"] == "localhost:5433/pmfi"
    assert "error" in payload["database"]["status"].lower() or "offline" in payload["database"]["status"].lower()
    assert payload["database"]["stats"] == {}
    assert payload["features"] == {
        "enable_cross_venue_matching": False,
        "enable_kalshi_live": False,
        "enable_ml_scoring": False,
        "enable_orderbook_reconstruction": False,
        "enable_polymarket_live": False,
        "enable_wallet_intelligence": False,
    }
    assert payload["unsupported_enabled_features"] == []
    assert isinstance(payload["fixtures"]["count"], int)


def test_status_json_lists_unsupported_enabled_features(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _health_config_with_unsupported_features)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["status", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["features"]["enable_wallet_intelligence"] is True
    assert payload["features"]["enable_ml_scoring"] is True
    assert payload["unsupported_enabled_features"] == [
        "enable_wallet_intelligence",
        "enable_ml_scoring",
    ]


def test_status_json_includes_db_stats_and_closes_pool(monkeypatch, capsys):
    class StatusPool:
        def __init__(self):
            self.closed = False
            self.values = {
                "SELECT COUNT(*) FROM markets": 3,
                "SELECT COUNT(*) FROM raw_events": 11,
                "SELECT COUNT(*) FROM alerts": 7,
                "SELECT COUNT(*) FROM market_baselines": 5,
                "SELECT MAX(fired_at) FROM alerts": datetime(2026, 6, 17, 12, 30, tzinfo=timezone.utc),
                "SELECT COUNT(*) FROM normalized_trades": 13,
                "SELECT COUNT(*) FROM dead_letters": 2,
                "SELECT COUNT(*) FROM market_outcomes": 17,
                "SELECT MAX(received_at) FROM normalized_trades": datetime(2026, 6, 17, 12, 45, tzinfo=timezone.utc),
            }

        async def fetchval(self, sql):
            return self.values[sql]

    pool = StatusPool()
    calls = {"close_pool": 0}

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        pool_arg.closed = True

    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["status", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["database"]["status"] == "ok"
    assert payload["database"]["stats"] == {
        "markets": 3,
        "raw_events": 11,
        "alerts": 7,
        "baselines": 5,
        "last_alert": "2026-06-17T12:30:00+00:00",
        "normalized_trades": 13,
        "dead_letters": 2,
        "market_outcomes": 17,
        "last_trade": "2026-06-17T12:45:00+00:00",
    }
    assert calls["close_pool"] == 1
    assert pool.closed is True


def _health_config():
    alerts = SimpleNamespace(default_delivery="console", allowed_delivery_modes=["console", "file"])
    features = SimpleNamespace(
        enable_polymarket_live=False,
        enable_kalshi_live=False,
        enable_orderbook_reconstruction=False,
        enable_cross_venue_matching=False,
        enable_wallet_intelligence=False,
        enable_ml_scoring=False,
    )
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(alerts=alerts, features=features, database=database, live_mode_enabled=False)


def _health_config_with_unsupported_features():
    cfg = _health_config()
    cfg.features.enable_wallet_intelligence = True
    cfg.features.enable_ml_scoring = True
    return cfg


class _FakeHealthPool:
    def __init__(
        self,
        *,
        watched=1,
        missing_poly=0,
        poly_token_count=2,
        connections=None,
        heartbeats=None,
        incidents=None,
    ):
        self.watched = watched
        self.missing_poly = missing_poly
        self.poly_token_count = poly_token_count
        self.connections = connections or []
        self.heartbeats = heartbeats or []
        self.incidents = incidents or []
        self.closed = False
        self.sql: list[str] = []

    async def fetchval(self, sql):
        self.sql.append(sql)
        normalized = " ".join(sql.split()).lower()
        if normalized == "select count(*) from markets":
            return max(self.watched, 1)
        if normalized == "select count(*) from markets where watched=true":
            return self.watched
        if "from markets where watched=true and venue_code='polymarket'" in normalized:
            return 1 if self.watched else 0
        if "from markets where watched=true and venue_code='kalshi'" in normalized:
            return 0
        if normalized == "select count(*) from raw_events":
            return 0
        if normalized == "select count(*) from normalized_trades":
            return 0
        if normalized == "select count(*) from dead_letters":
            return 0
        if normalized == "select count(*) from v_open_data_quality_incidents":
            return len(self.incidents)
        if normalized == "select count(*) from alerts":
            return 0
        if normalized == "select count(*) from market_baselines":
            return 0
        if normalized == "select count(*) from market_outcomes":
            return 2 if self.watched and not self.missing_poly else 0
        if normalized == "select max(received_at) from raw_events":
            return None
        if normalized == "select max(received_at) from normalized_trades":
            return None
        if normalized == "select max(fired_at) from alerts":
            return None
        if normalized == "select max(computed_at) from market_baselines":
            return None
        if normalized == "select count(*) from ingestion_connections":
            return len(self.connections)
        if normalized == "select count(*) from system_heartbeats":
            return len(self.heartbeats)
        if "having count(distinct mo.venue_outcome_id) < 2" in normalized and "select count(*)" in normalized:
            return self.missing_poly
        raise AssertionError(f"unexpected health SQL: {sql}")

    async def fetch(self, sql):
        self.sql.append(sql)
        normalized = " ".join(sql.split()).lower()
        if "having count(distinct mo.venue_outcome_id) < 2" in normalized and "limit 5" in normalized:
            if self.missing_poly:
                return [{"venue_market_id": "poly-1", "title": "Incomplete mapping", "token_count": self.poly_token_count}]
            return []
        if "from ingestion_connections" in normalized:
            return self.connections
        if "from system_heartbeats" in normalized:
            return self.heartbeats
        if "from v_open_data_quality_incidents" in normalized:
            return self.incidents[:5]
        raise AssertionError(f"unexpected health fetch SQL: {sql}")

    async def close(self):
        self.closed = True


def _patch_health_integrity(monkeypatch, *, ok=True):
    from pmfi.db.verify import IntegrityCheck, IntegrityResult

    check = IntegrityCheck(
        "relations",
        "pass" if ok else "fail",
        "relations present" if ok else "missing required tables/views",
        {} if ok else {"missing": ["raw_events"]},
    )

    async def verify_database_integrity(pool_arg):
        return IntegrityResult(ok=ok, status="ready" if ok else "blocked", checks=[check])

    monkeypatch.setattr("pmfi.db.verify.verify_database_integrity", verify_database_integrity)


def test_health_parser_accepts_json_format():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["health", "--format", "json"])
    assert args.command == "health"
    assert args.format == "json"


def test_health_json_returns_nonzero_when_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)
    rc = main(["health", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert any(check["name"] == "db" and check["status"] == "fail" for check in payload["checks"])
    assert any(check["name"] == "config" for check in payload["checks"])
    assert any(check["name"] == "live" for check in payload["checks"])
    assert any(check["name"] == "delivery" for check in payload["checks"])
    ingest_runtime = next(check for check in payload["checks"] if check["name"] == "ingest_runtime")
    assert ingest_runtime["status"] == "warn"
    assert "DB is unavailable" in ingest_runtime["message"]
    incidents = next(check for check in payload["checks"] if check["name"] == "data_quality_incidents")
    assert incidents["status"] == "warn"
    assert "DB is unavailable" in incidents["message"]
    assert "postgres is down" in captured.out


def test_health_json_fails_when_unsupported_feature_flags_enabled(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config_with_unsupported_features)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    feature_support = next(check for check in payload["checks"] if check["name"] == "feature_support")
    assert feature_support["status"] == "fail"
    assert feature_support["details"]["unsupported_enabled_features"] == [
        "enable_wallet_intelligence",
        "enable_ml_scoring",
    ]
    assert any("enable_wallet_intelligence" in action for action in payload["next_actions"])
    assert any("enable_ml_scoring" in action for action in payload["next_actions"])


def test_health_json_fails_closed_when_db_verify_import_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    calls = {"create_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        raise AssertionError("health should not create a pool after DB verify import fails")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    real_import = builtins.__import__

    def fail_verify_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pmfi.db" and "verify" in (fromlist or ()):
            raise RuntimeError("db verify import failed")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_verify_import)
    rc = main(["health", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert calls == {"create_pool": 0}
    assert "Traceback" not in captured.err
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    db_check = next(check for check in payload["checks"] if check["name"] == "db")
    assert db_check["status"] == "fail"
    assert "db verify import failed" in db_check["message"]
    for name in {
        "watched_markets",
        "polymarket_token_mappings",
        "kalshi_tickers",
        "raw_events",
        "normalized_trades",
        "dead_letters",
        "data_quality_incidents",
        "alerts",
        "market_baselines",
        "ingest_runtime",
    }:
        check = next(check for check in payload["checks"] if check["name"] == name)
        assert check["status"] == "warn"
        assert "DB is unavailable" in check["message"]
    assert any("python scripts\\db_local.py up" in action for action in payload["next_actions"])
    assert any("python scripts\\db_local.py verify" in action for action in payload["next_actions"])


def test_health_reports_zero_data_quality_incidents_as_pass(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(watched=1, incidents=[])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    incidents = next(check for check in payload["checks"] if check["name"] == "data_quality_incidents")
    assert incidents["status"] == "pass"
    assert incidents["message"] == "no open data-quality incidents"
    assert incidents["details"]["count"] == 0
    assert incidents["details"]["examples"] == []


def test_health_reports_open_data_quality_incidents_as_warning(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(
        watched=1,
        incidents=[
            {
                "incident_id": "11111111-1111-1111-1111-111111111111",
                "venue_code": "kalshi",
                "market_id": "22222222-2222-2222-2222-222222222222",
                "incident_type": "missing_trade_side",
                "severity": "medium",
                "started_at": datetime(2026, 6, 16, 0, 3, tzinfo=timezone.utc),
                "summary": "trade side missing from source payload",
            }
        ],
    )

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    incidents = next(check for check in payload["checks"] if check["name"] == "data_quality_incidents")
    assert incidents["status"] == "warn"
    assert "1 open data-quality incident" in incidents["message"]
    assert incidents["details"]["count"] == 1
    assert incidents["details"]["examples"][0]["incident_type"] == "missing_trade_side"
    assert incidents["details"]["examples"][0]["summary"] == "trade side missing from source payload"


def test_health_parser_and_table_output_include_ingest_runtime(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(
        watched=1,
        connections=[
            {
                "connection_id": "11111111-1111-1111-1111-111111111111",
                "venue_code": "kalshi",
                "source_channel": "websocket",
                "status": "connected",
                "connected_at": datetime(2026, 6, 16, tzinfo=timezone.utc),
                "last_message_at": datetime(2026, 6, 16, 0, 1, tzinfo=timezone.utc),
                "disconnected_at": None,
                "last_error": None,
                "reconnect_count": 0,
                "updated_at": datetime(2026, 6, 16, 0, 1, tzinfo=timezone.utc),
            }
        ],
        heartbeats=[
            {
                "worker_name": "pmfi-ingest:kalshi:websocket",
                "worker_type": "ingest",
                "status": "healthy",
                "last_heartbeat_at": datetime(2026, 6, 16, 0, 2, tzinfo=timezone.utc),
            }
        ],
    )

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ingest_runtime" in out
    assert "ingest runtime state recorded" in out


def test_health_table_returns_useful_text_when_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)
    rc = main(["health"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "PMFI Health" in out
    assert "DB connection or schema query failed" in out
    assert "python scripts\\db_local.py up" in out


def test_health_fake_pool_ready_with_warnings_is_validate_only(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(watched=1, missing_poly=0)
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters"):
            raise AssertionError(f"health imported live adapter {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr(builtins, "__import__", guard_import)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["status"] == "ready_with_warnings"
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)
    assert any(check["name"] == "db_integrity" and check["status"] == "pass" for check in payload["checks"])
    assert any(check["name"] == "raw_events" and check["status"] == "warn" for check in payload["checks"])
    ingest_runtime = next(check for check in payload["checks"] if check["name"] == "ingest_runtime")
    assert ingest_runtime["status"] == "warn"
    assert ingest_runtime["details"]["connection_count"] == 0
    assert ingest_runtime["details"]["heartbeat_count"] == 0
    assert "no ingest runtime state recorded yet" in ingest_runtime["message"]


def test_health_ingest_runtime_error_row_warns_without_failing(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(
        watched=1,
        connections=[
            {
                "connection_id": "22222222-2222-2222-2222-222222222222",
                "venue_code": "polymarket",
                "source_channel": "websocket",
                "status": "error",
                "connected_at": datetime(2026, 6, 16, tzinfo=timezone.utc),
                "last_message_at": None,
                "disconnected_at": datetime(2026, 6, 16, 0, 5, tzinfo=timezone.utc),
                "last_error": "socket closed",
                "reconnect_count": 2,
                "updated_at": datetime(2026, 6, 16, 0, 5, tzinfo=timezone.utc),
            }
        ],
        heartbeats=[
            {
                "worker_name": "pmfi-ingest:polymarket:websocket",
                "worker_type": "ingest",
                "status": "healthy",
                "last_heartbeat_at": datetime(2026, 6, 16, 0, 4, tzinfo=timezone.utc),
            }
        ],
    )

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    ingest_runtime = next(check for check in payload["checks"] if check["name"] == "ingest_runtime")
    assert ingest_runtime["status"] == "warn"
    assert "latest ingest runtime status is error" in ingest_runtime["message"]
    assert ingest_runtime["details"]["latest_connection"]["last_error"] == "socket closed"
    assert ingest_runtime["details"]["latest_connection"]["status"] == "error"


def test_health_fake_pool_blocks_without_watched_markets(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(watched=0, missing_poly=0)

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert any(check["name"] == "watched_markets" and check["status"] == "fail" for check in payload["checks"])


def test_health_fake_pool_blocks_incomplete_polymarket_token_mappings(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch)
    pool = _FakeHealthPool(watched=1, missing_poly=1, poly_token_count=1)

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert any(
        check["name"] == "polymarket_token_mappings" and check["status"] == "fail"
        for check in payload["checks"]
    )
    mapping_check = next(check for check in payload["checks"] if check["name"] == "polymarket_token_mappings")
    assert "fewer than two" in mapping_check["message"]
    assert mapping_check["details"]["examples"][0]["token_count"] == 1


def test_health_blocks_on_db_integrity_failure(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch, ok=False)
    pool = _FakeHealthPool(watched=1, missing_poly=0)

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    integrity = next(check for check in payload["checks"] if check["name"] == "db_integrity")
    assert integrity["status"] == "fail"
    assert integrity["details"]["failed_checks"][0]["name"] == "relations"
    incidents = next(check for check in payload["checks"] if check["name"] == "data_quality_incidents")
    assert incidents["status"] == "warn"
    assert "DB integrity failed" in incidents["message"]
    assert any("pmfi db-verify --format json" in action for action in payload["next_actions"])


def test_health_reports_integrity_failure_before_stats_queries(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _health_config)
    _patch_health_integrity(monkeypatch, ok=False)

    class StatsShouldNotRunPool:
        closed = False

        async def fetchval(self, sql):
            raise AssertionError(f"stats query should not run before integrity passes: {sql}")

        async def fetch(self, sql):
            raise AssertionError(f"stats query should not run before integrity passes: {sql}")

        async def close(self):
            self.closed = True

    pool = StatsShouldNotRunPool()

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    rc = main(["health", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert pool.closed is True
    assert any(check["name"] == "db" for check in payload["checks"])
    integrity = next(check for check in payload["checks"] if check["name"] == "db_integrity")
    assert integrity["status"] == "fail"
    assert all(
        check["status"] == "warn"
        for check in payload["checks"]
        if check["name"] in {
            "watched_markets",
            "raw_events",
            "normalized_trades",
            "data_quality_incidents",
            "alerts",
            "ingest_runtime",
        }
    )


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


def test_baseline_compute_cli_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["baseline", "compute", "--lookback-days", "1", "--min-samples", "2"])
    assert args.baseline_cmd == "compute"
    assert args.lookback_days == 1
    assert args.min_samples == 2


def test_report_cli_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["report", "--since", "7d", "--format", "json"])
    assert args.since == "7d"
    assert args.format == "json"
    assert args.source == "db"
    assert args.fixture_dir is None
    assert args.output_dir is None


def test_report_fixture_cli_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "report",
        "--source",
        "fixtures",
        "--fixture-dir",
        "fixtures",
        "--output-dir",
        "out",
        "--format",
        "json",
    ])
    assert args.source == "fixtures"
    assert args.fixture_dir == "fixtures"
    assert args.output_dir == "out"
    assert args.format == "json"


def test_report_cli_default_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["report"])
    assert args.since == "24h"
    assert args.format == "table"
    assert args.source == "db"
    assert args.fixture_dir is None
    assert args.output_dir is None


def test_report_json_db_unavailable_returns_parseable_operator_failure(monkeypatch, capsys):
    async def create_pool(_url):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["report", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["source"] == "db"
    assert "db offline for test" in payload["error"]
    assert any(
        "local Postgres" in action or "db_local" in action
        for action in payload["next_actions"]
    )
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
    assert "[report] DB unavailable" not in captured.out


def _copy_report_fixtures(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parent / "fixtures" / "raw"
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    for name in ["kalshi_trade.json", "polymarket_last_trade_price.json"]:
        shutil.copyfile(src / name, fixture_dir / name)
    return fixture_dir


def test_report_fixture_json_output_without_db(tmp_path, monkeypatch, capsys):
    fixture_dir = _copy_report_fixtures(tmp_path)
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name == "pmfi.config" or name.startswith("pmfi.db"):
            raise AssertionError(f"fixture report imported DB/config module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard_import)
    rc = main([
        "report",
        "--source",
        "fixtures",
        "--fixture-dir",
        str(fixture_dir),
        "--format",
        "json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["source"] == "fixtures"
    assert payload["fixture_count"] == 2
    assert payload["trade_count"] == 2
    assert payload["alert_count"] == 4
    assert payload["alerts_by_rule"] == {
        "large_trade_absolute_v1": 2,
        "market_relative_large_trade_v1": 2,
    }


def test_report_fixture_table_writes_report_file(tmp_path, capsys):
    fixture_dir = _copy_report_fixtures(tmp_path)
    output_dir = tmp_path / "out"

    rc = main([
        "report",
        "--source",
        "fixtures",
        "--fixture-dir",
        str(fixture_dir),
        "--output-dir",
        str(output_dir),
        "--format",
        "table",
    ])
    captured = capsys.readouterr()
    reports = list(output_dir.glob("*-fixture-report.txt"))

    assert rc == 0
    assert len(reports) == 1
    assert "[report] wrote fixture report:" in captured.out
    assert "fixtures=2 trades=2 alerts=4" in captured.out
    content = reports[0].read_text(encoding="utf-8")
    assert "# PMFI Fixture Report" in content
    assert "Fixtures processed : 2" in content
    assert "large_trade_absolute_v1" in content


def test_report_fixture_source_rejects_missing_fixture_dir(tmp_path, capsys):
    rc = main([
        "report",
        "--source",
        "fixtures",
        "--fixture-dir",
        str(tmp_path / "missing"),
        "--format",
        "json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert "fixture directory not found" in payload["error"]


def test_report_fixture_source_rejects_empty_runtime(tmp_path, capsys):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()

    rc = main([
        "report",
        "--source",
        "fixtures",
        "--fixture-dir",
        str(fixture_dir),
        "--format",
        "json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert "produced no normalized trades" in payload["error"]


def test_live_cli_args():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["live", "--venue", "polymarket", "--markets", "mkt1,mkt2", "--orderbook", "--refresh-map-minutes", "15"])
    assert args.venue == "polymarket"
    assert args.markets == "mkt1,mkt2"
    assert args.orderbook is True
    assert args.refresh_map_minutes == 15


def test_live_cli_defaults():
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["live"])
    assert args.venue == "polymarket"
    assert args.markets is None
    assert args.orderbook is False
    assert args.refresh_map_minutes == 30


def test_live_reports_create_pool_failure_without_live_imports(monkeypatch, capsys):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    guarded_imports = {
        "pmfi.adapters.polymarket",
        "pmfi.pipeline.engine",
        "pmfi.pipeline.runner",
        "pmfi.markets",
    }
    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name in guarded_imports:
            raise AssertionError(f"live imported {name} before DB pool was available")
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv("PMFI_ENABLE_LIVE", "1")
    monkeypatch.setattr("pmfi.config.load_config", lambda: SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db")))
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["live", "--markets", "dummy", "--venue", "polymarket"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "[live] DB unavailable: db offline for test" in captured.out
    assert "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first." in captured.out
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err
