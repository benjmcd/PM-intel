from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from pmfi.commands._shared import ROOT
from pmfi.qualification.evidence import (
    evidence_contains_secret,
    sanitize_git_remote,
    schema_fingerprint,
)

DEFAULT_MANIFEST = ROOT / "tests" / "qualification" / "alert_precision_manifest.yaml"
METRIC_NAME = "precision_at_proxy"
PROXY_DEFINITION = (
    "For each alert, compare the first normalized_trades.price observed at or after fired_at "
    "with the last normalized_trades.price observed after that start point and within the "
    "forward window. A proxy hit means abs(price_delta) >= threshold. Alerts without both "
    "price observations are INSUFFICIENT and excluded from the denominator."
)


def load_alert_precision_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("alert precision manifest must be a mapping")
    return data


def _git_value(args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value or None


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _jsonable_decimal(value: Decimal) -> int | float:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return int(normalized)
    return float(value)


def _classify_alert(
    alert: dict[str, Any],
    market_prices: list[dict[str, Any]],
    *,
    window_seconds: int,
    threshold: Decimal,
) -> dict[str, Any]:
    fired_at = _as_utc(alert["fired_at"])
    end_at = fired_at + timedelta(seconds=window_seconds)
    start = next(
        (
            price
            for price in market_prices
            if _as_utc(price["ts"]) >= fired_at and _as_utc(price["ts"]) <= end_at
        ),
        None,
    )
    if start is None:
        return {"status": "INSUFFICIENT", "reason": "missing_start_price"}

    start_ts = _as_utc(start["ts"])
    end_candidates = [
        price
        for price in market_prices
        if _as_utc(price["ts"]) > start_ts and _as_utc(price["ts"]) <= end_at
    ]
    if not end_candidates:
        return {"status": "INSUFFICIENT", "reason": "missing_forward_price"}

    end = end_candidates[-1]
    start_price = _as_decimal(start["price"])
    end_price = _as_decimal(end["price"])
    delta = end_price - start_price
    return {
        "status": "HIT" if abs(delta) >= threshold else "MISS",
        "start_price": _jsonable_decimal(start_price),
        "end_price": _jsonable_decimal(end_price),
        "price_delta": float(delta),
    }


def _empty_counts(rule_key: str, window_seconds: int, threshold: Decimal) -> dict[str, Any]:
    return {
        "rule_key": rule_key,
        "window_seconds": int(window_seconds),
        "threshold": _jsonable_decimal(threshold),
        "alerts": 0,
        "scorable_alerts": 0,
        "proxy_hits": 0,
        "proxy_misses": 0,
        "insufficient_alerts": 0,
        "precision_at_proxy": None,
    }


def score_alert_precision(
    alerts: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    *,
    windows_seconds: list[int],
    thresholds: list[Decimal],
) -> dict[str, Any]:
    prices_by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in prices:
        prices_by_market[str(row["market_id"])].append(row)
    for rows in prices_by_market.values():
        rows.sort(key=lambda item: _as_utc(item["ts"]))

    rules = sorted({str(alert["rule_key"]) for alert in alerts})
    grid_rows: list[dict[str, Any]] = []
    classified_count = 0
    for rule_key in rules:
        rule_alerts = [alert for alert in alerts if str(alert["rule_key"]) == rule_key]
        for window_seconds in windows_seconds:
            for threshold in thresholds:
                row = _empty_counts(rule_key, int(window_seconds), threshold)
                for alert in rule_alerts:
                    row["alerts"] += 1
                    classified_count += 1
                    market_prices = prices_by_market.get(str(alert["market_id"]), [])
                    classification = _classify_alert(
                        alert,
                        market_prices,
                        window_seconds=int(window_seconds),
                        threshold=threshold,
                    )
                    if classification["status"] == "INSUFFICIENT":
                        row["insufficient_alerts"] += 1
                    elif classification["status"] == "HIT":
                        row["scorable_alerts"] += 1
                        row["proxy_hits"] += 1
                    else:
                        row["scorable_alerts"] += 1
                        row["proxy_misses"] += 1
                if row["scorable_alerts"]:
                    row["precision_at_proxy"] = round(
                        row["proxy_hits"] / row["scorable_alerts"],
                        6,
                    )
                grid_rows.append(row)

    scorable = sum(int(row["scorable_alerts"]) for row in grid_rows)
    hits = sum(int(row["proxy_hits"]) for row in grid_rows)
    return {
        "metric_name": METRIC_NAME,
        "price_source": "normalized_trades",
        "proxy_definition": PROXY_DEFINITION,
        "alert_count": len(alerts),
        "price_observation_count": len(prices),
        "rule_count": len(rules),
        "grid_cell_count": len(grid_rows),
        "classified_alert_grid_rows": classified_count,
        "scorable_alerts": scorable,
        "proxy_hits": hits,
        "proxy_misses": sum(int(row["proxy_misses"]) for row in grid_rows),
        "insufficient_alerts": sum(int(row["insufficient_alerts"]) for row in grid_rows),
        "overall_precision_at_proxy_pooled_over_grid": round(hits / scorable, 6) if scorable else None,
        "overall_precision_note": (
            "overall_precision_at_proxy_pooled_over_grid pools alert evaluations across every "
            "window-threshold grid cell; it is not a per-alert hit rate"
        ),
        "proxy_thresholds_positive": all(threshold > 0 for threshold in thresholds),
        "per_rule_grid": grid_rows,
        "no_secrets_in_fixtures_logs_or_evidence": False,
    }


def evaluate_alert_precision_pass_invariants(measurements: dict[str, Any]) -> dict[str, bool]:
    per_rule = measurements.get("per_rule_grid") or []
    classified = int(measurements.get("classified_alert_grid_rows") or 0)
    expected_classified = sum(int(row.get("alerts") or 0) for row in per_rule)
    counts_balance = all(
        int(row.get("alerts") or 0)
        == int(row.get("scorable_alerts") or 0) + int(row.get("insufficient_alerts") or 0)
        for row in per_rule
    )
    return {
        "proxy_metric_is_explicit": measurements.get("metric_name") == METRIC_NAME,
        "grid_has_rule_cells": int(measurements.get("grid_cell_count") or 0) > 0,
        "alerts_were_measured": int(measurements.get("alert_count") or 0) > 0,
        "proxy_denominator_has_scorable_alerts": int(measurements.get("scorable_alerts") or 0) > 0,
        "proxy_thresholds_are_positive": bool(measurements.get("proxy_thresholds_positive")),
        "counts_balance_alerts_equals_scorable_plus_insufficient": counts_balance,
        "classified_alert_grid_rows_match": classified == expected_classified,
        "no_secrets_in_fixtures_logs_or_evidence": bool(
            measurements.get("no_secrets_in_fixtures_logs_or_evidence")
        ),
    }


def recommend_alert_precision_actions(measurements: dict[str, Any]) -> dict[str, Any]:
    best_by_rule: dict[str, dict[str, Any]] = {}
    for row in measurements.get("per_rule_grid") or []:
        if not row.get("scorable_alerts"):
            continue
        current = best_by_rule.get(str(row["rule_key"]))
        current_key = (
            float(current["precision_at_proxy"]),
            int(current["scorable_alerts"]),
        ) if current else (-1.0, -1)
        next_key = (
            float(row["precision_at_proxy"]),
            int(row["scorable_alerts"]),
        )
        if next_key > current_key:
            best_by_rule[str(row["rule_key"])] = dict(row)
    return {
        "mode": "recommend_only",
        "mutates_rules": False,
        "metric": METRIC_NAME,
        "rule_cells_to_review": list(best_by_rule.values()),
        "rationale": (
            "proxy cells identify where forward price movement followed historical alerts; "
            "this is not labeled precision and does not change alert rules or thresholds"
        ),
    }


def build_alert_precision_evidence(
    *,
    manifest: dict[str, Any],
    manifest_path: Path,
    measurements: dict[str, Any],
    actual_facets: list[str],
    commands: list[str],
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    manifest_path = Path(manifest_path)
    measured = dict(measurements)
    measured["no_secrets_in_fixtures_logs_or_evidence"] = False
    evidence: dict[str, Any] = {
        "version": "pmfi-data-plane-scenario-run.v1",
        "scenario_id": manifest["scenario_id"],
        "scenario_version": manifest["scenario_version"],
        "profile": manifest["profile"],
        "outcome": "PASS",
        "completeness_classifications": {
            "precision": "PROXY_BACKTEST_LOCAL",
            "operator_labeled_truth": "ACCEPTED_DEBT",
            "causal_attribution": "ACCEPTED_DEBT",
        },
        "repository": {
            "remote": sanitize_git_remote(_git_value(["config", "--get", "remote.origin.url"])),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "worktree_status": "not_recorded_by_alert_eval",
        },
        "runtime": {
            "python_version": platform.python_version(),
            "schema_version": schema_fingerprint(ROOT / "sql"),
            "environment": "offline_db_gated",
        },
        "time": {
            "started_at": started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
        },
        "expected_truth": {
            "manifest": _manifest_rel(manifest_path),
            "artifact_hash": _sha256_path(manifest_path),
            "proxy_definition": PROXY_DEFINITION,
        },
        "evidence": {
            "required_facets": list(manifest.get("required_facets", [])),
            "actual_facets": list(actual_facets),
            "deferred_facets": [
                item["facet"] for item in manifest.get("manual_deferred_facets", [])
            ],
            "commands": list(commands),
            "artifacts": [_manifest_rel(manifest_path)],
            "artifact_hashes": [_sha256_path(manifest_path)],
        },
        "measurements": measured,
        "recommended_actions": recommend_alert_precision_actions(measured),
        "pass_invariants": {},
        "fail_conditions": [],
        "incidents": {"unresolved_p0": [], "unresolved_p1": []},
        "accepted_debt": list(manifest.get("manual_deferred_facets", [])),
        "next_action": "orchestrator_verify_pr",
    }
    measured["no_secrets_in_fixtures_logs_or_evidence"] = not evidence_contains_secret(
        manifest_path,
        evidence,
    )
    invariants = evaluate_alert_precision_pass_invariants(measured)
    evidence["measurements"] = measured
    evidence["recommended_actions"] = recommend_alert_precision_actions(measured)
    evidence["pass_invariants"] = invariants
    if not all(invariants.values()):
        evidence["outcome"] = "FAIL"
        evidence["fail_conditions"] = [
            key for key, value in invariants.items() if value is not True
        ]
    return evidence


def _grid_from_manifest(manifest: dict[str, Any]) -> tuple[list[int], list[Decimal]]:
    grid = manifest.get("grid") or {}
    windows = [int(value) for value in grid.get("windows_seconds") or []]
    thresholds = [_as_decimal(value) for value in grid.get("thresholds") or []]
    if not windows:
        raise ValueError("alert precision manifest grid.windows_seconds must not be empty")
    if not thresholds:
        raise ValueError("alert precision manifest grid.thresholds must not be empty")
    if any(threshold <= 0 for threshold in thresholds):
        raise ValueError("alert precision manifest grid.thresholds must be > 0")
    return windows, thresholds


async def _fetch_alerts(pool: Any, *, limit: int | None) -> list[dict[str, Any]]:
    limit_clause = ""
    args: list[Any] = []
    if limit and limit > 0:
        limit_clause = " LIMIT $1"
        args.append(int(limit))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT alert_id::text AS alert_id,
                       market_id::text AS market_id,
                       rule_key,
                       fired_at
                FROM alerts
                WHERE market_id IS NOT NULL
                ORDER BY fired_at, alert_id{limit_clause}""",
            *args,
        )
    return [dict(row) for row in rows]


async def _fetch_prices(
    pool: Any,
    *,
    market_ids: list[str],
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    if not market_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT market_id::text AS market_id,
                      received_at AS ts,
                      price
               FROM normalized_trades
               WHERE market_id = ANY($1::uuid[])
                 AND received_at >= $2
                 AND received_at <= $3
               ORDER BY market_id, received_at""",
            market_ids,
            start_at,
            end_at,
        )
    return [dict(row) for row in rows]


async def run_alert_precision_measurement(
    pool: Any,
    manifest_path: Path = DEFAULT_MANIFEST,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    manifest = load_alert_precision_manifest(manifest_path)
    windows, thresholds = _grid_from_manifest(manifest)
    alerts = await _fetch_alerts(pool, limit=limit)
    if alerts:
        start_at = min(_as_utc(alert["fired_at"]) for alert in alerts)
        end_at = max(_as_utc(alert["fired_at"]) for alert in alerts) + timedelta(seconds=max(windows))
        market_ids = sorted({str(alert["market_id"]) for alert in alerts})
        prices = await _fetch_prices(pool, market_ids=market_ids, start_at=start_at, end_at=end_at)
    else:
        prices = []
    measurements = score_alert_precision(
        alerts,
        prices,
        windows_seconds=windows,
        thresholds=thresholds,
    )
    return build_alert_precision_evidence(
        manifest=manifest,
        manifest_path=manifest_path,
        measurements=measurements,
        actual_facets=["OFFLINE", "POSTGRES_INTEGRATION", "READ_ONLY_PRIMARY"],
        commands=[
            "pmfi alert-eval --format json",
            "python -m pytest -q tests\\test_alert_precision_db.py",
        ],
    )
