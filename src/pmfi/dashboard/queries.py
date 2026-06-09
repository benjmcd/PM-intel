"""Read-only DB queries powering the ingest-rate dashboard.

All functions take an asyncpg connection and never write. Windows are bounded so
frequent polling stays cheap on the existing indexes (raw_events received_at,
metric_windows window_start).
"""
from __future__ import annotations

import json

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
    """Per-venue per-bucket trade count + gross capital volume.

    Queries normalized_trades directly (bucketed by exchange_ts / received_at) so
    the view is live even when metric_windows hasn't been refreshed recently.
    """
    rows = await conn.fetch(
        """
        SELECT venue_code,
               date_trunc('minute',
                   received_at -
                   (EXTRACT(minute FROM received_at)::int % $2 || ' minutes')::interval
               ) AS window_start,
               COUNT(*)                         AS trades,
               SUM(capital_at_risk_usd)         AS volume_usd
        FROM normalized_trades
        WHERE received_at >= now() - ($1 || ' minutes')::interval
        GROUP BY venue_code, window_start
        ORDER BY window_start, venue_code
        """,
        str(lookback_minutes),
        window_seconds // 60,
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


def _summarize_evidence(evidence: dict) -> str:
    """Return a plain-English one-liner from an alert evidence dict.

    Pure function — no I/O. Used by the dashboard API and by pmfi alerts explain.
    Extracts the most actionable numeric fields: capital_at_risk_usd, threshold /
    percentile baseline fields, dominant_side, and trade_count.
    """
    if not evidence or not isinstance(evidence, dict):
        return ""
    parts: list[str] = []
    car = evidence.get("capital_at_risk_usd")
    if car is not None:
        parts.append(f"capital_at_risk_usd=${float(car):,.0f}")
    # Threshold / baseline comparisons
    for thresh_key in ("p99_threshold_usd", "p99_baseline_usd", "p995_threshold_usd", "threshold_usd"):
        val = evidence.get(thresh_key)
        if val is not None:
            parts.append(f"{thresh_key}=${float(val):,.0f}")
            break
    for pct_key in ("percentile", "pct_rank", "score_pct"):
        val = evidence.get(pct_key)
        if val is not None:
            parts.append(f"{pct_key}={float(val):.1f}")
            break
    side = evidence.get("dominant_side")
    if side:
        parts.append(f"side={side}")
    tc = evidence.get("trade_count")
    if tc is not None:
        parts.append(f"trades={int(tc)}")
    return "  ".join(parts)


async def recent_alerts(conn: asyncpg.Connection, *, limit: int = 20) -> list[dict]:
    """Recent alerts joined to markets for human-readable titles.

    Returns per-alert: rule_key, severity, confidence, market_title (falls back
    to venue_market_id), outcome_key, data_quality, a short evidence summary,
    and ISO timestamp. Bounded by limit; uses the alerts.fired_at index.
    """
    rows = await conn.fetch(
        """
        SELECT a.alert_id::text AS alert_id,
               a.rule_key,
               a.rule_version,
               a.severity,
               a.confidence,
               a.score,
               a.outcome_key,
               a.data_quality,
               a.evidence,
               a.fired_at,
               a.raw_event_id,
               a.trade_id::text AS trade_id,
               COALESCE(m.title, m.venue_market_id) AS market_title,
               m.venue_market_id
        FROM alerts a
        LEFT JOIN markets m ON m.market_id = a.market_id
        ORDER BY a.fired_at DESC
        LIMIT $1
        """,
        limit,
    )
    out: list[dict] = []
    for r in rows:
        ev_raw = r["evidence"]
        if isinstance(ev_raw, str):
            try:
                ev_dict = json.loads(ev_raw)
            except Exception:
                ev_dict = {}
        elif isinstance(ev_raw, dict):
            ev_dict = ev_raw
        else:
            ev_dict = {}
        out.append({
            "alert_id": r["alert_id"],
            "rule_key": r["rule_key"],
            "rule_version": r["rule_version"],
            "severity": r["severity"],
            "confidence": r["confidence"],
            "score": float(r["score"]) if r["score"] is not None else None,
            "outcome_key": r["outcome_key"],
            "data_quality": r["data_quality"],
            "evidence_summary": _summarize_evidence(ev_dict),
            "market_title": r["market_title"],
            "venue_market_id": r["venue_market_id"],
            "fired_at": r["fired_at"].isoformat() if r["fired_at"] else None,
            "raw_event_id": r["raw_event_id"],
            "trade_id": r["trade_id"],
        })
    return out
