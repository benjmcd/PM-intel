"""Read-only DB queries powering the ingest-rate dashboard.

All functions take an asyncpg connection and never write. Windows are bounded so
frequent polling stays cheap on the existing indexes (raw_events received_at,
metric_windows window_start).
"""
from __future__ import annotations

import asyncpg


async def feed_health(conn: asyncpg.Connection, *, lookback_minutes: int = 10) -> list[dict]:
    """Per-venue feed health: last-event age, events in last 60s / 5m, unresolved dead letters.

    Counts ALL raw_events (book/price_change/trade) — i.e. the true data-received rate,
    not just normalized trades.
    """
    rows = await conn.fetch(
        """
        SELECT venue_code,
               MAX(received_at) AS last_event_at,
               EXTRACT(EPOCH FROM (now() - MAX(received_at)))::int AS last_event_age_s,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '60 seconds') AS events_60s,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '5 minutes')  AS events_5m
        FROM raw_events
        WHERE received_at >= now() - ($1 || ' minutes')::interval
        GROUP BY venue_code
        ORDER BY venue_code
        """,
        str(lookback_minutes),
    )
    dl_rows = await conn.fetch(
        """
        SELECT venue_code, COUNT(*) AS n
        FROM dead_letters
        WHERE resolved = false AND created_at >= now() - interval '1 hour'
        GROUP BY venue_code
        """
    )
    dl_map = {r["venue_code"]: int(r["n"]) for r in dl_rows}
    out: list[dict] = []
    for r in rows:
        out.append({
            "venue_code": r["venue_code"],
            "last_event_at": r["last_event_at"].isoformat() if r["last_event_at"] else None,
            "last_event_age_s": int(r["last_event_age_s"]) if r["last_event_age_s"] is not None else None,
            "events_60s": int(r["events_60s"]),
            "events_5m": int(r["events_5m"]),
            "unresolved_dead_letters_1h": dl_map.get(r["venue_code"], 0),
        })
    return out


async def volume_timeseries(
    conn: asyncpg.Connection,
    *,
    lookback_minutes: int = 60,
    window_seconds: int = 300,
) -> list[dict]:
    """Per-venue per-bucket trade count + gross capital volume from metric_windows.

    metric_windows is already time-bucketed and carries venue_code directly (no join).
    """
    rows = await conn.fetch(
        """
        SELECT venue_code, window_start,
               SUM(trade_count) AS trades,
               SUM(gross_capital_at_risk_usd) AS volume_usd
        FROM metric_windows
        WHERE window_start >= now() - ($1 || ' minutes')::interval
          AND window_seconds = $2
        GROUP BY venue_code, window_start
        ORDER BY window_start, venue_code
        """,
        str(lookback_minutes),
        window_seconds,
    )
    return [
        {
            "venue_code": r["venue_code"],
            "window_start": r["window_start"].isoformat(),
            "trades": int(r["trades"]) if r["trades"] is not None else 0,
            "volume_usd": float(r["volume_usd"]) if r["volume_usd"] is not None else 0.0,
        }
        for r in rows
    ]
