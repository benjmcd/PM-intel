"""Read-only DB queries powering the ingest-rate dashboard.

All functions take an asyncpg connection and never write. Windows are bounded so
frequent polling stays cheap on the existing indexes (raw_events received_at,
metric_windows window_start).
"""
from __future__ import annotations

import asyncpg

from pmfi.alert_triage import parse_evidence, triage_flags


async def feed_health(conn: asyncpg.Connection, *, lookback_minutes: int = 10) -> list[dict]:
    """Per-venue feed health: last-event age, events in last 60s / 5m, unresolved dead letters.

    Returns a row for EVERY venue ever seen (not just those active in the lookback
    window) so the dashboard can distinguish never-started from went-silent.

    Strategy: MAX(received_at) over the last 30 days gives the true last-event
    timestamp per venue without an unbounded full-table scan. Windowed counts
    (events_60s, events_5m) are bounded by the lookback_minutes parameter.
    A venue with no events in the lookback window still appears with last_event_age_s
    computed from its most-recent event in the 30-day horizon, and events_60s/5m = 0.

    Counts ALL raw_events (book/price_change/trade) — i.e. the true data-received rate,
    not just normalized trades.
    """
    # Step 1: all venues seen in last 30 days with their true last-event timestamp
    # (bounded to 30 days — acceptable at local scale; operator can widen if needed).
    ever_rows = await conn.fetch(
        """
        SELECT venue_code,
               MAX(received_at) AS last_event_at
        FROM raw_events
        WHERE received_at >= now() - interval '30 days'
        GROUP BY venue_code
        ORDER BY venue_code
        """
    )
    if not ever_rows:
        return []

    # Step 2: windowed counts for active venues (bounded by lookback_minutes)
    window_rows = await conn.fetch(
        """
        SELECT venue_code,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '60 seconds') AS events_60s,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '5 minutes')  AS events_5m
        FROM raw_events
        WHERE received_at >= now() - ($1 || ' minutes')::interval
        GROUP BY venue_code
        """,
        str(lookback_minutes),
    )
    window_map = {r["venue_code"]: r for r in window_rows}

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
    for r in ever_rows:
        vc = r["venue_code"]
        last_at = r["last_event_at"]
        w = window_map.get(vc)
        out.append({
            "venue_code": vc,
            "last_event_at": last_at.isoformat() if last_at else None,
            # Age from the true last event (not the window boundary); computed below
            "last_event_age_s": None,
            "events_60s": int(w["events_60s"]) if w else 0,
            "events_5m": int(w["events_5m"]) if w else 0,
            "unresolved_dead_letters_1h": dl_map.get(vc, 0),
        })
        # Compute last_event_age_s server-side-equivalent in Python to avoid a
        # second round-trip; last_at is already a timezone-aware datetime from asyncpg.
        if last_at is not None:
            from datetime import datetime, timezone
            _now = datetime.now(timezone.utc)
            _last = last_at if last_at.tzinfo else last_at.replace(tzinfo=timezone.utc)
            out[-1]["last_event_age_s"] = int((_now - _last).total_seconds())
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
    def _money(value) -> str:
        n = float(value)
        if abs(n) < 100:
            return f"${n:,.2f}"
        return f"${n:,.0f}"

    parts: list[str] = []
    car = evidence.get("capital_at_risk_usd")
    if car is not None:
        parts.append(f"capital_at_risk_usd={_money(car)}")
    # Threshold / baseline comparisons
    for thresh_key in ("p99_threshold_usd", "p99_baseline_usd", "p995_threshold_usd", "threshold_usd"):
        val = evidence.get(thresh_key)
        if val is not None:
            parts.append(f"{thresh_key}={_money(val)}")
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
    this_trade = evidence.get("this_trade_usd")
    if this_trade is not None:
        parts.append(f"this_trade_usd={_money(this_trade)}")
    baseline_median = evidence.get("baseline_median_usd")
    if baseline_median is not None:
        parts.append(f"baseline_median_usd={_money(baseline_median)}")
    spike = evidence.get("spike_multiplier")
    if spike is not None:
        parts.append(f"spike_multiplier={float(spike):.1f}x")
    min_spike = evidence.get("min_spike_multiplier")
    if min_spike is not None:
        parts.append(f"min_spike_multiplier={float(min_spike):.1f}x")
    baseline_trades = evidence.get("baseline_trades")
    if baseline_trades is not None:
        parts.append(f"baseline_trades={int(baseline_trades)}")
    return "  ".join(parts)


async def recent_alerts(conn: asyncpg.Connection, *, limit: int = 20) -> list[dict]:
    """Recent alerts joined to markets and latest review state.

    Returns per-alert: rule_key, severity, confidence, market_title (falls back
    to venue_market_id), outcome_key, data_quality, latest review fields, a
    short evidence summary, and ISO timestamp. Bounded by limit; uses the
    alerts.fired_at index and only checks reviews for the limited alert set.
    """
    rows = await conn.fetch(
        """
        WITH recent AS MATERIALIZED (
            SELECT a.alert_id,
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
                   a.trade_id,
                   a.market_id
            FROM alerts a
            ORDER BY a.fired_at DESC
            LIMIT $1
        ),
        latest_reviews AS (
            SELECT DISTINCT ON (ar.alert_id)
                   ar.alert_id,
                   ar.label AS review_label,
                   ar.false_positive_category AS review_category,
                   ar.notes AS review_notes,
                   ar.reviewed_at,
                   ar.reviewed_by
            FROM alert_reviews ar
            JOIN recent a ON a.alert_id = ar.alert_id
            ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
        )
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
               m.venue_market_id,
               lr.review_label,
               lr.review_category,
               lr.review_notes,
               lr.reviewed_at,
               lr.reviewed_by,
               (lr.alert_id IS NOT NULL) AS is_reviewed
        FROM recent a
        LEFT JOIN markets m ON m.market_id = a.market_id
        LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id
        ORDER BY a.fired_at DESC
        """,
        limit,
    )
    out: list[dict] = []
    for r in rows:
        ev_dict = parse_evidence(r["evidence"])
        row_for_flags = {
            "data_quality": r["data_quality"],
            "raw_event_id": r["raw_event_id"],
            "trade_id": r["trade_id"],
        }
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
            "triage_flags": triage_flags(row_for_flags, ev_dict),
            "market_title": r["market_title"],
            "venue_market_id": r["venue_market_id"],
            "fired_at": r["fired_at"].isoformat() if r["fired_at"] else None,
            "raw_event_id": r["raw_event_id"],
            "trade_id": r["trade_id"],
            "review_label": r["review_label"],
            "review_category": r["review_category"],
            "review_notes": r["review_notes"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "reviewed_by": r["reviewed_by"],
            "is_reviewed": bool(r["is_reviewed"]),
        })
    return out
