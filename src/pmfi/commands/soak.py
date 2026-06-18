"""Read-only DB-backed soak evidence/readiness checks."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class SoakThresholds:
    min_duration_minutes: int = 60
    min_required_venue_duration_minutes: int | None = None
    min_raw_events: int = 1
    min_trades: int = 1
    max_dead_letters: int = 0
    max_incidents: int = 0
    required_venues: tuple[str, ...] = ()


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def parse_window(value: str) -> timedelta:
    """Parse a positive relative window such as 60m, 2h, or 1d."""
    m = re.fullmatch(r"(\d+)([mhd])", (value or "").strip().lower())
    if not m:
        raise ValueError("window must be a positive relative value such as 60m, 2h, or 1d")
    amount = int(m.group(1))
    if amount <= 0:
        raise ValueError("window must be greater than zero")
    unit = m.group(2)
    seconds = {"m": 60, "h": 3600, "d": 86400}[unit] * amount
    return timedelta(seconds=seconds)


def parse_soak_timestamp(value: str) -> datetime:
    """Parse a timezone-aware ISO timestamp and normalize to UTC."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("timestamp must be a non-empty ISO-8601 value")

    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            "must be a timezone-aware ISO-8601 timestamp"
        ) from exc

    if parsed.tzinfo is None:
        raise ValueError("must include a timezone offset (for example ...Z or +00:00)")
    parsed_utc = parsed.astimezone(timezone.utc)
    if parsed_utc > datetime.now(timezone.utc):
        raise ValueError("must not be in the future")
    return parsed_utc


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def normalize_required_venues(values: list[str] | None) -> tuple[str, ...]:
    venues: list[str] = []
    for raw in values or []:
        for part in raw.split(","):
            venue = part.strip().lower()
            if venue and venue not in venues:
                venues.append(venue)
    return tuple(venues)


def validate_thresholds(thresholds: SoakThresholds) -> list[str]:
    errors: list[str] = []
    for name in (
        "min_duration_minutes",
        "min_raw_events",
        "min_trades",
        "max_dead_letters",
        "max_incidents",
    ):
        value = getattr(thresholds, name)
        if value < 0:
            errors.append(f"{name.replace('_', '-')} must be >= 0")
    if (
        thresholds.min_required_venue_duration_minutes is not None
        and thresholds.min_required_venue_duration_minutes < 0
    ):
        errors.append("min-required-venue-duration-minutes must be >= 0")
    return errors


def _dt(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _raw_duration_minutes(first_raw: Any, last_raw: Any) -> float:
    first = _dt(first_raw)
    last = _dt(last_raw)
    if not first or not last:
        return 0.0
    return max(0.0, (last - first).total_seconds() / 60)


def evaluate_soak(summary: dict[str, Any], thresholds: SoakThresholds) -> dict[str, Any]:
    """Return a deterministic readiness verdict from a DB summary."""
    raw = int(summary.get("raw_events") or 0)
    trades = int(summary.get("normalized_trades") or 0)
    dead_letters = int(summary.get("unresolved_dead_letters") or 0)
    incidents = int(summary.get("open_data_quality_incidents") or 0)
    first_raw = _dt(summary.get("first_raw_event_at"))
    last_raw = _dt(summary.get("last_raw_event_at"))
    duration_minutes = _raw_duration_minutes(first_raw, last_raw)

    by_venue = {
        str(row.get("venue_code")): {
            "venue_code": str(row.get("venue_code")),
            "raw_events": int(row.get("raw_events") or 0),
            "normalized_trades": int(row.get("normalized_trades") or 0),
            "first_raw_event_at": row.get("first_raw_event_at"),
            "last_raw_event_at": row.get("last_raw_event_at"),
            "raw_evidence_duration_minutes": round(
                _raw_duration_minutes(row.get("first_raw_event_at"), row.get("last_raw_event_at")),
                3,
            ),
            "first_trade_at": row.get("first_trade_at"),
            "last_trade_at": row.get("last_trade_at"),
        }
        for row in summary.get("venues", [])
    }

    failures: list[str] = []
    if raw <= 0:
        failures.append("no raw events in window")
    if raw < thresholds.min_raw_events:
        failures.append(f"raw events {raw} < minimum {thresholds.min_raw_events}")
    if trades < thresholds.min_trades:
        failures.append(f"normalized trades {trades} < minimum {thresholds.min_trades}")
    if duration_minutes < thresholds.min_duration_minutes:
        failures.append(
            f"raw evidence duration {duration_minutes:.1f}m < minimum {thresholds.min_duration_minutes}m"
        )
    if dead_letters > thresholds.max_dead_letters:
        failures.append(
            f"unresolved dead letters {dead_letters} > maximum {thresholds.max_dead_letters}"
        )
    if incidents > thresholds.max_incidents:
        failures.append(
            f"open data-quality incidents {incidents} > maximum {thresholds.max_incidents}"
        )

    missing_required: list[dict[str, Any]] = []
    for venue in thresholds.required_venues:
        row = by_venue.get(venue)
        venue_failures: list[str] = []
        if not row or row["raw_events"] <= 0:
            venue_failures.append("missing raw events")
        if not row or row["normalized_trades"] <= 0:
            venue_failures.append("missing normalized trades")
        if row and thresholds.min_required_venue_duration_minutes is not None:
            venue_duration = row["raw_evidence_duration_minutes"]
            if venue_duration < thresholds.min_required_venue_duration_minutes:
                venue_failures.append(
                    "raw evidence duration "
                    f"{venue_duration:.1f}m < minimum "
                    f"{thresholds.min_required_venue_duration_minutes}m"
                )
        if venue_failures:
            missing_required.append({"venue_code": venue, "reasons": venue_failures})
            duration_failures = [
                reason for reason in venue_failures
                if reason.startswith("raw evidence duration ")
            ]
            presence_failures = [
                reason for reason in venue_failures
                if not reason.startswith("raw evidence duration ")
            ]
            if presence_failures:
                failures.append(f"required venue {venue}: {', '.join(presence_failures)}")
            failures.extend(f"required venue {venue} {reason}" for reason in duration_failures)

    return {
        "ok": not failures,
        "failures": failures,
        "thresholds": {
            "min_duration_minutes": thresholds.min_duration_minutes,
            "min_required_venue_duration_minutes": thresholds.min_required_venue_duration_minutes,
            "min_raw_events": thresholds.min_raw_events,
            "min_trades": thresholds.min_trades,
            "max_dead_letters": thresholds.max_dead_letters,
            "max_incidents": thresholds.max_incidents,
            "required_venues": list(thresholds.required_venues),
        },
        "window": summary.get("window"),
        "counts": {
            "raw_events": raw,
            "normalized_trades": trades,
            "alerts": int(summary.get("alerts") or 0),
            "unresolved_dead_letters": dead_letters,
            "open_data_quality_incidents": incidents,
        },
        "timestamps": {
            "first_raw_event_at": first_raw,
            "last_raw_event_at": last_raw,
            "first_trade_at": summary.get("first_trade_at"),
            "last_trade_at": summary.get("last_trade_at"),
            "first_alert_at": summary.get("first_alert_at"),
            "last_alert_at": summary.get("last_alert_at"),
            "raw_evidence_duration_minutes": round(duration_minutes, 3),
        },
        "venues": list(by_venue.values()),
        "missing_required_venues": missing_required,
    }


async def fetch_soak_summary(conn: Any, *, start_at: datetime, end_at: datetime) -> dict[str, Any]:
    """Read DB evidence for a soak window using SELECT-only queries."""
    raw = await conn.fetchrow(
        """
        SELECT COUNT(*) AS raw_events,
               MIN(received_at) AS first_raw_event_at,
               MAX(received_at) AS last_raw_event_at
        FROM raw_events
        WHERE received_at >= $1 AND received_at <= $2
        """,
        start_at,
        end_at,
    )
    trades = await conn.fetchrow(
        """
        SELECT COUNT(*) AS normalized_trades,
               MIN(received_at) AS first_trade_at,
               MAX(received_at) AS last_trade_at
        FROM normalized_trades
        WHERE received_at >= $1 AND received_at <= $2
        """,
        start_at,
        end_at,
    )
    alerts = await conn.fetchrow(
        """
        SELECT COUNT(*) AS alerts,
               MIN(fired_at) AS first_alert_at,
               MAX(fired_at) AS last_alert_at
        FROM alerts
        WHERE fired_at >= $1 AND fired_at <= $2
        """,
        start_at,
        end_at,
    )
    unresolved_dead_letters = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM dead_letters
        WHERE resolved = false AND created_at >= $1 AND created_at <= $2
        """,
        start_at,
        end_at,
    )
    open_incidents = await conn.fetchval(
        "SELECT COUNT(*) FROM data_quality_incidents WHERE status = 'open'"
    )
    raw_by_venue = await conn.fetch(
        """
        SELECT venue_code,
               COUNT(*) AS raw_events,
               MIN(received_at) AS first_raw_event_at,
               MAX(received_at) AS last_raw_event_at
        FROM raw_events
        WHERE received_at >= $1 AND received_at <= $2
        GROUP BY venue_code
        ORDER BY venue_code
        """,
        start_at,
        end_at,
    )
    trade_by_venue = await conn.fetch(
        """
        SELECT venue_code,
               COUNT(*) AS normalized_trades,
               MIN(received_at) AS first_trade_at,
               MAX(received_at) AS last_trade_at
        FROM normalized_trades
        WHERE received_at >= $1 AND received_at <= $2
        GROUP BY venue_code
        ORDER BY venue_code
        """,
        start_at,
        end_at,
    )

    venues: dict[str, dict[str, Any]] = {}
    for row in raw_by_venue:
        venue = str(row["venue_code"])
        venues[venue] = {
            "venue_code": venue,
            "raw_events": int(row["raw_events"] or 0),
            "normalized_trades": 0,
            "first_raw_event_at": row["first_raw_event_at"],
            "last_raw_event_at": row["last_raw_event_at"],
            "first_trade_at": None,
            "last_trade_at": None,
        }
    for row in trade_by_venue:
        venue = str(row["venue_code"])
        entry = venues.setdefault(
            venue,
            {
                "venue_code": venue,
                "raw_events": 0,
                "normalized_trades": 0,
                "first_raw_event_at": None,
                "last_raw_event_at": None,
                "first_trade_at": None,
                "last_trade_at": None,
            },
        )
        entry["normalized_trades"] = int(row["normalized_trades"] or 0)
        entry["first_trade_at"] = row["first_trade_at"]
        entry["last_trade_at"] = row["last_trade_at"]

    return {
        "window": {"start_at": start_at, "end_at": end_at},
        "raw_events": int(raw["raw_events"] or 0),
        "normalized_trades": int(trades["normalized_trades"] or 0),
        "alerts": int(alerts["alerts"] or 0),
        "unresolved_dead_letters": int(unresolved_dead_letters or 0),
        "open_data_quality_incidents": int(open_incidents or 0),
        "first_raw_event_at": raw["first_raw_event_at"],
        "last_raw_event_at": raw["last_raw_event_at"],
        "first_trade_at": trades["first_trade_at"],
        "last_trade_at": trades["last_trade_at"],
        "first_alert_at": alerts["first_alert_at"],
        "last_alert_at": alerts["last_alert_at"],
        "venues": list(venues.values()),
    }


def render_text(result: dict[str, Any]) -> str:
    status = "PASS" if result["ok"] else "FAIL"
    counts = result["counts"]
    ts = result["timestamps"]
    lines = [
        f"Soak readiness: {status}",
        (
            "Counts: "
            f"raw_events={counts['raw_events']} "
            f"normalized_trades={counts['normalized_trades']} "
            f"alerts={counts['alerts']} "
            f"unresolved_dead_letters={counts['unresolved_dead_letters']} "
            f"open_data_quality_incidents={counts['open_data_quality_incidents']}"
        ),
        (
            "Raw evidence: "
            f"first={_json_default(ts['first_raw_event_at']) if ts['first_raw_event_at'] else '-'} "
            f"last={_json_default(ts['last_raw_event_at']) if ts['last_raw_event_at'] else '-'} "
            f"duration_minutes={ts['raw_evidence_duration_minutes']}"
        ),
        "Per venue:",
    ]
    venues = result.get("venues") or []
    if venues:
        for row in venues:
            lines.append(
                "  "
                f"{row['venue_code']}: raw={row['raw_events']} trades={row['normalized_trades']} "
                f"raw_duration_minutes={row['raw_evidence_duration_minutes']} "
                f"raw_first={_json_default(row['first_raw_event_at']) if row['first_raw_event_at'] else '-'} "
                f"raw_last={_json_default(row['last_raw_event_at']) if row['last_raw_event_at'] else '-'} "
                f"trade_first={_json_default(row['first_trade_at']) if row['first_trade_at'] else '-'} "
                f"trade_last={_json_default(row['last_trade_at']) if row['last_trade_at'] else '-'}"
            )
    else:
        lines.append("  none")
    if result["failures"]:
        lines.append("Failures:")
        lines.extend(f"  - {failure}" for failure in result["failures"])
    return "\n".join(lines)


def cmd_soak(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool

    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    explicit_start: datetime | None = None
    explicit_end: datetime | None = None
    if since is not None:
        try:
            explicit_start = parse_soak_timestamp(since)
        except ValueError as exc:
            print(f"[soak] Invalid --since: {exc}", file=sys.stderr)
            return 1
    if until is not None:
        try:
            explicit_end = parse_soak_timestamp(until)
        except ValueError as exc:
            print(f"[soak] Invalid --until: {exc}", file=sys.stderr)
            return 1

    window: timedelta | None = None
    if since is None:
        try:
            window = parse_window(getattr(args, "window", "2h"))
        except ValueError as exc:
            print(f"[soak] Invalid --window: {exc}", file=sys.stderr)
            return 1

    end_at = explicit_end or datetime.now(timezone.utc)
    if since is None:
        if window is None:
            raise AssertionError("soak window is not initialized")
        query_start_at = end_at - window
    else:
        query_start_at = explicit_start
    if query_start_at is None:
        raise AssertionError("soak window start is not initialized")
    if query_start_at >= end_at:
        print("[soak] Invalid window: start must be before end", file=sys.stderr)
        return 1

    thresholds = SoakThresholds(
        min_duration_minutes=getattr(args, "min_duration_minutes", 60),
        min_required_venue_duration_minutes=getattr(
            args,
            "min_required_venue_duration_minutes",
            None,
        ),
        min_raw_events=getattr(args, "min_raw_events", 1),
        min_trades=getattr(args, "min_trades", 1),
        max_dead_letters=getattr(args, "max_dead_letters", 0),
        max_incidents=getattr(args, "max_incidents", 0),
        required_venues=normalize_required_venues(getattr(args, "required_venue", [])),
    )
    errors = validate_thresholds(thresholds)
    if errors:
        for error in errors:
            print(f"[soak] Invalid threshold: {error}", file=sys.stderr)
        return 1

    async def _run() -> dict[str, Any]:
        cfg = load_config()
        pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        try:
            async with pool.acquire() as conn:
                summary = await fetch_soak_summary(conn, start_at=query_start_at, end_at=end_at)
        finally:
            await close_pool(pool)
        return evaluate_soak(summary, thresholds)

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        print(f"[soak] DB unavailable or query failed: {exc}", file=sys.stderr)
        print(r"  Run 'python scripts\db_local.py up' and retry after an ingest window completes.", file=sys.stderr)
        return 1

    fmt = getattr(args, "format", "text")
    if fmt == "json":
        print(json.dumps(result, indent=2, default=_json_default))
    else:
        print(render_text(result))
    return 0 if result["ok"] else 1
