"""Read-only DB queries powering the ingest-rate dashboard.

All functions take an asyncpg connection and never write. Windows are bounded so
frequent polling stays cheap on the existing indexes (raw_events received_at,
metric_windows window_start).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from pmfi.alert_triage import parse_evidence, triage_flags
from pmfi.db.repos.alerts import ALLOWED_REVIEW_LABELS, resolve_alert_id


ALLOWED_REVIEW_STATES = {"unreviewed", "reviewed"}
DEFAULT_REVIEW_HISTORY_LIMIT = 20
MAX_REVIEW_HISTORY_LIMIT = 100
ALLOWED_ALERT_RULE_KEYS = {
    "volume_spike_v1",
    "directional_cluster_v1",
    "market_relative_large_trade_v1",
    "momentum_v1",
    "large_trade_absolute_v1",
}
ALLOWED_TRIAGE_FLAGS = {
    "low_notional",
    "thin_baseline",
    "near_threshold",
    "degraded_data_quality",
    "missing_lineage",
}


def _validate_review_history_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1 or value > MAX_REVIEW_HISTORY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_REVIEW_HISTORY_LIMIT}")
    return value


def _validate_alert_id_lookup(alert_id_or_prefix: str) -> str:
    if not isinstance(alert_id_or_prefix, str):
        raise ValueError("alert_id must be a string")
    value = alert_id_or_prefix.strip()
    if not value:
        raise ValueError("alert_id is required")
    if len(value) > 36:
        raise ValueError("alert_id must be a UUID or short prefix")
    if any(ch in value for ch in ("%", "_", "/", "\\")):
        raise ValueError("alert_id contains unsupported characters")
    if len(value) == 36 and value.count("-") == 4:
        try:
            UUID(value)
        except ValueError as exc:
            raise ValueError("alert_id must be a UUID or short prefix") from exc
    return value


async def alert_review_history(
    conn: asyncpg.Connection,
    alert_id_or_prefix: str,
    *,
    limit: int = DEFAULT_REVIEW_HISTORY_LIMIT,
) -> dict | None:
    """Return append-only review history for one alert, newest first.

    Resolves the same full-UUID-or-prefix shape used by review writes. Unknown
    alerts return None; malformed inputs raise ValueError for caller 400s.
    """
    bounded_limit = _validate_review_history_limit(limit)
    lookup = _validate_alert_id_lookup(alert_id_or_prefix)
    alert_id = await resolve_alert_id(conn, lookup)
    if not alert_id:
        return None

    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM alerts WHERE alert_id = $1::uuid)",
        alert_id,
    )
    if not exists:
        return None

    rows = await conn.fetch(
        """SELECT review_id::text AS review_id,
                  alert_id::text AS alert_id,
                  label,
                  false_positive_category AS category,
                  notes,
                  reviewed_by,
                  reviewed_at
           FROM alert_reviews
           WHERE alert_id = $1::uuid
           ORDER BY reviewed_at DESC, review_id DESC
           LIMIT $2""",
        alert_id,
        bounded_limit,
    )
    return {
        "alert_id": alert_id,
        "reviews": [
            {
                "review_id": r["review_id"],
                "alert_id": r["alert_id"],
                "label": r["label"],
                "category": r["category"],
                "notes": r["notes"],
                "reviewed_by": r["reviewed_by"],
                "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            }
            for r in rows
        ],
    }


def _money(value) -> str:
    n = float(value)
    if abs(n) < 100:
        return f"${n:,.2f}"
    return f"${n:,.0f}"


def _evidence_facts(evidence: dict) -> list[dict]:
    """Return ordered, display-ready evidence facts for compact comparison."""
    if not evidence or not isinstance(evidence, dict):
        return []

    facts: list[dict] = []
    if evidence.get("capital_at_risk_usd") is not None:
        facts.append({
            "key": "capital_at_risk_usd",
            "label": "capital",
            "value": _money(evidence["capital_at_risk_usd"]),
        })
    if evidence.get("this_trade_usd") is not None:
        facts.append({
            "key": "this_trade_usd",
            "label": "trade",
            "value": _money(evidence["this_trade_usd"]),
        })

    threshold_labels = {
        "p99_threshold_usd": "p99",
        "p99_baseline_usd": "p99 baseline",
        "p995_threshold_usd": "p99.5",
        "threshold_usd": "threshold",
    }
    for key, label in threshold_labels.items():
        if evidence.get(key) is not None:
            facts.append({
                "key": key,
                "label": label,
                "value": _money(evidence[key]),
            })
            break

    percentile_labels = {
        "percentile": "percentile",
        "pct_rank": "pct rank",
        "score_pct": "score pct",
    }
    for key, label in percentile_labels.items():
        if evidence.get(key) is not None:
            facts.append({
                "key": key,
                "label": label,
                "value": f"{float(evidence[key]):.1f}",
            })
            break

    if evidence.get("dominant_side"):
        facts.append({
            "key": "dominant_side",
            "label": "side",
            "value": str(evidence["dominant_side"]),
        })
    if evidence.get("trade_count") is not None:
        facts.append({
            "key": "trade_count",
            "label": "trades",
            "value": str(int(evidence["trade_count"])),
        })
    if evidence.get("baseline_median_usd") is not None:
        facts.append({
            "key": "baseline_median_usd",
            "label": "baseline",
            "value": _money(evidence["baseline_median_usd"]),
        })
    if evidence.get("spike_multiplier") is not None:
        facts.append({
            "key": "spike_multiplier",
            "label": "spike",
            "value": f"{float(evidence['spike_multiplier']):.1f}x",
        })
    if evidence.get("min_spike_multiplier") is not None:
        facts.append({
            "key": "min_spike_multiplier",
            "label": "min spike",
            "value": f"{float(evidence['min_spike_multiplier']):.1f}x",
        })
    if evidence.get("baseline_trades") is not None:
        facts.append({
            "key": "baseline_trades",
            "label": "baseline trades",
            "value": str(int(evidence["baseline_trades"])),
        })
    return facts


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


async def persistence_health(conn: asyncpg.Connection) -> dict:
    """Per-venue normalized-trade persistence health for operator diagnostics."""
    trade_rows = await conn.fetch(
        """
        SELECT venue_code,
               MAX(received_at) AS last_persisted_at,
               EXTRACT(EPOCH FROM (now() - MAX(received_at)))::int AS last_persisted_age_s,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '5 minutes') AS trades_5m,
               COUNT(*) FILTER (WHERE received_at >= now() - interval '1 hour') AS trades_1h
        FROM normalized_trades
        WHERE received_at >= now() - interval '2 hours'
        GROUP BY venue_code
        ORDER BY venue_code
        """
    )
    dl_row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n
        FROM dead_letters
        WHERE resolved = false AND created_at >= now() - interval '1 hour'
        """
    )
    return {
        "venues": [
            {
                "venue_code": row["venue_code"],
                "last_persisted_at": row["last_persisted_at"].isoformat()
                if row["last_persisted_at"]
                else None,
                "last_persisted_age_s": int(row["last_persisted_age_s"])
                if row["last_persisted_age_s"] is not None
                else None,
                "trades_5m": int(row["trades_5m"]),
                "trades_1h": int(row["trades_1h"]),
            }
            for row in trade_rows
        ],
        "unresolved_dead_letters_1h": int(dl_row["n"]) if dl_row else 0,
    }


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


async def recent_alerts(
    conn: asyncpg.Connection,
    *,
    limit: int = 20,
    review_state: str | None = None,
    review_label: str | None = None,
    rule_key: str | None = None,
    triage_flags_filter: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Recent alerts joined to markets and latest review state.

    Returns per-alert: rule_key, severity, confidence, market_title (falls back
    to venue_market_id), outcome_key, data_quality, latest review fields, a
    short evidence summary, and ISO timestamp. Review and triage filters are
    applied before the returned limit. Triage filtering runs in Python because
    the deterministic flag helper is intentionally shared outside SQL.

    triage_flags_filter uses deterministic flags from pmfi.alert_triage and applies
    AND semantics across all requested flags. If review filters are invalid,
    ValueError is raised so caller can return a precise 400 to the user.
    """
    if review_state is not None and review_state not in ALLOWED_REVIEW_STATES:
        raise ValueError(f"invalid review_state={review_state!r}")
    if review_state == "unreviewed" and review_label is not None:
        raise ValueError("review_state=unreviewed cannot be combined with review_label")
    if review_label is not None and review_label not in ALLOWED_REVIEW_LABELS:
        raise ValueError(f"invalid review_label={review_label!r}")
    if rule_key is not None and rule_key not in ALLOWED_ALERT_RULE_KEYS:
        raise ValueError(f"invalid rule_key={rule_key!r}")

    requested_flags = [
        f for f in (list(triage_flags_filter) if triage_flags_filter else [])
        if isinstance(f, str) and f.strip()
    ]
    if requested_flags:
        requested_flags = [f.strip() for f in requested_flags]
        unknown = {f for f in requested_flags if f not in ALLOWED_TRIAGE_FLAGS}
        if unknown:
            raise ValueError(f"invalid triage_flags={','.join(sorted(unknown))}")

    filters: list[str] = []
    params: list = []
    idx = 1
    if review_state == "unreviewed":
        filters.append("lr.alert_id IS NULL")
    elif review_state == "reviewed":
        filters.append("lr.alert_id IS NOT NULL")
    if review_label is not None:
        filters.append(f"lr.review_label = ${idx}")
        params.append(review_label)
        idx += 1
    if rule_key is not None:
        filters.append(f"a.rule_key = ${idx}")
        params.append(rule_key)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(filters)) if filters else ""
    if requested_flags:
        limit_clause = ""
    else:
        limit_clause = f"LIMIT ${idx}"
        params.append(limit)

    rows = await conn.fetch(
        f"""
        WITH latest_reviews AS (
            SELECT DISTINCT ON (ar.alert_id)
                   ar.alert_id,
                   ar.label AS review_label,
                   ar.false_positive_category AS review_category,
                   ar.notes AS review_notes,
                   ar.reviewed_at,
                   ar.reviewed_by
            FROM alert_reviews ar
            JOIN alerts a ON a.alert_id = ar.alert_id
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
        FROM alerts a
        LEFT JOIN markets m ON m.market_id = a.market_id
        LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id
        {where_clause}
        ORDER BY a.fired_at DESC
        {limit_clause}
        """,
        *params,
    )
    rows_out: list[dict] = []
    if requested_flags:
        required_flags = set(requested_flags)
        for r in rows:
            ev_dict = parse_evidence(r["evidence"])
            row_for_flags = {
                "data_quality": r["data_quality"],
                "raw_event_id": r["raw_event_id"],
                "trade_id": r["trade_id"],
            }
            flags = triage_flags(row_for_flags, ev_dict)
            if not required_flags.issubset(set(flags)):
                continue
            rows_out.append({
                "alert_id": r["alert_id"],
                "rule_key": r["rule_key"],
                "rule_version": r["rule_version"],
                "severity": r["severity"],
                "confidence": r["confidence"],
                "score": float(r["score"]) if r["score"] is not None else None,
                "outcome_key": r["outcome_key"],
                "data_quality": r["data_quality"],
                "evidence_summary": _summarize_evidence(ev_dict),
                "evidence_facts": _evidence_facts(ev_dict),
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
        return rows_out[:limit]

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
            "evidence_facts": _evidence_facts(ev_dict),
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
