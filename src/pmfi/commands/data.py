from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pmfi.data_reports import format_data_coverage_text, summarize_data_coverage_rows


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
        )
        SELECT
            re.venue_code,
            re.source_event_type,
            (nr.raw_event_id IS NOT NULL) AS has_normalized,
            (dl.raw_event_id IS NOT NULL) AS has_dead_letter,
            COUNT(*) AS cnt
        FROM raw_events re
        LEFT JOIN normalized_raw nr ON nr.raw_event_id = re.raw_event_id
        LEFT JOIN dead_letter_raw dl ON dl.raw_event_id = re.raw_event_id
        {where_sql}
        GROUP BY re.venue_code, re.source_event_type, has_normalized, has_dead_letter
        ORDER BY re.venue_code, re.source_event_type, has_normalized DESC, has_dead_letter DESC
    """
    return list(await pool.fetch(sql, *params))  # type: ignore[attr-defined]


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
            report = summarize_data_coverage_rows(rows)
            report["filters"] = {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
                "venue": getattr(args, "venue", None),
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
