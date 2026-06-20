from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pmfi.data_reports import (
    build_volume_spike_sensitivity_rows,
    format_backtest_analytics_text,
    format_data_coverage_text,
    summarize_backtest_analytics,
    summarize_data_coverage_rows,
)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _parse_window_time(label: str, raw: str | None) -> tuple[datetime | None, str | None]:
    if raw is None:
        return None, None
    value = raw.strip()
    if not value:
        return None, f"{label} must not be empty"

    match = re.fullmatch(r"(\d+)([mhd])", value.lower())
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            return None, f"{label} relative window must be greater than zero"
        seconds = {"m": 60, "h": 3600, "d": 86400}[match.group(2)] * amount
        return datetime.now(timezone.utc) - timedelta(seconds=seconds), None

    iso_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        return None, f"{label} must be ISO-8601 or a relative value like 24h"
    if parsed.tzinfo is None:
        return None, f"{label} must include a timezone offset"
    return parsed.astimezone(timezone.utc), None


def _load_rules_config() -> dict[str, Any]:
    import yaml
    from pmfi.commands._shared import ROOT

    rules_path = ROOT / "config" / "alert_rules.yaml"
    return yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}


def _volume_spike_min_trade_usd(rules_config: dict[str, Any]) -> float:
    rules = rules_config.get("rules") or {}
    volume_spike = rules.get("volume_spike_v1") or {}
    return float(volume_spike.get("min_trade_usd", 0.0) or 0.0)


def _volume_spike_sweep_values(raw_values: list[float] | None, current: float) -> list[float]:
    if raw_values:
        values = set(float(value) for value in raw_values)
        values.add(float(current))
        return sorted(values)
    candidates = {float(current)}
    if current > 250:
        candidates.add(float(current - 250))
    candidates.add(float(current + 150))
    candidates.add(float(current + 650))
    return sorted(value for value in candidates if value > 0)


def _rules_config_with_volume_floor(rules_config: dict[str, Any], value: float) -> dict[str, Any]:
    candidate = copy.deepcopy(rules_config)
    rules = candidate.setdefault("rules", {})
    volume_spike = rules.setdefault("volume_spike_v1", {})
    volume_spike["min_trade_usd"] = float(value)
    return candidate

async def fetch_data_coverage_rows(
    pool: object,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    venue: str | None = None,
) -> list[Any]:
    conditions: list[str] = []
    params: list[Any] = []

    def _add(expr: str, value: Any) -> None:
        params.append(value)
        conditions.append(expr.replace("?", f"${len(params)}"))

    if since is not None:
        _add("re.received_at >= ?", since)
    if until is not None:
        _add("re.received_at <= ?", until)
    if venue:
        _add("re.venue_code = ?", venue)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        WITH normalized_raw AS (
            SELECT DISTINCT raw_event_id
            FROM normalized_trades
            WHERE raw_event_id IS NOT NULL
        ),
        dead_letter_raw AS (
            SELECT DISTINCT raw_event_id
            FROM dead_letters
            WHERE raw_event_id IS NOT NULL
        ),
        raw_dispositions AS (
            SELECT
                re.venue_code,
                re.source_event_type,
                (nr.raw_event_id IS NOT NULL) AS has_normalized,
                (dl.raw_event_id IS NOT NULL) AS has_dead_letter,
                (
                    re.venue_code = 'polymarket'
                    AND (
                        COALESCE(re.venue_market_id, '') LIKE 'pm-%'
                        OR COALESCE(re.payload->>'market', '') LIKE 'pm-%'
                    )
                ) AS is_synthetic
            FROM raw_events re
            LEFT JOIN normalized_raw nr ON nr.raw_event_id = re.raw_event_id
            LEFT JOIN dead_letter_raw dl ON dl.raw_event_id = re.raw_event_id
            {where_sql}
        )
        SELECT
            venue_code,
            source_event_type,
            has_normalized,
            has_dead_letter,
            is_synthetic,
            COUNT(*) AS cnt
        FROM raw_dispositions
        GROUP BY venue_code, source_event_type, has_normalized, has_dead_letter, is_synthetic
        ORDER BY venue_code, source_event_type, has_normalized DESC, has_dead_letter DESC, is_synthetic
    """
    return list(await pool.fetch(sql, *params))  # type: ignore[attr-defined]


async def fetch_dead_letter_reconciliation(
    pool: object,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    venue: str | None = None,
) -> dict[str, int]:
    conditions: list[str] = []
    params: list[Any] = []

    def _add(expr: str, value: Any) -> None:
        params.append(value)
        conditions.append(expr.replace("?", f"${len(params)}"))

    if since is not None:
        _add("dl.created_at >= ?", since)
    if until is not None:
        _add("dl.created_at <= ?", until)
    if venue:
        _add("dl.venue_code = ?", venue)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    row = await pool.fetchrow(  # type: ignore[attr-defined]
        f"""
        SELECT
            COUNT(*)::bigint AS total_dead_letters,
            COUNT(*) FILTER (
                WHERE raw_event_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1
                      FROM raw_events re
                      WHERE re.raw_event_id = dl.raw_event_id
                  )
            )::bigint AS linked_dead_letters,
            COUNT(*) FILTER (
                WHERE raw_event_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                       FROM raw_events re
                       WHERE re.raw_event_id = dl.raw_event_id
                   )
            )::bigint AS unlinked_dead_letters
        FROM dead_letters dl
        {where_sql}
        """,
        *params,
    )
    if row is None:
        return {
            "total_dead_letters": 0,
            "linked_dead_letters": 0,
            "unlinked_dead_letters": 0,
        }
    return {
        "total_dead_letters": int(row["total_dead_letters"] or 0),
        "linked_dead_letters": int(row["linked_dead_letters"] or 0),
        "unlinked_dead_letters": int(row["unlinked_dead_letters"] or 0),
    }


def cmd_data_coverage(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    import asyncpg

    since, since_error = _parse_window_time("--since", getattr(args, "since", None))
    if since_error:
        print(f"[data-coverage] {since_error}")
        return 1
    until, until_error = _parse_window_time("--until", getattr(args, "until", None))
    if until_error:
        print(f"[data-coverage] {until_error}")
        return 1
    if since is not None and until is not None and since >= until:
        print("[data-coverage] --since must be before --until.")
        return 1

    async def _run() -> tuple[dict[str, Any] | None, str | None]:
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            rows = await fetch_data_coverage_rows(
                pool,
                since=since,
                until=until,
                venue=getattr(args, "venue", None),
            )
            dead_letter_reconciliation = await fetch_dead_letter_reconciliation(
                pool,
                since=since,
                until=until,
                venue=getattr(args, "venue", None),
            )
            exclude_synthetic = not bool(getattr(args, "include_synthetic", False))
            report = summarize_data_coverage_rows(
                rows,
                exclude_synthetic=exclude_synthetic,
                dead_letter_reconciliation=dead_letter_reconciliation,
            )
            report["filters"] = {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "venue": getattr(args, "venue", None),
                "exclude_synthetic": exclude_synthetic,
            }
            return report, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    report, error = asyncio.run(_run())
    if error:
        print(f"[data-coverage] DB query failed: {error}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert report is not None

    if getattr(args, "format", "text") == "json":
        print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    else:
        print(format_data_coverage_text(report))
    return 1 if report["has_unaccounted_warning"] else 0


async def fetch_latest_review_index(
    pool: object,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    venue: str | None = None,
) -> dict[tuple[int, str], str]:
    conditions: list[str] = ["a.raw_event_id IS NOT NULL"]
    params: list[Any] = []

    def _add(expr: str, value: Any) -> None:
        params.append(value)
        conditions.append(expr.replace("?", f"${len(params)}"))

    if since is not None:
        _add("a.fired_at >= ?", since)
    if until is not None:
        _add("a.fired_at <= ?", until)
    if venue:
        _add("a.venue_code = ?", venue)

    where_sql = "WHERE " + " AND ".join(conditions)
    sql = f"""
        WITH latest_reviews AS (
            SELECT DISTINCT ON (ar.alert_id)
                ar.alert_id,
                ar.label,
                ar.reviewed_at
            FROM alert_reviews ar
            ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
        )
        SELECT a.raw_event_id, a.rule_key, lr.label
        FROM latest_reviews lr
        JOIN alerts a ON a.alert_id = lr.alert_id
        {where_sql}
        ORDER BY a.raw_event_id, a.rule_key
    """
    rows = await pool.fetch(sql, *params)  # type: ignore[attr-defined]
    index: dict[tuple[int, str], str] = {}
    for row in rows:
        raw_event_id = row["raw_event_id"]
        rule_key = row["rule_key"]
        label = row["label"]
        if raw_event_id is None or not rule_key or label not in {"tp", "fp", "noise"}:
            continue
        index[(int(raw_event_id), str(rule_key))] = str(label)
    return index


def cmd_backtest_analytics(args: argparse.Namespace) -> int:
    from pmfi.commands.alerts import _load_rule_fp_rate_min_reviewed, _load_rule_fp_rate_targets
    from pmfi.config import load_config
    import asyncpg

    start_ts, start_error = _parse_window_time("--from", getattr(args, "backtest_from", None))
    if start_error:
        print(f"[backtest-analytics] {start_error}")
        return 1
    end_ts, end_error = _parse_window_time("--to", getattr(args, "backtest_to", None))
    if end_error:
        print(f"[backtest-analytics] {end_error}")
        return 1
    if start_ts is not None and end_ts is not None and start_ts >= end_ts:
        print("[backtest-analytics] --from must be before --to.")
        return 1

    targets, target_error = _load_rule_fp_rate_targets()
    if target_error:
        print(f"[backtest-analytics] {target_error}")
        return 1
    min_reviewed_by_rule, min_reviewed_error = _load_rule_fp_rate_min_reviewed()
    if min_reviewed_error:
        print(f"[backtest-analytics] {min_reviewed_error}")
        return 1
    rules_config = _load_rules_config()
    current_min_trade_usd = _volume_spike_min_trade_usd(rules_config)
    sweep_values = _volume_spike_sweep_values(
        getattr(args, "volume_spike_min_trade_usd", None),
        current_min_trade_usd,
    )

    async def _run() -> tuple[dict[str, Any] | None, str | None]:
        from pmfi.replay import replay_from_db

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url,
                min_size=1,
                max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            replay_kwargs = {
                "limit": int(getattr(args, "limit", 0)),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "venue": getattr(args, "backtest_venue", None),
                "market": getattr(args, "backtest_market", None),
                "persist": False,
                "seed": not bool(getattr(args, "cold_start", False)),
                "print_summary": False,
                "normalized_only": True,
            }
            review_index = await fetch_latest_review_index(
                pool,
                since=start_ts,
                until=end_ts,
                venue=getattr(args, "backtest_venue", None),
            )
            current_results = await replay_from_db(pool, **replay_kwargs)
            current_summary = summarize_backtest_analytics(
                current_results,
                review_index=review_index,
                fp_rate_targets=targets,
                min_reviewed_by_rule=min_reviewed_by_rule,
            )
            candidate_summaries: list[dict[str, Any]] = []
            for value in sweep_values:
                if float(value) == float(current_min_trade_usd):
                    summary = current_summary
                else:
                    candidate_config = _rules_config_with_volume_floor(rules_config, value)
                    candidate_results = await replay_from_db(
                        pool,
                        rules_config=candidate_config,
                        **replay_kwargs,
                    )
                    summary = summarize_backtest_analytics(
                        candidate_results,
                        review_index=review_index,
                        fp_rate_targets=targets,
                        min_reviewed_by_rule=min_reviewed_by_rule,
                    )
                candidate_summaries.append({"min_trade_usd": float(value), "summary": summary})

            report = {
                "filters": {
                    "from": start_ts.isoformat() if start_ts else None,
                    "to": end_ts.isoformat() if end_ts else None,
                    "venue": getattr(args, "backtest_venue", None),
                    "market": getattr(args, "backtest_market", None),
                    "limit": int(getattr(args, "limit", 0)),
                    "cold_start": bool(getattr(args, "cold_start", False)),
                },
                "current_volume_spike_min_trade_usd": current_min_trade_usd,
                "current": current_summary,
                "volume_spike_sensitivity": build_volume_spike_sensitivity_rows(
                    candidate_summaries,
                    baseline_min_trade_usd=current_min_trade_usd,
                ),
            }
            return report, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    report, error = asyncio.run(_run())
    if error:
        print(f"[backtest-analytics] DB query failed: {error}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    assert report is not None

    if getattr(args, "format", "text") == "json":
        print(json.dumps(report, indent=2, sort_keys=True, default=_json_default))
    else:
        print(format_backtest_analytics_text(report))
    return 0
