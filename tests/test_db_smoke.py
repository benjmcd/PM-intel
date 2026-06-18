from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_db_smoke():
    path = ROOT / "scripts" / "db_smoke.py"
    spec = importlib.util.spec_from_file_location("db_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_make_db_name_is_disposable_safe(monkeypatch):
    db_smoke = _load_db_smoke()
    monkeypatch.setattr(db_smoke.secrets, "token_hex", lambda _n: "abc123")

    name = db_smoke.make_db_name(datetime(2026, 6, 17, 1, 2, 3, tzinfo=timezone.utc))

    assert name == "pmfi_smoke_20260617_010203_abc123"
    db_smoke.validate_smoke_db_name(name)


def test_database_url_for_rewrites_only_database_name():
    db_smoke = _load_db_smoke()

    url = db_smoke.database_url_for(
        "postgresql://pmfi:secret@localhost:5433/pmfi?sslmode=disable",
        "pmfi_smoke_20260617_010203_abc123",
    )

    assert url == "postgresql://pmfi:secret@localhost:5433/pmfi_smoke_20260617_010203_abc123?sslmode=disable"


def test_database_url_for_rejects_unsafe_database_name():
    db_smoke = _load_db_smoke()

    with pytest.raises(ValueError):
        db_smoke.database_url_for("postgresql://pmfi:secret@localhost:5433/pmfi", "pmfi;drop")


def test_report_assertion_requires_clean_disposable_counts():
    db_smoke = _load_db_smoke()

    db_smoke._assert_report(
        {
            "total": 12,
            "raw_events": 11,
            "normalized_trades": 10,
            "dead_letters": 1,
        }
    )
    with pytest.raises(RuntimeError):
        db_smoke._assert_report(
            {
                "total": 12,
                "raw_events": 11,
                "normalized_trades": 16,
                "dead_letters": 1,
            }
        )


def test_ingest_check_assertion_requires_ready_local_prerequisites():
    db_smoke = _load_db_smoke()

    ready = {
        "ok": True,
        "status": "ready",
        "checks": [
            {"name": "db_integrity", "status": "pass"},
            {"name": "delivery", "status": "pass"},
            {"name": "baselines", "status": "pass"},
            {"name": "watched_markets", "status": "pass"},
            {"name": "kalshi_subscriptions", "status": "pass"},
            {"name": "live_connections", "status": "pass"},
        ],
        "subscriptions": {"kalshi_tickers": 1},
    }

    db_smoke._assert_ingest_check(ready)
    with pytest.raises(RuntimeError):
        db_smoke._assert_ingest_check({**ready, "status": "blocked"})
    with pytest.raises(RuntimeError):
        db_smoke._assert_ingest_check({**ready, "subscriptions": {"kalshi_tickers": 0}})


def test_resume_counts_require_stable_operator_rows_and_duplicate_observations():
    db_smoke = _load_db_smoke()

    before = {
        "raw_events": 11,
        "normalized_trades": 10,
        "alerts": 12,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 11,
        "raw_duplicate_count": 0,
    }
    after = {
        **before,
        "raw_duplicate_count": 11,
    }

    db_smoke._assert_resume_counts(before, after)


def test_resume_counts_reject_added_alerts_or_missing_duplicate_count():
    db_smoke = _load_db_smoke()

    before = {
        "raw_events": 11,
        "normalized_trades": 10,
        "alerts": 12,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 11,
        "raw_duplicate_count": 0,
    }
    with pytest.raises(RuntimeError):
        db_smoke._assert_resume_counts(before, {**before, "alerts": 13, "raw_duplicate_count": 11})
    with pytest.raises(RuntimeError):
        db_smoke._assert_resume_counts(before, {**before, "raw_duplicate_count": 10})


def test_live_smoke_counts_require_raw_trade_dedupe_and_alert_progress():
    db_smoke = _load_db_smoke()

    before = {
        "raw_events": 11,
        "normalized_trades": 10,
        "alerts": 12,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 11,
        "raw_duplicate_count": 11,
    }
    after = {
        **before,
        "raw_events": 12,
        "normalized_trades": 11,
        "alerts": 14,
        "event_dedupe_keys": 12,
    }

    db_smoke._assert_live_smoke_counts(before, after)
    with pytest.raises(RuntimeError):
        db_smoke._assert_live_smoke_counts(before, {**after, "normalized_trades": 10})
    with pytest.raises(RuntimeError):
        db_smoke._assert_live_smoke_counts(before, {**after, "alerts": 12})
    with pytest.raises(RuntimeError):
        db_smoke._assert_live_smoke_counts(before, {**after, "dead_letters": 2})


def test_fixture_ingest_counts_require_duplicate_observation_after_live_smoke():
    db_smoke = _load_db_smoke()

    before = {
        "raw_events": 12,
        "normalized_trades": 11,
        "alerts": 14,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 12,
        "raw_duplicate_count": 11,
    }
    after = {
        **before,
        "raw_duplicate_count": 12,
    }

    db_smoke._assert_fixture_ingest_counts(before, after)
    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_counts(before, {**after, "normalized_trades": 12})
    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_counts(before, {**before, "raw_duplicate_count": 11})


def test_fixture_ingest_runtime_health_requires_stopped_fixture_worker():
    db_smoke = _load_db_smoke()

    payload = {
        "ok": True,
        "checks": [
            {
                "name": "ingest_runtime",
                "status": "pass",
                "details": {
                    "connection_count": 1,
                    "heartbeat_count": 1,
                    "latest_connection": {
                        "source_channel": "fixture_source",
                        "status": "stopped",
                        "last_error": None,
                    },
                    "latest_heartbeat": {
                        "worker_name": "pmfi-ingest:kalshi:fixture_source",
                        "status": "stopped",
                    },
                },
            }
        ],
    }

    db_smoke._assert_fixture_ingest_runtime_health(payload)


def test_fixture_ingest_runtime_health_rejects_missing_or_error_state():
    db_smoke = _load_db_smoke()

    good_details = {
        "connection_count": 1,
        "heartbeat_count": 1,
        "latest_connection": {
            "source_channel": "fixture_source",
            "status": "stopped",
            "last_error": None,
        },
        "latest_heartbeat": {
            "worker_name": "pmfi-ingest:kalshi:fixture_source",
            "status": "stopped",
        },
    }
    base_payload = {
        "ok": True,
        "checks": [{"name": "ingest_runtime", "status": "pass", "details": good_details}],
    }

    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_runtime_health({"ok": False, "checks": []})
    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_runtime_health({"ok": True, "checks": []})
    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_runtime_health(
            {
                **base_payload,
                "checks": [{"name": "ingest_runtime", "status": "warn", "details": good_details}],
            }
        )
    with pytest.raises(RuntimeError):
        db_smoke._assert_fixture_ingest_runtime_health(
            {
                **base_payload,
                "checks": [
                    {
                        "name": "ingest_runtime",
                        "status": "pass",
                        "details": {
                            **good_details,
                            "latest_connection": {
                                "source_channel": "fixture_source",
                                "status": "error",
                                "last_error": "socket closed",
                            },
                        },
                    }
                ],
            }
        )


def test_live_smoke_replay_requires_fake_live_event_evidence_and_stable_counts():
    db_smoke = _load_db_smoke()

    counts = {
        "raw_events": 12,
        "normalized_trades": 11,
        "alerts": 14,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 12,
        "raw_duplicate_count": 11,
    }
    result = db_smoke.CommandResult(
        args=["python", "-m", "pmfi.cli", "replay", "--from-db"],
        stdout=(
            '{"alert": true, "rule_id": "large_trade_absolute_v1", "severity": "medium", '
            '"confidence": "medium", "score": "0.6", "venue_code": "kalshi", '
            '"market_id": "KXLIVE-SMOKE-26JUN03", '
            '"reason_codes": ["capital_at_risk_threshold"], '
            '"evidence": {"venue_code": "kalshi", "venue_market_id": "KXLIVE-SMOKE-26JUN03", '
            '"capital_at_risk_usd": "33300.00"}}\n'
            "[from-db] replayed 11 raw_event(s) from Postgres\n"
        ),
        stderr="",
    )

    db_smoke._assert_live_smoke_replay(result, counts, counts, expected_replayed=11)


def test_live_smoke_replay_rejects_missing_evidence_or_mutated_counts():
    db_smoke = _load_db_smoke()

    counts = {
        "raw_events": 12,
        "normalized_trades": 11,
        "alerts": 14,
        "dead_letters": 1,
        "market_baselines": 3,
        "event_dedupe_keys": 12,
        "raw_duplicate_count": 11,
    }
    missing_evidence = db_smoke.CommandResult(
        args=["python", "-m", "pmfi.cli", "replay", "--from-db"],
        stdout="[from-db] replayed 11 raw_event(s) from Postgres\n",
        stderr="",
    )

    with pytest.raises(RuntimeError):
        db_smoke._assert_live_smoke_replay(missing_evidence, counts, counts, expected_replayed=11)
    with pytest.raises(RuntimeError):
        db_smoke._assert_live_smoke_replay(
            missing_evidence,
            counts,
            {**counts, "alerts": 15},
            expected_replayed=11,
        )


def test_main_reports_unreachable_local_postgres_without_traceback(monkeypatch, capsys):
    db_smoke = _load_db_smoke()

    async def fail_smoke(*, keep_db=False):
        raise ConnectionRefusedError(1225, "The remote computer refused the network connection")

    monkeypatch.setattr(db_smoke, "run_smoke", fail_smoke)

    assert db_smoke.main([]) != 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Traceback" not in output
    assert "local Postgres is not reachable" in captured.err
    assert "scripts\\db_local.py up" in captured.err
    assert "scripts\\db_local.py verify" in captured.err


def test_main_reports_missing_asyncpg_without_traceback(monkeypatch, capsys):
    db_smoke = _load_db_smoke()

    async def fail_smoke(*, keep_db=False):
        raise ModuleNotFoundError("No module named 'asyncpg'")

    monkeypatch.setattr(db_smoke, "run_smoke", fail_smoke)

    assert db_smoke.main([]) != 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "Traceback" not in output
    assert "asyncpg is not installed" in captured.err
    assert 'python.exe -m pip install -e ".[dev]"' in captured.err


def test_task_wrapper_routes_db_smoke(monkeypatch):
    import scripts.task as task

    calls = []

    def python_script(script, *args):
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", python_script)

    assert task.main(["db-smoke"]) == 0
    assert calls == [("scripts/db_smoke.py", ())]
