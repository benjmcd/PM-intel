r"""Validate the DB-free local operator path.

This smoke gate is intentionally validate-only: it uses fixture-backed CLI
commands, does not require Docker/Postgres, does not make live calls, and does
not request artifact-writing command modes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

try:
    from setup_smoke import validate_payload as validate_setup_payload
except ModuleNotFoundError:  # pragma: no cover - used when imported as a module from repo root
    from scripts.setup_smoke import validate_payload as validate_setup_payload


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]


def env_with_src(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    src = str(ROOT / "src")
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not current else src + os.pathsep + current
    return env


def run_command(args: list[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        env=env_with_src(),
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _run_required(label: str, args: list[str], runner: Runner) -> CommandResult:
    print(f"== {label} ==", flush=True)
    result = runner(args)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit {result.returncode}: {' '.join(args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    if not result.stdout.strip():
        raise RuntimeError(f"{label} produced empty stdout")
    return result


def _parse_json_value(label: str, result: CommandResult) -> object:
    if not result.stdout.strip():
        raise RuntimeError(f"{label} produced empty stdout")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} produced unparsable JSON: {exc}") from exc


def _parse_json(label: str, result: CommandResult) -> dict:
    payload = _parse_json_value(label, result)
    _require(isinstance(payload, dict), f"{label} JSON payload must be an object")
    return payload


def _count(payload: dict, key: str, label: str) -> int:
    value = payload.get(key)
    _require(isinstance(value, int), f"{label} missing integer {key}")
    _require(value > 0, f"{label} {key} must be > 0")
    return value


def _assert_positive_breakdown(payload: dict, key: str, expected_names: tuple[str, ...]) -> None:
    breakdown = payload.get(key)
    _require(isinstance(breakdown, dict), f"report {key} must be an object")
    invalid = []
    for name in expected_names:
        count = breakdown.get(name)
        if not isinstance(count, int) or count <= 0:
            invalid.append(f"{name}={count!r}")
    _require(
        not invalid,
        f"report {key} missing positive count(s): {', '.join(invalid)}",
    )


def _assert_cluster_event(payload: dict) -> None:
    cluster_events = payload.get("cluster_events")
    _require(
        isinstance(cluster_events, list) and cluster_events,
        "report cluster_events missing polymarket pm-cluster-market evidence: must be a non-empty list",
    )

    expected_fields = {
        "venue_code": "polymarket",
        "venue_market_id": "pm-cluster-market",
        "dominant_side": "yes",
        "cluster_trade_count": "3",
        "net_capital_usd": "25920.00",
        "price_impact_cents": "8.00",
    }
    event = next(
        (
            item
            for item in cluster_events
            if isinstance(item, dict)
            and item.get("venue_code") == "polymarket"
            and item.get("venue_market_id") == "pm-cluster-market"
        ),
        None,
    )
    _require(
        event is not None,
        "report cluster_events missing polymarket pm-cluster-market directional cluster evidence",
    )

    mismatches = [
        f"{field}={event.get(field)!r}"
        for field, expected_value in expected_fields.items()
        if str(event.get(field)) != expected_value
    ]
    _require(
        not mismatches,
        "report cluster_events polymarket pm-cluster-market invalid field(s): " + ", ".join(mismatches),
    )


def _find_check(payload: dict, name: str) -> dict | None:
    checks = payload.get("checks")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if isinstance(check, dict) and check.get("name") == name:
            return check
    return None


def _require_check_status(payload: dict, label: str, name: str, status: str) -> dict:
    check = _find_check(payload, name)
    _require(check is not None, f"{label} missing {name} check")
    actual = check.get("status")
    _require(actual == status, f"{label} {name} check must be {status}, got {actual!r}")
    return check


def _require_any_check_status(payload: dict, label: str, names: tuple[str, ...], status: str) -> dict:
    for name in names:
        check = _find_check(payload, name)
        if check is not None:
            actual = check.get("status")
            _require(actual == status, f"{label} {name} check must be {status}, got {actual!r}")
            return check
    raise RuntimeError(f"{label} missing one of these checks: {', '.join(names)}")


def _require_db_unavailable_warning(payload: dict, name: str) -> None:
    check = _require_check_status(payload, "health", name, "warn")
    message = check.get("message")
    _require(
        isinstance(message, str) and "DB is unavailable" in message,
        f"health {name} warning must explain DB is unavailable",
    )


def _assert_health_success(payload: dict) -> None:
    _require(payload.get("ok") is True, "health ok must be true when exit is 0")
    _require(
        payload.get("status") in {"ready", "ready_with_warnings"},
        "health status must be ready or ready_with_warnings when exit is 0",
    )
    _require_check_status(payload, "health", "config", "pass")
    _require_check_status(payload, "health", "delivery", "pass")
    _require_check_status(payload, "health", "feature_support", "pass")
    _require_check_status(payload, "health", "live", "pass")
    _require_check_status(payload, "health", "db", "pass")
    _require_any_check_status(
        payload,
        "health",
        ("db_integrity", "database_integrity", "schema_integrity"),
        "pass",
    )


def _assert_health_blocked_db_unavailable(payload: dict) -> None:
    _require(payload.get("ok") is False, "health ok must be false when DB is unavailable")
    _require(payload.get("status") == "blocked", "health status must be blocked when DB is unavailable")
    _require_check_status(payload, "health", "config", "pass")
    _require_check_status(payload, "health", "delivery", "pass")
    _require_check_status(payload, "health", "feature_support", "pass")
    _require_check_status(payload, "health", "live", "pass")
    db = _require_check_status(payload, "health", "db", "fail")
    message = db.get("message")
    _require(isinstance(message, str) and message, "health db failure must include a useful message")

    for name in (
        "watched_markets",
        "raw_events",
        "normalized_trades",
        "dead_letters",
        "data_quality_incidents",
        "alerts",
        "market_baselines",
        "ingest_runtime",
    ):
        _require_db_unavailable_warning(payload, name)

    next_actions = payload.get("next_actions")
    _require(isinstance(next_actions, list) and next_actions, "health next_actions must be a non-empty list")
    _require(
        all(isinstance(action, str) for action in next_actions),
        "health next_actions must contain only strings",
    )
    _require(
        any("db_local.py up" in action for action in next_actions),
        "health next_actions must mention db_local.py up",
    )
    _require(
        any("db_local.py verify" in action for action in next_actions),
        "health next_actions must mention db_local.py verify",
    )


def assert_health_result(result: CommandResult) -> None:
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"health failed with unsupported exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    payload = _parse_json("health", result)
    if result.returncode == 0:
        _assert_health_success(payload)
    else:
        _assert_health_blocked_db_unavailable(payload)


def assert_status_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"status failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    lower_stdout = result.stdout.lower()
    _require("secret" not in lower_stdout and "password" not in lower_stdout, "status JSON leaked credential text")

    payload = _parse_json("status", result)
    _require(payload.get("ok") is True, "status ok must be true")

    database = payload.get("database")
    _require(isinstance(database, dict), "status database must be an object")
    target = database.get("target") or database.get("display_target")
    _require(isinstance(target, str) and target, "status database target must be a non-empty string")
    _require("@" not in target, "status database target must not contain credential text")
    _require(isinstance(database.get("status"), str) and database["status"], "status database status missing")
    _require(isinstance(database.get("stats"), dict), "status database stats must be an object")

    _require(isinstance(payload.get("live_mode_enabled"), bool), "status live_mode_enabled must be boolean")
    features = payload.get("features")
    _require(isinstance(features, dict), "status features must be an object")
    for name in (
        "enable_polymarket_live",
        "enable_kalshi_live",
        "enable_orderbook_reconstruction",
        "enable_cross_venue_matching",
        "enable_wallet_intelligence",
        "enable_ml_scoring",
    ):
        _require(isinstance(features.get(name), bool), f"status features.{name} must be boolean")
    unsupported = payload.get("unsupported_enabled_features")
    _require(
        isinstance(unsupported, list) and all(isinstance(name, str) for name in unsupported),
        "status unsupported_enabled_features must be a list of strings",
    )

    delivery = payload.get("delivery")
    _require(isinstance(delivery, dict), "status delivery must be an object")
    _require(isinstance(delivery.get("default_delivery"), str) and delivery["default_delivery"], "status default_delivery missing")

    alert_rules = payload.get("alert_rules")
    _require(isinstance(alert_rules, dict), "status alert_rules must be an object")
    enabled = alert_rules.get("enabled")
    _require(isinstance(enabled, list), "status alert_rules.enabled must be a list")
    _require(all(isinstance(rule, str) and rule for rule in enabled), "status enabled alert rules must be strings")
    _require(alert_rules.get("enabled_count") == len(enabled), "status enabled alert rule count mismatch")

    fixtures = payload.get("fixtures")
    _require(isinstance(fixtures, dict), "status fixtures must be an object")
    fixture_dir = fixtures.get("raw_dir") or fixtures.get("fixture_dir")
    _require(isinstance(fixture_dir, str) and fixture_dir, "status fixture directory missing")
    _count(fixtures, "count", "status fixtures")


def _combined_command_output(result: CommandResult) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _assert_no_traceback_or_credential_leak(label: str, result: CommandResult) -> None:
    output = _combined_command_output(result)
    lower_output = output.lower()
    _require("traceback" not in lower_output, f"{label} output leaked traceback text")

    credential_markers = (
        "database_url",
        "password=",
        "passwd=",
        "pwd=",
        "secret=",
        "postgresql://",
        "postgres://",
    )
    _require(
        not any(marker in lower_output for marker in credential_markers),
        f"{label} output leaked credential text",
    )


def _require_db_next_actions(payload: dict, label: str) -> None:
    next_actions = payload.get("next_actions")
    _require(isinstance(next_actions, list) and next_actions, f"{label} next_actions must be a non-empty list")
    _require(
        all(isinstance(action, str) for action in next_actions),
        f"{label} next_actions must contain only strings",
    )
    _require(
        any("db_local.py up" in action for action in next_actions),
        f"{label} next_actions must mention db_local.py up",
    )
    _require(
        any("db_local.py verify" in action for action in next_actions),
        f"{label} next_actions must mention db_local.py verify",
    )


def _assert_db_inspection_success(payload: dict, label: str, list_field: str) -> None:
    _require(payload.get("ok") is True, f"{label} ok must be true when exit is 0")
    count = payload.get("count")
    _require(isinstance(count, int), f"{label} missing integer count")
    _require(count >= 0, f"{label} count must be >= 0")
    rows = payload.get(list_field)
    _require(isinstance(rows, list), f"{label} missing list field {list_field}")
    _require(count == len(rows), f"{label} count must match {list_field} length")
    _require(all(isinstance(row, dict) for row in rows), f"{label} {list_field} must contain only objects")


def _assert_db_inspection_unavailable(payload: dict, label: str) -> None:
    _require(payload.get("ok") is False, f"{label} ok must be false when DB is unavailable")
    error = payload.get("error")
    _require(
        isinstance(error, str) and "db unavailable" in error.lower(),
        f"{label} error must explain DB unavailable",
    )
    _require_db_next_actions(payload, label)


def _assert_db_inspection_result(result: CommandResult, label: str, list_field: str) -> None:
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"{label} failed with unsupported exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    if result.returncode == 1:
        _assert_no_traceback_or_credential_leak(label, result)
    payload = _parse_json(label, result)
    if result.returncode == 0:
        _assert_db_inspection_success(payload, label, list_field)
    else:
        _assert_db_inspection_unavailable(payload, label)


def assert_dead_letters_result(result: CommandResult) -> None:
    _assert_db_inspection_result(result, "dead-letters", "dead_letters")


def assert_data_quality_incidents_result(result: CommandResult) -> None:
    _assert_db_inspection_result(result, "data-quality-incidents", "data_quality_incidents")


def assert_delivery_failures_result(result: CommandResult) -> None:
    _assert_db_inspection_result(result, "delivery-failures", "delivery_failures")


def assert_alert_reviews_result(result: CommandResult) -> None:
    _assert_db_inspection_result(result, "alerts reviews", "reviews")


def assert_alert_fp_rate_result(result: CommandResult) -> None:
    label = "alerts fp-rate"
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"{label} failed with unsupported exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    if result.returncode == 1:
        _assert_no_traceback_or_credential_leak(label, result)
    payload = _parse_json(label, result)
    if result.returncode == 1:
        _assert_db_inspection_unavailable(payload, label)
        return

    _require(payload.get("ok") is True, f"{label} ok must be true when exit is 0")
    _require(isinstance(payload.get("since"), str) and payload["since"], f"{label} since must be a non-empty string")
    _require(payload.get("bucket") in {"all", "day", "hour"}, f"{label} bucket must be all, day, or hour")
    count = payload.get("count")
    _require(isinstance(count, int) and count >= 0, f"{label} missing non-negative integer count")
    summaries = payload.get("summaries")
    _require(isinstance(summaries, list), f"{label} missing list field summaries")
    _require(count == len(summaries), f"{label} count must match summaries length")
    required_counts = (
        "reviewed_count",
        "false_positive_count",
        "true_positive_count",
        "noise_count",
        "unsure_count",
    )
    for index, row in enumerate(summaries, start=1):
        row_label = f"{label} summaries[{index}]"
        _require(isinstance(row, dict), f"{row_label} must be an object")
        _require(isinstance(row.get("rule_key"), str) and row["rule_key"], f"{row_label}.rule_key missing")
        bucket = row.get("bucket")
        _require(bucket is None or isinstance(bucket, str), f"{row_label}.bucket must be null or string")
        for field in required_counts:
            value = row.get(field)
            _require(isinstance(value, int) and value >= 0, f"{row_label}.{field} must be a non-negative integer")
        rate = row.get("false_positive_rate")
        _require(isinstance(rate, int | float), f"{row_label}.false_positive_rate must be numeric")
        _require(0 <= float(rate) <= 1, f"{row_label}.false_positive_rate must be between 0 and 1")


def assert_alerts_list_result(result: CommandResult) -> None:
    label = "alerts list"
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"{label} failed with unsupported exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    if result.returncode == 0:
        payload = _parse_json_value(label, result)
        _require(isinstance(payload, list), f"{label} JSON payload must be a list")
        _require(all(isinstance(alert, dict) for alert in payload), f"{label} payload must contain only alert objects")
        return

    _assert_no_traceback_or_credential_leak(label, result)
    payload = _parse_json(label, result)
    _require(payload.get("ok") is False, f"{label} ok must be false when DB is unavailable")
    error = payload.get("error")
    _require(isinstance(error, str) and error, f"{label} error must be a non-empty string")
    _require_db_next_actions(payload, label)


def _checks_by_name(payload: dict, label: str) -> dict[str, dict]:
    checks = payload.get("checks")
    _require(isinstance(checks, list), f"{label} checks must be a list")
    named = {}
    for check in checks:
        _require(isinstance(check, dict), f"{label} checks must contain only objects")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"{label} check missing name")
        named[name] = check
    return named


def _require_non_empty_string(value: object, message: str) -> None:
    _require(isinstance(value, str) and value, message)


def _assert_optional_text_field(item: dict, field: str, label: str) -> None:
    if field in item and item[field] is not None:
        _require(isinstance(item[field], str), f"{label} {field} must be a string when present")


def _assert_ingest_blocked_db_unavailable(payload: dict) -> None:
    _require(payload.get("ok") is False, "ingest-check ok must be false when DB is unavailable")
    _require(payload.get("status") == "blocked", "ingest-check status must be blocked when DB is unavailable")
    checks = _checks_by_name(payload, "ingest-check")
    db_connectivity = checks.get("db_connectivity")
    _require(db_connectivity is not None, "ingest-check missing db_connectivity check")
    _require(
        db_connectivity.get("status") == "fail",
        f"ingest-check db_connectivity check must fail, got {db_connectivity.get('status')!r}",
    )
    message = db_connectivity.get("message")
    _require(
        isinstance(message, str) and "DB unavailable" in message,
        "ingest-check db_connectivity failure must explain DB is unavailable",
    )

    next_actions = payload.get("next_actions")
    _require(isinstance(next_actions, list) and next_actions, "ingest-check next_actions must be a non-empty list")
    _require(
        all(isinstance(action, str) for action in next_actions),
        "ingest-check next_actions must contain only strings",
    )
    _require(
        any("db_local.py up" in action for action in next_actions),
        "ingest-check next_actions must mention db_local.py up",
    )
    _require(
        any("db_local.py verify" in action for action in next_actions),
        "ingest-check next_actions must mention db_local.py verify",
    )


def _assert_ingest_kalshi_markets(subscriptions: dict) -> None:
    kalshi_tickers = subscriptions.get("kalshi_tickers")
    _require(isinstance(kalshi_tickers, int), "ingest-check subscriptions.kalshi_tickers must be an integer")
    _require(kalshi_tickers > 0, "ingest-check subscriptions.kalshi_tickers must be > 0")

    kalshi_markets = subscriptions.get("kalshi_markets")
    _require(
        isinstance(kalshi_markets, list) and kalshi_markets,
        "ingest-check subscriptions.kalshi_markets must be a non-empty list",
    )
    _require(
        kalshi_tickers == len(kalshi_markets),
        "ingest-check subscriptions.kalshi_tickers must match kalshi_markets length",
    )
    for index, market in enumerate(kalshi_markets, start=1):
        label = f"ingest-check subscriptions.kalshi_markets[{index}]"
        _require(isinstance(market, dict), f"{label} must be an object")
        for field in ("market_id", "venue_market_id", "ticker"):
            _require_non_empty_string(market.get(field), f"{label}.{field} must be a non-empty string")
        _assert_optional_text_field(market, "title", label)
        _assert_optional_text_field(market, "status", label)


def _assert_ingest_polymarket_markets(subscriptions: dict) -> None:
    if "polymarket_asset_ids" in subscriptions:
        _require(
            isinstance(subscriptions["polymarket_asset_ids"], int),
            "ingest-check subscriptions.polymarket_asset_ids must be an integer",
        )

    polymarket_markets = subscriptions.get("polymarket_markets")
    if polymarket_markets is None:
        return
    _require(isinstance(polymarket_markets, list), "ingest-check subscriptions.polymarket_markets must be a list")

    asset_id_total = 0
    counted_all_markets = True
    for index, market in enumerate(polymarket_markets, start=1):
        label = f"ingest-check subscriptions.polymarket_markets[{index}]"
        _require(isinstance(market, dict), f"{label} must be an object")
        for field in ("market_id", "venue_market_id"):
            _require_non_empty_string(market.get(field), f"{label}.{field} must be a non-empty string")
        _assert_optional_text_field(market, "title", label)
        _assert_optional_text_field(market, "status", label)

        asset_ids = market.get("asset_ids")
        if asset_ids is not None:
            _require(isinstance(asset_ids, list), f"{label}.asset_ids must be a list when present")
            _require(
                all(isinstance(asset_id, str) and asset_id for asset_id in asset_ids),
                f"{label}.asset_ids must contain only non-empty strings",
            )
        asset_id_count = market.get("asset_id_count")
        if asset_id_count is not None:
            _require(isinstance(asset_id_count, int), f"{label}.asset_id_count must be an integer when present")
            _require(asset_id_count >= 0, f"{label}.asset_id_count must be >= 0")
            if asset_ids is not None:
                _require(asset_id_count == len(asset_ids), f"{label}.asset_id_count must match asset_ids length")
            asset_id_total += asset_id_count
        else:
            counted_all_markets = False

    if "polymarket_asset_ids" in subscriptions and counted_all_markets:
        _require(
            subscriptions["polymarket_asset_ids"] == asset_id_total,
            "ingest-check subscriptions.polymarket_asset_ids must match polymarket_markets asset_id_count total",
        )


def _assert_ingest_ready(payload: dict) -> None:
    _require(payload.get("ok") is True, "ingest-check ok must be true when exit is 0")
    _require(payload.get("status") == "ready", "ingest-check status must be ready when exit is 0")
    checks = _checks_by_name(payload, "ingest-check")
    live_connections = checks.get("live_connections")
    _require(live_connections is not None, "ingest-check missing live_connections check")
    _require(
        live_connections.get("status") == "pass",
        f"ingest-check live_connections check must pass, got {live_connections.get('status')!r}",
    )

    subscriptions = payload.get("subscriptions")
    _require(isinstance(subscriptions, dict), "ingest-check subscriptions must be an object")
    for key in ("polymarket_asset_ids", "kalshi_tickers"):
        if key in subscriptions:
            _require(isinstance(subscriptions[key], int), f"ingest-check subscriptions.{key} must be an integer")
    _assert_ingest_kalshi_markets(subscriptions)
    _assert_ingest_polymarket_markets(subscriptions)


def assert_ingest_check_result(result: CommandResult) -> None:
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"ingest-check failed with unsupported exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    payload = _parse_json("ingest-check", result)
    if result.returncode == 0:
        _assert_ingest_ready(payload)
    else:
        _assert_ingest_blocked_db_unavailable(payload)


def _run_health(args: list[str], runner: Runner) -> CommandResult:
    print("== health ==", flush=True)
    result = runner(args)
    assert_health_result(result)
    return result


def _run_ingest_check(args: list[str], runner: Runner) -> CommandResult:
    print("== ingest-check ==", flush=True)
    result = runner(args)
    assert_ingest_check_result(result)
    return result


def _run_db_inspection(
    label: str,
    args: list[str],
    runner: Runner,
    assertion: Callable[[CommandResult], None],
) -> CommandResult:
    print(f"== {label} ==", flush=True)
    result = runner(args)
    assertion(result)
    return result


def _assert_malformed_fixture_skip_evidence(check: dict) -> None:
    _require(check.get("status") == "pass", "review-pass fixture_skips check must pass")
    details = check.get("details")
    _require(isinstance(details, dict), "review-pass fixture_skips missing details object")
    required_malformed = [
        {
            "source_event_id": "pm-malformed-1",
            "error_fragment": "not-a-number",
            "fields": {
                "classification": "expected_dead_letter",
                "dead_letter_expected": True,
                "dead_letter_stage": "normalization",
                "dead_letter_error_class": "invalid_price_or_size",
                "raw_event_expected": True,
                "no_derived_records_expected": True,
                "venue_code": "polymarket",
                "venue_market_id": "pm-bad-market",
            },
        },
        {
            "source_event_id": "ks-rest-malformed-1",
            "error_fragment": "not-a-count",
            "fields": {
                "classification": "expected_dead_letter",
                "dead_letter_expected": True,
                "dead_letter_stage": "normalization",
                "dead_letter_error_class": "invalid_price_or_size",
                "raw_event_expected": True,
                "no_derived_records_expected": True,
                "venue_code": "kalshi",
                "source_channel": "rest_trades",
                "source_event_type": "trade",
                "venue_market_id": "KXBTCD-23DEC3100",
            },
        },
    ]
    _require(
        details.get("expected_dead_letter_count") == len(required_malformed),
        f"review-pass fixture_skips expected_dead_letter_count must be exactly {len(required_malformed)}",
    )
    expected = details.get("expected")
    _require(isinstance(expected, list) and expected, "review-pass fixture_skips expected evidence must be a non-empty list")

    for requirement in required_malformed:
        source_event_id = requirement["source_event_id"]
        malformed = next(
            (
                item
                for item in expected
                if isinstance(item, dict) and item.get("source_event_id") == source_event_id
            ),
            None,
        )
        _require(
            malformed is not None,
            f"review-pass fixture_skips missing malformed fixture evidence for {source_event_id}",
        )

        mismatches = [
            f"{field}={malformed.get(field)!r}"
            for field, expected_value in requirement["fields"].items()
            if malformed.get(field) != expected_value
        ]
        _require(
            not mismatches,
            f"review-pass fixture_skips malformed evidence for {source_event_id} has invalid field(s): "
            + ", ".join(mismatches),
        )
        error = malformed.get("error")
        error_fragment = requirement["error_fragment"]
        _require(
            isinstance(error, str) and error_fragment in error,
            f"review-pass fixture_skips malformed evidence for {source_event_id} missing {error_fragment} error",
        )


def assert_review_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "review-pass ok must be true")
    _require(payload.get("status") in {"pass", "pass_with_warnings"}, "review-pass status must pass")
    _require(payload.get("source") == "fixtures", "review-pass source must be fixtures")
    _count(payload, "fixture_files", "review-pass")
    _count(payload, "normalized_trades", "review-pass")
    _count(payload, "alerts", "review-pass")

    local_only = _find_check(payload, "local_only")
    _require(local_only is not None, "review-pass missing local_only evidence")
    _require(local_only.get("status") == "pass", "review-pass local_only check must pass")

    fixture_skips = _find_check(payload, "fixture_skips")
    _require(fixture_skips is not None, "review-pass missing fixture_skips evidence")
    _assert_malformed_fixture_skip_evidence(fixture_skips)


def assert_report_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "report ok must be true")
    _require(payload.get("source") == "fixtures", "report source must be fixtures")
    _count(payload, "fixture_count", "report")
    _count(payload, "trade_count", "report")
    _count(payload, "alert_count", "report")
    _assert_positive_breakdown(
        payload,
        "alerts_by_rule",
        (
            "large_trade_absolute_v1",
            "market_relative_large_trade_v1",
            "directional_cluster_v1",
            "open_interest_shock_v1",
        ),
    )
    _assert_positive_breakdown(payload, "alerts_by_severity", ("high", "medium"))
    _assert_positive_breakdown(payload, "alerts_by_confidence", ("low", "medium"))
    _assert_positive_breakdown(payload, "alerts_by_venue", ("kalshi", "polymarket"))
    _assert_cluster_event(payload)


def assert_replay_delivery_output(stdout: str) -> None:
    alerts: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("alert") is True:
            alerts.append(payload)

    _require(alerts, "replay-fixtures emitted no alert JSON")
    for index, alert in enumerate(alerts, start=1):
        missing = [field for field in ("rule_version", "data_quality") if not alert.get(field)]
        _require(
            not missing,
            f"replay-fixtures alert {index} missing delivery field(s): {', '.join(missing)}",
        )


def assert_monitor_output(stdout: str) -> None:
    start = re.search(r"Streaming\s+(\d+)\s+fixture\(s\)\s+\(delay=0(?:\.0)?s\)\.", stdout)
    _require(start is not None, "monitor missing streaming start line")
    start_fixtures = int(start.group(1))
    _require(start_fixtures > 0, "monitor fixture count must be > 0")

    complete = re.search(r"Stream complete:\s+(\d+)\s+alert\(s\)\s+from\s+(\d+)\s+fixture\(s\)\.", stdout)
    _require(complete is not None, "monitor missing parseable stream complete line")
    alert_count = int(complete.group(1))
    complete_fixtures = int(complete.group(2))
    _require(alert_count > 0, "monitor alert count must be > 0")
    _require(complete_fixtures > 0, "monitor complete fixture count must be > 0")
    _require(complete_fixtures == start_fixtures, "monitor fixture counts do not match")
    _require(
        "normalization skipped: invalid decimal for price: 'not-a-number'" in stdout,
        "monitor missing malformed fixture normalization-skip message",
    )

    alerts: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("alert") is True:
            alerts.append(payload)

    _require(alerts, "monitor emitted no alert JSON")
    for index, alert in enumerate(alerts, start=1):
        missing = [field for field in ("rule_version", "data_quality") if not alert.get(field)]
        _require(
            not missing,
            f"monitor alert {index} missing delivery field(s): {', '.join(missing)}",
        )


def assert_live_smoke_output(stdout: str) -> None:
    _require("[live-smoke] subscription: fixture_source=" in stdout, "live-smoke missing fixture-source subscription")
    _require("[live-smoke] done:" in stdout, "live-smoke missing done line")
    _require("event(s) processed" in stdout and "captured" in stdout, "live-smoke missing processed/captured text")
    match = re.search(r"\[live-smoke\] done:\s+(\d+) event\(s\) processed,\s+(\d+) captured", stdout)
    _require(match is not None, "live-smoke done line is not parseable")
    processed = int(match.group(1))
    captured = int(match.group(2))
    _require(processed > 0, "live-smoke processed count must be > 0")
    _require(captured > 0, "live-smoke captured count must be > 0")


def _require_lifecycle_details(payload: dict, name: str) -> dict:
    check = _require_check_status(payload, "lifecycle-smoke", name, "pass")
    details = check.get("details")
    _require(isinstance(details, dict), f"lifecycle-smoke {name} details must be an object")
    return details


def assert_lifecycle_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "lifecycle-smoke ok must be true")
    _require(payload.get("status") == "pass", "lifecycle-smoke status must be pass")
    _require(
        payload.get("source") == "db_free_runner_contracts",
        "lifecycle-smoke source must be db_free_runner_contracts",
    )

    replay = _require_lifecycle_details(payload, "raw_event_replay_dedupe")
    _require(replay.get("first_run_consumed") == 1, "lifecycle-smoke first_run_consumed must be 1")
    _require(replay.get("second_run_consumed") == 2, "lifecycle-smoke second_run_consumed must be 2")
    _require(replay.get("raw_insert_attempts") == 3, "lifecycle-smoke raw_insert_attempts must be 3")
    _require(
        replay.get("normalized_source_ids") == ["restart-1", "restart-2"],
        "lifecycle-smoke normalized_source_ids must prove restart dedupe",
    )

    duplicate = _require_lifecycle_details(payload, "duplicate_trade_skip")
    _require(duplicate.get("normalized_events") == 1, "lifecycle-smoke normalized_events must be 1")
    _require(duplicate.get("insert_trade_calls") == 1, "lifecycle-smoke insert_trade_calls must be 1")
    _require(duplicate.get("alerts_inserted") == 0, "lifecycle-smoke duplicate alerts_inserted must be 0")
    _require(duplicate.get("alerts_delivered") == 0, "lifecycle-smoke alerts_delivered must be 0")

    suppression = _require_lifecycle_details(payload, "suppression_cache_seed")
    _require(suppression.get("raw_events_seen") == 1, "lifecycle-smoke suppression raw_events_seen must be 1")
    _require(suppression.get("process_event_calls") == 1, "lifecycle-smoke process_event_calls must be 1")
    _require(suppression.get("seeded_entries") == 1, "lifecycle-smoke seeded_entries must be 1")

    expiry = _require_lifecycle_details(payload, "suppression_window_expiry")
    _require(
        expiry.get("raw_events_seen") == 3,
        "lifecycle-smoke suppression_window_expiry raw_events_seen must be 3",
    )
    _require(
        expiry.get("alerts_inserted") == 2,
        "lifecycle-smoke suppression_window_expiry alerts_inserted must be 2",
    )
    _require(
        expiry.get("alerts_delivered") == 2,
        "lifecycle-smoke suppression_window_expiry alerts_delivered must be 2",
    )
    _require(
        expiry.get("alerts_suppressed") == 1,
        "lifecycle-smoke suppression_window_expiry alerts_suppressed must be 1",
    )
    _require(
        expiry.get("suppression_window_seconds") == 300,
        "lifecycle-smoke suppression_window_expiry suppression_window_seconds must be 300",
    )

    non_trade = _require_lifecycle_details(payload, "non_trade_raw_persistence")
    _require(non_trade.get("raw_events_seen") == 1, "lifecycle-smoke non-trade raw_events_seen must be 1")
    _require(non_trade.get("raw_events_inserted") == 1, "lifecycle-smoke raw_events_inserted must be 1")
    _require(non_trade.get("non_trade_skips") == 1, "lifecycle-smoke non_trade_skips must be 1")
    _require(non_trade.get("alerts_inserted") == 0, "lifecycle-smoke non-trade alerts_inserted must be 0")
    _require(non_trade.get("alerts_delivered") == 0, "lifecycle-smoke non-trade alerts_delivered must be 0")

    kalshi_overlap = _require_lifecycle_details(payload, "kalshi_rest_poll_overlap_dedupe")
    _require(
        kalshi_overlap.get("ticker") == "KXOVERLAP-26JUN",
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe ticker mismatch",
    )
    _require(
        kalshi_overlap.get("poll_windows") == [["t1", "t2"], ["t2", "t3"]],
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe poll_windows mismatch",
    )
    _require(
        kalshi_overlap.get("raw_events_seen") == 4,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe raw_events_seen must be 4",
    )
    _require(
        kalshi_overlap.get("raw_events_inserted") == 3,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe raw_events_inserted must be 3",
    )
    _require(
        kalshi_overlap.get("raw_event_duplicates") == 1,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe raw_event_duplicates must be 1",
    )
    _require(
        kalshi_overlap.get("normalized_trades_inserted") == 3,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe normalized_trades_inserted must be 3",
    )
    _require(
        kalshi_overlap.get("duplicate_trades") == 0,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe duplicate_trades must be 0",
    )
    _require(
        kalshi_overlap.get("metrics_upserted") == 3,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe metrics_upserted must be 3",
    )
    _require(
        kalshi_overlap.get("alerts_inserted") == 3,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe alerts_inserted must be 3",
    )
    _require(
        kalshi_overlap.get("alerts_delivered") == 3,
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe alerts_delivered must be 3",
    )
    _require(
        kalshi_overlap.get("source_event_ids") == ["t1", "t2", "t3"],
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe source_event_ids must be t1,t2,t3",
    )
    _require(
        kalshi_overlap.get("venue_trade_ids") == ["t1", "t2", "t3"],
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe venue_trade_ids must be t1,t2,t3",
    )
    _require(
        kalshi_overlap.get("source_channels") == ["rest_trades"],
        "lifecycle-smoke kalshi_rest_poll_overlap_dedupe source_channels must be rest_trades",
    )


def assert_lifecycle_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"lifecycle-smoke failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    assert_lifecycle_payload(_parse_json("lifecycle-smoke", result))


EXPECTED_BASELINE_SMOKE_CHECKS = (
    "compute_path_uses_normalized_trades",
    "baseline_upsert_conflict_constraint",
    "baseline_available_alert",
    "baseline_stale_alert",
    "baseline_missing_alert",
    "volume_spike_uses_prior_history",
)


def assert_baseline_smoke_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "baseline-smoke ok must be true")
    _require(payload.get("status") == "pass", "baseline-smoke status must be pass")
    _require(
        payload.get("source") == "db_free_baseline_contracts",
        "baseline-smoke source must be db_free_baseline_contracts",
    )
    checks = payload.get("checks")
    _require(isinstance(checks, list), "baseline-smoke checks must be a list")
    names = []
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"baseline-smoke check {index} must be an object")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"baseline-smoke check {index} missing name")
        names.append(name)
        _require(check.get("status") == "pass", f"baseline-smoke {name} check must pass")
        _require(isinstance(check.get("details"), dict), f"baseline-smoke {name} details must be an object")
    _require(
        tuple(names) == EXPECTED_BASELINE_SMOKE_CHECKS,
        "baseline-smoke checks must match exactly: " + ", ".join(EXPECTED_BASELINE_SMOKE_CHECKS),
    )


def assert_baseline_smoke_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"baseline-smoke failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    assert_baseline_smoke_payload(_parse_json("baseline-smoke", result))


EXPECTED_SCOPE_SMOKE_CHECKS = (
    "authority_docs",
    "hosted_saas_markers_absent",
    "order_execution_markers_absent",
    "platform_scaffold_paths_absent",
    "config_defaults_local",
    "localhost_http_endpoint_validation",
    "default_tests_offline",
    "github_workflow_absent",
)


EXPECTED_AUTOSTART_SMOKE_CHECKS = (
    "dry_run_plan_shape",
    "absolute_path_safety",
    "repo_local_log_path",
    "db_recovery_instructions",
    "docker_postgres_dependency_warning",
    "missing_status_idempotent",
    "missing_uninstall_idempotent",
)


def assert_scope_smoke_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "scope-smoke ok must be true")
    _require(payload.get("status") == "pass", "scope-smoke status must be pass")
    _require(payload.get("source") == "local_scope_contracts", "scope-smoke source must be local_scope_contracts")
    checks = payload.get("checks")
    _require(isinstance(checks, list), "scope-smoke checks must be a list")
    names = []
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"scope-smoke check {index} must be an object")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"scope-smoke check {index} missing name")
        names.append(name)
        _require(check.get("status") == "pass", f"scope-smoke {name} check must pass")
    _require(
        tuple(names) == EXPECTED_SCOPE_SMOKE_CHECKS,
        "scope-smoke checks must match exactly: " + ", ".join(EXPECTED_SCOPE_SMOKE_CHECKS),
    )


def assert_scope_smoke_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"scope-smoke failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    assert_scope_smoke_payload(_parse_json("scope-smoke", result))


def assert_autostart_smoke_payload(payload: dict) -> None:
    _require(payload.get("ok") is True, "autostart-smoke ok must be true")
    _require(payload.get("status") == "pass", "autostart-smoke status must be pass")
    _require(
        payload.get("source") == "db_free_autostart_contracts",
        "autostart-smoke source must be db_free_autostart_contracts",
    )
    checks = payload.get("checks")
    _require(isinstance(checks, list), "autostart-smoke checks must be a list")
    names = []
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"autostart-smoke check {index} must be an object")
        name = check.get("name")
        _require(isinstance(name, str) and name, f"autostart-smoke check {index} missing name")
        names.append(name)
        _require(check.get("status") == "pass", f"autostart-smoke {name} check must pass")
        _require(isinstance(check.get("details"), dict), f"autostart-smoke {name} details must be an object")
    _require(
        tuple(names) == EXPECTED_AUTOSTART_SMOKE_CHECKS,
        "autostart-smoke checks must match exactly: " + ", ".join(EXPECTED_AUTOSTART_SMOKE_CHECKS),
    )


def assert_autostart_smoke_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"autostart-smoke failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    assert_autostart_smoke_payload(_parse_json("autostart-smoke", result))


def assert_setup_smoke_result(result: CommandResult) -> None:
    if result.returncode != 0:
        raise RuntimeError(
            f"setup-smoke failed with exit {result.returncode}: {' '.join(result.args)}\n"
            f"stdout: {result.stdout.strip() or '<empty>'}\n"
            f"stderr: {result.stderr.strip() or '<empty>'}"
        )
    validate_setup_payload(_parse_json("setup-smoke", result))


def run_operator_smoke(runner: Runner = run_command) -> None:
    review = _run_required(
        "review-pass",
        [sys.executable, "-m", "pmfi.cli", "review-pass", "--format", "json"],
        runner,
    )
    assert_review_payload(_parse_json("review-pass", review))

    report = _run_required(
        "report-fixtures",
        [
            sys.executable,
            "-m",
            "pmfi.cli",
            "report",
            "--source",
            "fixtures",
            "--fixture-dir",
            "tests/fixtures/raw",
            "--format",
            "json",
        ],
        runner,
    )
    assert_report_payload(_parse_json("report-fixtures", report))

    status = _run_required(
        "status-json",
        [sys.executable, "-m", "pmfi.cli", "status", "--format", "json"],
        runner,
    )
    assert_status_result(status)

    setup_smoke = _run_required(
        "setup-smoke",
        [sys.executable, "scripts/setup_smoke.py", "--format", "json"],
        runner,
    )
    assert_setup_smoke_result(setup_smoke)

    _run_health(
        [sys.executable, "-m", "pmfi.cli", "health", "--format", "json"],
        runner,
    )

    _run_db_inspection(
        "dead-letters",
        [sys.executable, "-m", "pmfi.cli", "dead-letters", "--format", "json"],
        runner,
        assert_dead_letters_result,
    )

    _run_db_inspection(
        "data-quality-incidents",
        [sys.executable, "-m", "pmfi.cli", "data-quality-incidents", "--format", "json"],
        runner,
        assert_data_quality_incidents_result,
    )

    _run_db_inspection(
        "delivery-failures",
        [sys.executable, "-m", "pmfi.cli", "delivery-failures", "--format", "json"],
        runner,
        assert_delivery_failures_result,
    )

    _run_db_inspection(
        "alerts-list",
        [sys.executable, "-m", "pmfi.cli", "alerts", "list", "--format", "json"],
        runner,
        assert_alerts_list_result,
    )

    _run_db_inspection(
        "alerts-reviews",
        [sys.executable, "-m", "pmfi.cli", "alerts", "reviews", "--format", "json"],
        runner,
        assert_alert_reviews_result,
    )

    _run_db_inspection(
        "alerts-fp-rate",
        [sys.executable, "-m", "pmfi.cli", "alerts", "fp-rate", "--format", "json"],
        runner,
        assert_alert_fp_rate_result,
    )

    _run_ingest_check(
        [
            sys.executable,
            "-m",
            "pmfi.cli",
            "ingest",
            "--venue",
            "kalshi",
            "--check",
            "--format",
            "json",
        ],
        runner,
    )

    replay = _run_required(
        "replay-fixtures",
        [
            sys.executable,
            "-m",
            "pmfi.cli",
            "replay-fixtures",
            "--fixture-dir",
            "tests/fixtures/raw",
        ],
        runner,
    )
    assert_replay_delivery_output(replay.stdout)

    monitor = _run_required(
        "monitor-fixture-replay",
        [
            sys.executable,
            "-m",
            "pmfi.cli",
            "monitor",
            "--fixture-replay",
            "--fixture-dir",
            "tests/fixtures/raw",
            "--delay",
            "0",
        ],
        runner,
    )
    assert_monitor_output(monitor.stdout)

    live_smoke = _run_required(
        "live-smoke-fixture",
        [
            sys.executable,
            "-m",
            "pmfi.cli",
            "live-smoke",
            "--fixture-source",
            "tests/fixtures/raw/kalshi_trade.json",
            "--force",
            "--venue",
            "kalshi",
            "--max-events",
            "1",
        ],
        runner,
    )
    assert_live_smoke_output(live_smoke.stdout)

    lifecycle = _run_required(
        "lifecycle-smoke",
        [sys.executable, "scripts/lifecycle_smoke.py", "--format", "json"],
        runner,
    )
    assert_lifecycle_result(lifecycle)

    baseline = _run_required(
        "baseline-smoke",
        [sys.executable, "scripts/baseline_smoke.py", "--format", "json"],
        runner,
    )
    assert_baseline_smoke_result(baseline)

    scope = _run_required(
        "scope-smoke",
        [sys.executable, "scripts/scope_smoke.py", "--format", "json"],
        runner,
    )
    assert_scope_smoke_result(scope)

    autostart_smoke = _run_required(
        "autostart-smoke",
        [sys.executable, "scripts/autostart_smoke.py", "--format", "json"],
        runner,
    )
    assert_autostart_smoke_result(autostart_smoke)


def main() -> int:
    try:
        run_operator_smoke()
    except RuntimeError as exc:
        print(f"operator smoke failed: {exc}", file=sys.stderr)
        return 1
    print("operator smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
