from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_operator_smoke():
    path = ROOT / "scripts" / "operator_smoke.py"
    spec = importlib.util.spec_from_file_location("operator_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture_report_payload(**overrides):
    payload = {
        "ok": True,
        "source": "fixtures",
        "fixture_count": 10,
        "trade_count": 10,
        "alert_count": 14,
        "alerts_by_rule": {
            "directional_cluster_v1": 1,
            "large_trade_absolute_v1": 4,
            "market_relative_large_trade_v1": 8,
            "open_interest_shock_v1": 1,
        },
        "alerts_by_severity": {
            "high": 2,
            "medium": 12,
        },
        "alerts_by_confidence": {
            "low": 8,
            "medium": 6,
        },
        "alerts_by_venue": {
            "kalshi": 4,
            "polymarket": 10,
        },
        "cluster_events": [
            {
                "venue_code": "polymarket",
                "venue_market_id": "pm-cluster-market",
                "dominant_side": "yes",
                "cluster_trade_count": "3",
                "net_capital_usd": "25920.00",
                "price_impact_cents": "8.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _health_blocked_payload(**overrides):
    dependent_checks = [
        "watched_markets",
        "raw_events",
        "normalized_trades",
        "dead_letters",
        "data_quality_incidents",
        "alerts",
        "market_baselines",
        "ingest_runtime",
    ]
    payload = {
        "ok": False,
        "status": "blocked",
        "checks": [
            {"name": "config", "status": "pass", "message": "config loaded and validated"},
            {"name": "delivery", "status": "pass", "message": "delivery mode 'console' is allowed and supported"},
            {
                "name": "feature_support",
                "status": "pass",
                "message": "no unsupported feature flags are enabled",
                "details": {"unsupported_enabled_features": []},
            },
            {"name": "live", "status": "pass", "message": "live flags inspected without opening venue connections"},
            {
                "name": "db",
                "status": "fail",
                "message": "DB connection or schema query failed: connection refused",
            },
            *[
                {
                    "name": name,
                    "status": "warn",
                    "message": "not checked because DB is unavailable",
                }
                for name in dependent_checks
            ],
        ],
        "next_actions": [
            "Start local Postgres with 'python scripts\\db_local.py up' and run 'python scripts\\db_local.py verify'."
        ],
    }
    payload.update(overrides)
    return payload


def _health_success_payload(**overrides):
    payload = {
        "ok": True,
        "status": "ready_with_warnings",
        "checks": [
            {"name": "config", "status": "pass", "message": "config loaded and validated"},
            {"name": "delivery", "status": "pass", "message": "delivery mode 'console' is allowed and supported"},
            {
                "name": "feature_support",
                "status": "pass",
                "message": "no unsupported feature flags are enabled",
                "details": {"unsupported_enabled_features": []},
            },
            {"name": "live", "status": "pass", "message": "live flags inspected without opening venue connections"},
            {"name": "db", "status": "pass", "message": "DB connected and read-only schema queries completed"},
            {"name": "db_integrity", "status": "pass", "message": "DB schema integrity contract passed"},
            {"name": "raw_events", "status": "warn", "message": "0 raw event(s)"},
        ],
        "next_actions": [],
    }
    payload.update(overrides)
    return payload


def _status_payload(**overrides):
    payload = {
        "ok": True,
        "database": {
            "target": "localhost:5433/pmfi",
            "status": "error: db offline for test",
            "stats": {},
        },
        "live_mode_enabled": False,
        "features": {
            "enable_polymarket_live": False,
            "enable_kalshi_live": False,
            "enable_orderbook_reconstruction": False,
            "enable_cross_venue_matching": False,
            "enable_wallet_intelligence": False,
            "enable_ml_scoring": False,
        },
        "unsupported_enabled_features": [],
        "delivery": {
            "default_delivery": "console",
        },
        "alert_rules": {
            "enabled_count": 2,
            "enabled": ["large_trade_absolute_v1", "market_relative_large_trade_v1"],
        },
        "fixtures": {
            "raw_dir": "tests/fixtures/raw",
            "count": 11,
        },
    }
    payload.update(overrides)
    return payload


def _ingest_blocked_payload(**overrides):
    payload = {
        "ok": False,
        "status": "blocked",
        "venues": ["kalshi"],
        "checks": [
            {
                "name": "db_connectivity",
                "status": "fail",
                "message": "DB unavailable: db offline for test",
                "details": {"error": "db offline for test"},
            }
        ],
        "next_actions": [
            "Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first."
        ],
    }
    payload.update(overrides)
    return payload


def _ingest_ready_payload(**overrides):
    payload = {
        "ok": True,
        "status": "ready",
        "venues": ["kalshi"],
        "checks": [
            {"name": "db_integrity", "status": "pass", "message": "DB schema integrity contract passed"},
            {"name": "delivery", "status": "pass", "message": "delivery mode 'console' is supported"},
            {"name": "baselines", "status": "pass", "message": "1 persisted baseline(s) loaded"},
            {"name": "watched_markets", "status": "pass", "message": "2 watched market(s)"},
            {"name": "kalshi_subscriptions", "status": "pass", "message": "2 Kalshi ticker subscription(s)"},
            {"name": "live_connections", "status": "pass", "message": "readiness check did not import or connect live adapters"},
        ],
        "subscriptions": {
            "polymarket_asset_ids": 3,
            "kalshi_tickers": 2,
            "kalshi_markets": [
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
            ],
            "polymarket_markets": [
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
            ],
        },
        "next_actions": [],
    }
    payload.update(overrides)
    return payload


def _inspection_success_payload(field, rows=None, **overrides):
    rows = [] if rows is None else rows
    payload = {
        "ok": True,
        "count": len(rows),
        field: rows,
    }
    payload.update(overrides)
    return payload


def _inspection_db_unavailable_payload(**overrides):
    payload = {
        "ok": False,
        "error": "DB unavailable: postgres unavailable",
        "next_actions": [
            "Start local Postgres with 'python scripts\\db_local.py up'.",
            "Verify local Postgres with 'python scripts\\db_local.py verify'.",
        ],
    }
    payload.update(overrides)
    return payload


def _alerts_list_db_unavailable_payload(**overrides):
    payload = {
        "ok": False,
        "error": "postgres unavailable",
        "next_actions": [
            "Start local Postgres with 'python scripts\\db_local.py up' and run 'python scripts\\db_local.py verify'."
        ],
    }
    payload.update(overrides)
    return payload


def _lifecycle_payload(**overrides):
    payload = {
        "ok": True,
        "status": "pass",
        "source": "db_free_runner_contracts",
        "checks": [
            {
                "name": "raw_event_replay_dedupe",
                "status": "pass",
                "details": {
                    "first_run_consumed": 1,
                    "second_run_consumed": 2,
                    "raw_insert_attempts": 3,
                    "normalized_source_ids": ["restart-1", "restart-2"],
                },
            },
            {
                "name": "duplicate_trade_skip",
                "status": "pass",
                "details": {
                    "normalized_events": 1,
                    "insert_trade_calls": 1,
                    "alerts_inserted": 0,
                    "alerts_delivered": 0,
                },
            },
            {
                "name": "suppression_cache_seed",
                "status": "pass",
                "details": {
                    "raw_events_seen": 1,
                    "process_event_calls": 1,
                    "seeded_entries": 1,
                },
            },
            {
                "name": "suppression_window_expiry",
                "status": "pass",
                "details": {
                    "raw_events_seen": 3,
                    "alerts_inserted": 2,
                    "alerts_delivered": 2,
                    "alerts_suppressed": 1,
                    "suppression_window_seconds": 300,
                },
            },
            {
                "name": "non_trade_raw_persistence",
                "status": "pass",
                "details": {
                    "raw_events_seen": 1,
                    "raw_events_inserted": 1,
                    "non_trade_skips": 1,
                    "alerts_inserted": 0,
                    "alerts_delivered": 0,
                },
            },
            {
                "name": "kalshi_rest_poll_overlap_dedupe",
                "status": "pass",
                "details": {
                    "ticker": "KXOVERLAP-26JUN",
                    "poll_windows": [["t1", "t2"], ["t2", "t3"]],
                    "raw_events_seen": 4,
                    "raw_events_inserted": 3,
                    "raw_event_duplicates": 1,
                    "normalized_trades_inserted": 3,
                    "duplicate_trades": 0,
                    "metrics_upserted": 3,
                    "alerts_inserted": 3,
                    "alerts_delivered": 3,
                    "source_event_ids": ["t1", "t2", "t3"],
                    "venue_trade_ids": ["t1", "t2", "t3"],
                    "source_channels": ["rest_trades"],
                },
            },
        ],
    }
    payload.update(overrides)
    return payload


def _baseline_smoke_payload(**overrides):
    payload = {
        "ok": True,
        "status": "pass",
        "source": "db_free_baseline_contracts",
        "checks": [
            {"name": "compute_path_uses_normalized_trades", "status": "pass", "details": {"proof": "compute"}},
            {"name": "baseline_upsert_conflict_constraint", "status": "pass", "details": {"proof": "upsert"}},
            {"name": "baseline_available_alert", "status": "pass", "details": {"proof": "available"}},
            {"name": "baseline_stale_alert", "status": "pass", "details": {"proof": "stale"}},
            {"name": "baseline_missing_alert", "status": "pass", "details": {"proof": "missing"}},
            {"name": "volume_spike_uses_prior_history", "status": "pass", "details": {"proof": "volume"}},
        ],
    }
    payload.update(overrides)
    return payload


def _scope_smoke_payload(**overrides):
    payload = {
        "ok": True,
        "status": "pass",
        "source": "local_scope_contracts",
        "checks": [
            {"name": "authority_docs", "status": "pass"},
            {"name": "hosted_saas_markers_absent", "status": "pass"},
            {"name": "order_execution_markers_absent", "status": "pass"},
            {"name": "platform_scaffold_paths_absent", "status": "pass"},
            {"name": "config_defaults_local", "status": "pass"},
            {"name": "localhost_http_endpoint_validation", "status": "pass"},
            {"name": "default_tests_offline", "status": "pass"},
            {"name": "github_workflow_absent", "status": "pass"},
        ],
    }
    payload.update(overrides)
    return payload


def _autostart_smoke_payload(**overrides):
    payload = {
        "ok": True,
        "status": "pass",
        "source": "db_free_autostart_contracts",
        "checks": [
            {"name": "dry_run_plan_shape", "status": "pass", "details": {}},
            {"name": "absolute_path_safety", "status": "pass", "details": {}},
            {"name": "repo_local_log_path", "status": "pass", "details": {}},
            {"name": "db_recovery_instructions", "status": "pass", "details": {}},
            {"name": "docker_postgres_dependency_warning", "status": "pass", "details": {}},
            {"name": "missing_status_idempotent", "status": "pass", "details": {}},
            {"name": "missing_uninstall_idempotent", "status": "pass", "details": {}},
        ],
    }
    payload.update(overrides)
    return payload


def _setup_smoke_payload(**overrides):
    payload = {
        "ok": False,
        "status": "blocked",
        "command": ["docker", "compose", "-f", "docker-compose.local.yml", "ps"],
        "command_string": "docker compose -f docker-compose.local.yml ps",
        "docker": {
            "available": True,
            "returncode": 1,
            "stdout": "",
            "stderr": "Docker Desktop is unable to start.",
            "diagnostic": {
                "title": "Docker Desktop is not ready for local Postgres.",
                "guidance": [
                    "Start Docker Desktop and wait until it reports the engine is running.",
                    "If startup fails, enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform.",
                    "Run `wsl -l -v` to confirm `docker-desktop` is running, then rerun `python scripts\\db_local.py up`.",
                ],
            },
        },
        "wsl": {
            "checked": True,
            "lines": ["WSL2 cannot start because virtualization is not enabled."],
        },
        "next_actions": [
            "Start Docker Desktop and wait until it reports the engine is running.",
            "If startup fails, enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform.",
            "Run `wsl -l -v` to confirm `docker-desktop` is running, then rerun `python scripts\\db_local.py up`.",
        ],
    }
    payload.update(overrides)
    return payload


def test_operator_smoke_runs_expected_db_free_commands():
    operator_smoke = _load_operator_smoke()
    calls = []

    def command_name(args):
        if len(args) > 4 and args[1:3] == ["-m", "pmfi.cli"] and args[3] == "alerts":
            return f"alerts {args[4]}"
        if len(args) > 3 and args[1:3] == ["-m", "pmfi.cli"]:
            return args[3]
        return Path(args[1]).name

    def runner(args):
        calls.append(args)
        command = command_name(args)
        if command == "review-pass":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "status": "pass_with_warnings",
                        "source": "fixtures",
                        "fixture_files": 11,
                        "normalized_trades": 10,
                        "alerts": 12,
                        "checks": [
                            {"name": "local_only", "status": "pass"},
                            {
                                "name": "fixture_skips",
                                "status": "pass",
                                "details": {
                                    "expected_dead_letter_count": 2,
                                    "fixture_files": 11,
                                    "normalized_trades": 10,
                                    "expected": [
                                        {
                                            "classification": "expected_dead_letter",
                                            "dead_letter_expected": True,
                                            "dead_letter_stage": "normalization",
                                            "dead_letter_error_class": "invalid_price_or_size",
                                            "raw_event_expected": True,
                                            "no_derived_records_expected": True,
                                            "source_event_id": "pm-malformed-1",
                                            "venue_code": "polymarket",
                                            "venue_market_id": "pm-bad-market",
                                            "error": "invalid decimal for price: 'not-a-number'",
                                        },
                                        {
                                            "classification": "expected_dead_letter",
                                            "dead_letter_expected": True,
                                            "dead_letter_stage": "normalization",
                                            "dead_letter_error_class": "invalid_price_or_size",
                                            "raw_event_expected": True,
                                            "no_derived_records_expected": True,
                                            "source_event_id": "ks-rest-malformed-1",
                                            "venue_code": "kalshi",
                                            "source_channel": "rest_trades",
                                            "source_event_type": "trade",
                                            "venue_market_id": "KXBTCD-23DEC3100",
                                            "error": "invalid decimal for count: 'not-a-count'",
                                        }
                                    ],
                                },
                            },
                        ],
                    }
                ),
                stderr="",
            )
        if command == "report":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_fixture_report_payload()),
                stderr="",
            )
        if command == "status":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_status_payload()),
                stderr="",
            )
        if command == "health":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_health_blocked_payload()),
                stderr="",
            )
        if command == "setup_smoke.py":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_setup_smoke_payload()),
                stderr="",
            )
        if command == "dead-letters":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        if command == "data-quality-incidents":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        if command == "delivery-failures":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        if command == "alerts list":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_alerts_list_db_unavailable_payload()),
                stderr="",
            )
        if command == "alerts reviews":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        if command == "alerts fp-rate":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        if command == "ingest":
            return operator_smoke.CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_ingest_blocked_payload()),
                stderr="",
            )
        if command == "replay-fixtures":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=(
                    json.dumps(
                        {
                            "alert": True,
                            "rule_id": "large_trade_absolute_v1",
                            "rule_version": "alert_rules.v1",
                            "data_quality": "complete",
                        }
                    )
                    + "\nReplay: 2 fixtures, 1 alerts\n"
                ),
                stderr="",
            )
        if command == "monitor":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=(
                    "Streaming 11 fixture(s) (delay=0.0s). Press Ctrl+C to stop.\n"
                    "\n[polymarket_last_trade_price.json] venue=polymarket market=condition-1\n"
                    + json.dumps(
                        {
                            "alert": True,
                            "rule_id": "large_trade_absolute_v1",
                            "rule_version": "alert_rules.v1",
                            "data_quality": "complete",
                        }
                    )
                    + "\n[malformed_payload.json] venue=polymarket market=condition-2\n"
                    "  normalization skipped: invalid decimal for price: 'not-a-number'\n"
                    "\nStream complete: 14 alert(s) from 11 fixture(s).\n"
                ),
                stderr="",
            )
        if command == "live-smoke":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=(
                    "[live-smoke] venue=fixture max_events=1 max_seconds=120\n"
                    "[live-smoke] subscription: fixture_source=1 file(s)\n"
                    "\n[live-smoke] done: 1 event(s) processed, 1 captured\n"
                ),
                stderr="",
            )
        if command == "lifecycle_smoke.py":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_lifecycle_payload()),
                stderr="",
            )
        if command == "baseline_smoke.py":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_baseline_smoke_payload()),
                stderr="",
            )
        if command == "scope_smoke.py":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_scope_smoke_payload()),
                stderr="",
            )
        if command == "autostart_smoke.py":
            return operator_smoke.CommandResult(
                args=args,
                returncode=0,
                stdout=json.dumps(_autostart_smoke_payload()),
                stderr="",
            )
        raise AssertionError(args)

    operator_smoke.run_operator_smoke(runner=runner)

    assert [command_name(call) for call in calls] == [
        "review-pass",
        "report",
        "status",
        "setup_smoke.py",
        "health",
        "dead-letters",
        "data-quality-incidents",
        "delivery-failures",
        "alerts list",
        "alerts reviews",
        "alerts fp-rate",
        "ingest",
        "replay-fixtures",
        "monitor",
        "live-smoke",
        "lifecycle_smoke.py",
        "baseline_smoke.py",
        "scope_smoke.py",
        "autostart_smoke.py",
    ]
    assert calls[0][-2:] == ["--format", "json"]
    assert calls[1][4:] == [
        "--source",
        "fixtures",
        "--fixture-dir",
        "tests/fixtures/raw",
        "--format",
        "json",
    ]
    assert calls[2][4:] == ["--format", "json"]
    assert calls[3] == [sys.executable, "scripts/setup_smoke.py", "--format", "json"]
    assert calls[4][4:] == ["--format", "json"]
    assert calls[5][4:] == ["--format", "json"]
    assert calls[6][4:] == ["--format", "json"]
    assert calls[7][4:] == ["--format", "json"]
    assert calls[8][4:] == ["list", "--format", "json"]
    assert calls[9][4:] == ["reviews", "--format", "json"]
    assert calls[10][4:] == ["fp-rate", "--format", "json"]
    assert calls[11][4:] == [
        "--venue",
        "kalshi",
        "--check",
        "--format",
        "json",
    ]
    assert calls[12][4:] == [
        "--fixture-dir",
        "tests/fixtures/raw",
    ]
    assert "--persist" not in calls[12]
    assert "--from-db" not in calls[12]
    assert calls[13][4:] == [
        "--fixture-replay",
        "--fixture-dir",
        "tests/fixtures/raw",
        "--delay",
        "0",
    ]
    for forbidden_flag in (
        "--persist",
        "--from-db",
        "--live",
        "--persist-raw",
        "--save-fixtures",
        "--artifact-dir",
        "--output",
    ):
        assert forbidden_flag not in calls[11]
        assert forbidden_flag not in calls[13]
    assert calls[14][4:] == [
        "--fixture-source",
        "tests/fixtures/raw/kalshi_trade.json",
        "--force",
        "--venue",
        "kalshi",
        "--max-events",
        "1",
    ]
    assert "--persist-raw" not in calls[14]
    assert "--save-fixtures" not in calls[14]
    assert calls[15] == [sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"]
    assert calls[16] == [sys.executable, "scripts/baseline_smoke.py", "--format", "json"]
    assert calls[17] == [sys.executable, "scripts/scope_smoke.py", "--format", "json"]
    assert calls[18] == [sys.executable, "scripts/autostart_smoke.py", "--format", "json"]


@pytest.mark.parametrize(
    ("helper_name", "command", "field"),
    [
        ("assert_dead_letters_result", "dead-letters", "dead_letters"),
        ("assert_data_quality_incidents_result", "data-quality-incidents", "data_quality_incidents"),
        ("assert_delivery_failures_result", "delivery-failures", "delivery_failures"),
        ("assert_alert_reviews_result", "alerts reviews", "reviews"),
    ],
)
def test_operator_smoke_accepts_db_inspection_success_payloads(helper_name, command, field):
    operator_smoke = _load_operator_smoke()

    getattr(operator_smoke, helper_name)(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", command, "--format", "json"],
            returncode=0,
            stdout=json.dumps(_inspection_success_payload(field, [{"id": "row-1"}])),
            stderr="",
        )
    )


def test_operator_smoke_accepts_alert_fp_rate_success_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_alert_fp_rate_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "alerts", "fp-rate", "--format", "json"],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "since": "30d",
                    "bucket": "day",
                    "count": 1,
                    "summaries": [
                        {
                            "rule_key": "large_trade_absolute_v1",
                            "bucket": "2026-06-17T00:00:00+00:00",
                            "reviewed_count": 4,
                            "false_positive_count": 2,
                            "true_positive_count": 1,
                            "noise_count": 1,
                            "unsure_count": 0,
                            "false_positive_rate": 0.5,
                        }
                    ],
                }
            ),
            stderr="",
        )
    )


@pytest.mark.parametrize(
    ("helper_name", "command"),
    [
        ("assert_dead_letters_result", "dead-letters"),
        ("assert_data_quality_incidents_result", "data-quality-incidents"),
        ("assert_delivery_failures_result", "delivery-failures"),
        ("assert_alert_reviews_result", "alerts reviews"),
        ("assert_alert_fp_rate_result", "alerts fp-rate"),
    ],
)
def test_operator_smoke_accepts_db_inspection_unavailable_payloads(helper_name, command):
    operator_smoke = _load_operator_smoke()

    getattr(operator_smoke, helper_name)(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", command, "--format", "json"],
            returncode=1,
            stdout=json.dumps(_inspection_db_unavailable_payload()),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_alert_fp_rate_malformed_summary():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="false_positive_rate"):
        operator_smoke.assert_alert_fp_rate_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "alerts", "fp-rate", "--format", "json"],
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "since": "30d",
                        "bucket": "day",
                        "count": 1,
                        "summaries": [
                            {
                                "rule_key": "large_trade_absolute_v1",
                                "bucket": None,
                                "reviewed_count": 1,
                                "false_positive_count": 1,
                                "true_positive_count": 0,
                                "noise_count": 0,
                                "unsure_count": 0,
                            }
                        ],
                    }
                ),
                stderr="",
            )
        )


def test_operator_smoke_accepts_alerts_list_success_payloads():
    operator_smoke = _load_operator_smoke()

    for stdout in ("[]", json.dumps([{"rule_key": "large_trade_absolute_v1"}])):
        operator_smoke.assert_alerts_list_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "alerts", "list", "--format", "json"],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
        )


def test_operator_smoke_accepts_alerts_list_db_unavailable_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_alerts_list_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "alerts", "list", "--format", "json"],
            returncode=1,
            stdout=json.dumps(_alerts_list_db_unavailable_payload()),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_db_inspection_missing_list_field():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="dead_letters"):
        operator_smoke.assert_dead_letters_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "dead-letters", "--format", "json"],
                returncode=0,
                stdout=json.dumps({"ok": True, "count": 0}),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_count_mismatch():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="count"):
        operator_smoke.assert_data_quality_incidents_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "data-quality-incidents", "--format", "json"],
                returncode=0,
                stdout=json.dumps(
                    _inspection_success_payload("data_quality_incidents", [{"id": "row-1"}], count=2)
                ),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_missing_next_action():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="db_local.py verify"):
        operator_smoke.assert_delivery_failures_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "delivery-failures", "--format", "json"],
                returncode=1,
                stdout=json.dumps(
                    _inspection_db_unavailable_payload(
                        next_actions=["Start local Postgres with 'python scripts\\db_local.py up'."]
                    )
                ),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_traceback_leak():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="traceback"):
        operator_smoke.assert_dead_letters_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "dead-letters", "--format", "json"],
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="Traceback (most recent call last):",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_credential_leak():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="credential"):
        operator_smoke.assert_data_quality_incidents_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "data-quality-incidents", "--format", "json"],
                returncode=1,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="DATABASE_URL=postgresql://pmfi:secret@localhost/pmfi",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_malformed_stdout():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="unparsable JSON"):
        operator_smoke.assert_delivery_failures_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "delivery-failures", "--format", "json"],
                returncode=1,
                stdout="{",
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_db_inspection_unsupported_exit():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="unsupported exit 2"):
        operator_smoke.assert_dead_letters_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "dead-letters", "--format", "json"],
                returncode=2,
                stdout=json.dumps(_inspection_db_unavailable_payload()),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_alerts_list_non_list_success_payload():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="JSON payload must be a list"):
        operator_smoke.assert_alerts_list_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "alerts", "list", "--format", "json"],
                returncode=0,
                stdout=json.dumps({"ok": True, "count": 0, "alerts": []}),
                stderr="",
            )
        )


def test_operator_smoke_accepts_blocked_ingest_db_unavailable_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_ingest_check_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"],
            returncode=1,
            stdout=json.dumps(_ingest_blocked_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_ready_ingest_payload_with_plan_details():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_ingest_check_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_ingest_ready_payload()),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_missing_ingest_db_verify_next_action():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="db_local.py verify"):
        operator_smoke.assert_ingest_check_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"],
                returncode=1,
                stdout=json.dumps(
                    _ingest_blocked_payload(
                        next_actions=["Run 'python scripts\\db_local.py up' first."]
                    )
                ),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_ready_ingest_missing_kalshi_markets():
    operator_smoke = _load_operator_smoke()
    subscriptions = dict(_ingest_ready_payload()["subscriptions"])
    subscriptions.pop("kalshi_markets")

    with pytest.raises(RuntimeError, match="kalshi_markets"):
        operator_smoke.assert_ingest_check_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_ingest_ready_payload(subscriptions=subscriptions)),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_ready_ingest_count_mismatch():
    operator_smoke = _load_operator_smoke()
    subscriptions = dict(_ingest_ready_payload()["subscriptions"])
    subscriptions["kalshi_tickers"] = 3

    with pytest.raises(RuntimeError, match="kalshi_tickers"):
        operator_smoke.assert_ingest_check_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "ingest", "--venue", "kalshi", "--check", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_ingest_ready_payload(subscriptions=subscriptions)),
                stderr="",
            )
        )


def test_operator_smoke_accepts_successful_health_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_health_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "health", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_health_success_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_operator_status_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_status_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "-m", "pmfi.cli", "status", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_status_payload()),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_missing_status_unsupported_features():
    operator_smoke = _load_operator_smoke()
    payload = _status_payload()
    payload.pop("unsupported_enabled_features")

    with pytest.raises(RuntimeError, match="unsupported_enabled_features"):
        operator_smoke.assert_status_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "status", "--format", "json"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_missing_health_feature_support():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _health_success_payload()["checks"]
        if check["name"] != "feature_support"
    ]

    with pytest.raises(RuntimeError, match="feature_support"):
        operator_smoke.assert_health_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "health", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_health_success_payload(checks=checks)),
                stderr="",
            )
        )


def test_operator_smoke_accepts_lifecycle_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_lifecycle_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_lifecycle_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_baseline_smoke_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_baseline_smoke_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "scripts/baseline_smoke.py", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_baseline_smoke_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_scope_smoke_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_scope_smoke_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "scripts/scope_smoke.py", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_scope_smoke_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_autostart_smoke_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_autostart_smoke_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "scripts/autostart_smoke.py", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_autostart_smoke_payload()),
            stderr="",
        )
    )


def test_operator_smoke_accepts_setup_smoke_payload():
    operator_smoke = _load_operator_smoke()

    operator_smoke.assert_setup_smoke_result(
        operator_smoke.CommandResult(
            args=[sys.executable, "scripts/setup_smoke.py", "--format", "json"],
            returncode=0,
            stdout=json.dumps(_setup_smoke_payload()),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_malformed_setup_smoke_payload():
    operator_smoke = _load_operator_smoke()
    payload = _setup_smoke_payload()
    payload.pop("docker")

    with pytest.raises(RuntimeError, match="docker object"):
        operator_smoke.assert_setup_smoke_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/setup_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_empty_setup_smoke_payload():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="setup-smoke produced empty stdout"):
        operator_smoke.assert_setup_smoke_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/setup_smoke.py", "--format", "json"],
                returncode=0,
                stdout="",
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_malformed_scope_smoke_payload():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _scope_smoke_payload()["checks"]
        if check["name"] != "default_tests_offline"
    ]

    with pytest.raises(RuntimeError, match="default_tests_offline"):
        operator_smoke.assert_scope_smoke_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/scope_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_scope_smoke_payload(checks=checks)),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_malformed_autostart_smoke_payload():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _autostart_smoke_payload()["checks"]
        if check["name"] != "repo_local_log_path"
    ]

    with pytest.raises(RuntimeError, match="repo_local_log_path"):
        operator_smoke.assert_autostart_smoke_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/autostart_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_autostart_smoke_payload(checks=checks)),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_malformed_baseline_smoke_payload():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _baseline_smoke_payload()["checks"]
        if check["name"] != "baseline_stale_alert"
    ]

    with pytest.raises(RuntimeError, match="baseline_stale_alert"):
        operator_smoke.assert_baseline_smoke_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/baseline_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_baseline_smoke_payload(checks=checks)),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_empty_lifecycle_payload():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="lifecycle-smoke produced empty stdout"):
        operator_smoke.assert_lifecycle_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
                returncode=0,
                stdout="",
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_missing_lifecycle_restart_check():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _lifecycle_payload()["checks"]
        if check["name"] != "raw_event_replay_dedupe"
    ]

    with pytest.raises(RuntimeError, match="raw_event_replay_dedupe"):
        operator_smoke.assert_lifecycle_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_lifecycle_payload(checks=checks)),
            stderr="",
        )
    )


def test_operator_smoke_fails_closed_on_missing_lifecycle_kalshi_overlap_check():
    operator_smoke = _load_operator_smoke()
    checks = [
        check
        for check in _lifecycle_payload()["checks"]
        if check["name"] != "kalshi_rest_poll_overlap_dedupe"
    ]

    with pytest.raises(RuntimeError, match="kalshi_rest_poll_overlap_dedupe"):
        operator_smoke.assert_lifecycle_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(_lifecycle_payload(checks=checks)),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_wrong_lifecycle_suppression_expiry_counts():
    operator_smoke = _load_operator_smoke()
    payload = _lifecycle_payload()
    checks = {check["name"]: check for check in payload["checks"]}
    checks["suppression_window_expiry"]["details"]["alerts_suppressed"] = 0

    with pytest.raises(RuntimeError, match="suppression_window_expiry alerts_suppressed"):
        operator_smoke.assert_lifecycle_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_wrong_lifecycle_kalshi_overlap_counts():
    operator_smoke = _load_operator_smoke()
    payload = _lifecycle_payload()
    checks = {check["name"]: check for check in payload["checks"]}
    checks["kalshi_rest_poll_overlap_dedupe"]["details"]["alerts_delivered"] = 4

    with pytest.raises(RuntimeError, match="kalshi_rest_poll_overlap_dedupe alerts_delivered"):
        operator_smoke.assert_lifecycle_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_status_credential_leak():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="credential"):
        operator_smoke.assert_status_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "status", "--format", "json"],
                returncode=0,
                stdout=json.dumps(
                    _status_payload(
                        database={
                            "target": "pmfi:secret@localhost:5433/pmfi",
                            "status": "ok",
                            "stats": {},
                        }
                    )
                ),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_empty_health_payload():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="health produced empty stdout"):
        operator_smoke.assert_health_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "health", "--format", "json"],
                returncode=1,
                stdout="",
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_missing_health_db_next_actions():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="db_local.py up"):
        operator_smoke.assert_health_result(
            operator_smoke.CommandResult(
                args=[sys.executable, "-m", "pmfi.cli", "health", "--format", "json"],
                returncode=1,
                stdout=json.dumps(_health_blocked_payload(next_actions=["Run something else."])),
                stderr="",
            )
        )


def test_operator_smoke_fails_closed_on_missing_local_only_evidence():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="local_only"):
        operator_smoke.assert_review_payload(
            {
                "ok": True,
                "status": "pass",
                "source": "fixtures",
                "fixture_files": 1,
                "normalized_trades": 1,
                "alerts": 1,
                "checks": [],
            }
        )


def test_operator_smoke_fails_closed_on_malformed_fixture_skip_evidence():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="fixture_skips.*ks-rest-malformed-1"):
        operator_smoke.assert_review_payload(
            {
                "ok": True,
                "status": "pass",
                "source": "fixtures",
                "fixture_files": 11,
                "normalized_trades": 10,
                "alerts": 12,
                "checks": [
                    {"name": "local_only", "status": "pass"},
                    {
                        "name": "fixture_skips",
                        "status": "pass",
                        "details": {
                            "expected_dead_letter_count": 2,
                            "fixture_files": 11,
                            "normalized_trades": 10,
                            "expected": [
                                {
                                    "classification": "expected_dead_letter",
                                    "dead_letter_expected": True,
                                    "dead_letter_stage": "normalization",
                                    "dead_letter_error_class": "invalid_price_or_size",
                                    "raw_event_expected": True,
                                    "no_derived_records_expected": True,
                                    "source_event_id": "pm-malformed-1",
                                    "venue_code": "polymarket",
                                    "venue_market_id": "pm-bad-market",
                                    "error": "invalid decimal for price: 'not-a-number'",
                                }
                            ],
                        },
                    },
                ],
            }
        )


def test_operator_smoke_fails_closed_on_missing_polymarket_malformed_fixture_skip_evidence():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="fixture_skips.*pm-malformed-1"):
        operator_smoke.assert_review_payload(
            {
                "ok": True,
                "status": "pass",
                "source": "fixtures",
                "fixture_files": 12,
                "normalized_trades": 10,
                "alerts": 12,
                "checks": [
                    {"name": "local_only", "status": "pass"},
                    {
                        "name": "fixture_skips",
                        "status": "pass",
                        "details": {
                            "expected_dead_letter_count": 2,
                            "fixture_files": 12,
                            "normalized_trades": 10,
                            "expected": [
                                {
                                    "classification": "expected_dead_letter",
                                    "dead_letter_expected": True,
                                    "dead_letter_stage": "normalization",
                                    "dead_letter_error_class": "invalid_price_or_size",
                                    "raw_event_expected": True,
                                    "no_derived_records_expected": True,
                                    "source_event_id": "ks-rest-malformed-1",
                                    "venue_code": "kalshi",
                                    "source_channel": "rest_trades",
                                    "source_event_type": "trade",
                                    "venue_market_id": "KXBTCD-23DEC3100",
                                    "error": "invalid decimal for count: 'not-a-count'",
                                }
                            ],
                        },
                    },
                ],
            }
        )


def test_operator_smoke_fails_closed_on_missing_report_breakdown():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="report alerts_by_rule.*open_interest_shock_v1"):
        operator_smoke.assert_report_payload(
            _fixture_report_payload(
                alerts_by_rule={
                    "directional_cluster_v1": 1,
                    "large_trade_absolute_v1": 4,
                    "market_relative_large_trade_v1": 8,
                }
            )
        )


def test_operator_smoke_fails_closed_on_missing_report_cluster_evidence():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="report cluster_events.*pm-cluster-market"):
        operator_smoke.assert_report_payload(_fixture_report_payload(cluster_events=[]))


def test_operator_smoke_fails_closed_on_empty_live_runtime():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="processed count"):
        operator_smoke.assert_live_smoke_output(
            "[live-smoke] subscription: fixture_source=1 file(s)\n"
            "[live-smoke] done: 0 event(s) processed, 0 captured\n"
        )


def test_operator_smoke_fails_closed_on_missing_replay_delivery_fields():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="rule_version, data_quality"):
        operator_smoke.assert_replay_delivery_output(
            json.dumps(
                {
                    "alert": True,
                    "rule_id": "large_trade_absolute_v1",
                    "severity": "high",
                }
            )
        )


def test_operator_smoke_fails_closed_on_empty_monitor_output():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="streaming start line"):
        operator_smoke.assert_monitor_output("")


def test_operator_smoke_fails_closed_on_zero_monitor_counts():
    operator_smoke = _load_operator_smoke()

    with pytest.raises(RuntimeError, match="fixture count"):
        operator_smoke.assert_monitor_output(
            "Streaming 0 fixture(s) (delay=0.0s). Press Ctrl+C to stop.\n"
            "\nStream complete: 0 alert(s) from 0 fixture(s).\n"
        )


def test_task_wrapper_routes_operator_smoke(monkeypatch):
    import scripts.task as task

    calls = []

    def python_script(script, *args):
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", python_script)

    assert task.main(["operator-smoke"]) == 0
    assert calls == [("scripts/operator_smoke.py", ())]


def test_task_wrapper_routes_baseline_smoke(monkeypatch):
    import scripts.task as task

    calls = []

    def python_script(script, *args):
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", python_script)

    assert task.main(["baseline-smoke"]) == 0
    assert calls == [("scripts/baseline_smoke.py", ())]


def test_task_wrapper_routes_scope_smoke(monkeypatch):
    import scripts.task as task

    calls = []

    def python_script(script, *args):
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", python_script)

    assert task.main(["scope-smoke"]) == 0
    assert calls == [("scripts/scope_smoke.py", ())]
