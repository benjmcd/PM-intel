from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

import asyncpg

from pmfi.alert_triage import parse_evidence, triage_flags
from pmfi.domain import AlertDecision

ALLOWED_REVIEW_LABELS = {"tp", "fp", "noise"}


def _dedupe_key(
    decision: AlertDecision,
    *,
    venue_code: str,
    market_id: str | None,
    outcome_key: str | None,
    hour_bucket: str,
) -> str:
    raw = f"{venue_code}:{market_id}:{outcome_key}:{decision.rule_id}:{decision.rule_version}:{hour_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

async def insert_alert(
    conn: asyncpg.Connection,
    decision: AlertDecision,
    *,
    event_ts: datetime | None = None,
    title: str,
    summary: str,
    venue_code: str,
    market_id: str | None = None,
    outcome_key: str | None = None,
    raw_event_id: int | None = None,
    trade_id=None,
) -> str | None:
    if not decision.emit_alert:
        return None
    hour_bucket = (event_ts or datetime.now(timezone.utc)).strftime("%Y-%m-%d-%H")
    dedupe = _dedupe_key(
        decision,
        venue_code=venue_code,
        market_id=market_id,
        outcome_key=outcome_key,
        hour_bucket=hour_bucket,
    )
    existing = await conn.fetchrow("SELECT alert_id::text FROM alerts WHERE dedupe_key=$1", dedupe)
    if existing:
        return None
    # Coerce trade_id to string for uuid cast; None stays None.
    trade_id_str = str(trade_id) if trade_id is not None else None
    try:
        row = await conn.fetchrow(
            """INSERT INTO alerts
               (dedupe_key, rule_key, rule_version, venue_code, market_id,
                outcome_key, severity, confidence, score, title, summary, evidence, data_quality,
                raw_event_id, trade_id)
               VALUES ($1,$2,$3,$4,$5::uuid,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,$15::uuid)
               RETURNING alert_id::text""",
            dedupe, decision.rule_id, decision.rule_version, venue_code,
            market_id, outcome_key, decision.severity, decision.confidence,
            decision.score, title, summary,
            json.dumps(decision.evidence), decision.data_quality,
            raw_event_id, trade_id_str,
        )
        return str(row["alert_id"])
    except asyncpg.UniqueViolationError:
        return None


async def resolve_alert_id(conn, alert_id_or_prefix: str) -> str | None:
    """Resolve a full UUID or short prefix to a full alert_id string, or None."""
    if len(alert_id_or_prefix) == 36 and alert_id_or_prefix.count('-') == 4:
        return alert_id_or_prefix
    row = await conn.fetchrow(
        "SELECT alert_id::text FROM alerts WHERE alert_id::text LIKE $1 || '%' ORDER BY fired_at DESC LIMIT 1",
        alert_id_or_prefix,
    )
    return row["alert_id"] if row else None


async def get_alert_by_id(conn, alert_id: str) -> dict | None:
    """Fetch a single alert by UUID or prefix, joined to markets for title.

    Returns a dict with all alert columns plus market_title and venue_market_id,
    or None when not found.
    """
    if not (len(alert_id) == 36 and alert_id.count('-') == 4):
        alert_id = await resolve_alert_id(conn, alert_id)
        if not alert_id:
            return None
    row = await conn.fetchrow(
        """SELECT a.alert_id::text AS alert_id,
                  a.rule_key, a.rule_version, a.severity, a.confidence, a.score,
                  a.title, a.summary, a.evidence, a.data_quality, a.outcome_key,
                  a.fired_at, a.created_at,
                  a.raw_event_id, a.trade_id::text AS trade_id,
                  COALESCE(m.title, m.venue_market_id) AS market_title,
                  m.venue_market_id, m.venue_code AS market_venue_code
           FROM alerts a
           LEFT JOIN markets m ON m.market_id = a.market_id
           WHERE a.alert_id = $1::uuid""",
        alert_id,
    )
    if row is None:
        return None
    return dict(row)


async def insert_alert_review(
    conn,
    alert_id_or_prefix: str,
    *,
    label: str,
    category: str | None = None,
    notes: str | None = None,
    reviewed_by: str | None = None,
) -> dict | None:
    """Append one review row and return inserted metadata, or None if alert is missing."""
    if label not in ALLOWED_REVIEW_LABELS:
        raise ValueError("label must be one of: tp, fp, noise")
    alert_id = await resolve_alert_id(conn, alert_id_or_prefix)
    if not alert_id:
        return None
    try:
        row = await conn.fetchrow(
            """INSERT INTO alert_reviews
               (alert_id, label, false_positive_category, notes, reviewed_by)
               VALUES ($1::uuid, $2, $3, $4, $5)
               RETURNING review_id::text AS review_id,
                         alert_id::text AS alert_id,
                         label,
                         false_positive_category,
                         notes,
                         reviewed_by,
                         reviewed_at""",
            alert_id,
            label,
            category,
            notes,
            reviewed_by,
        )
    except (asyncpg.ForeignKeyViolationError, asyncpg.InvalidTextRepresentationError):
        return None
    if row is None:
        return None
    return {
        "review_id": row["review_id"],
        "alert_id": row["alert_id"],
        "label": row["label"],
        "category": row["false_positive_category"],
        "notes": row["notes"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
    }


async def list_alerts(
    conn,
    *,
    limit: int = 50,
    venue_code: str | None = None,
    severity: str | None = None,
    market: str | None = None,
    since: "datetime | None" = None,
) -> list[dict]:
    conditions = []
    params: list = []

    if venue_code:
        params.append(venue_code)
        conditions.append(f"venue_code = ${len(params)}")
    if severity:
        params.append(severity)
        conditions.append(f"severity = ${len(params)}")
    if market:
        params.append(f"%{market}%")
        conditions.append(f"title ILIKE ${len(params)}")
    if since:
        params.append(since)
        conditions.append(f"created_at >= ${len(params)}")

    params.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""SELECT alert_id, rule_key, rule_version, severity, title, summary, venue_code, outcome_key,
                     confidence, data_quality, raw_event_id, trade_id, created_at
              FROM alerts {where} ORDER BY created_at DESC LIMIT ${len(params)}"""
    rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


def _review_queue_item_with_flags(row) -> dict:  # noqa: ANN001
    item = dict(row)
    evidence = parse_evidence(item.pop("evidence", None))
    item["triage_flags"] = triage_flags(item, evidence)
    item.pop("raw_event_id", None)
    item.pop("trade_id", None)
    return item


def _triage_flag_summary(rows) -> dict:  # noqa: ANN001
    triage_counter: Counter[str] = Counter()
    total_flagged = 0
    for row in rows:
        item = dict(row)
        evidence = parse_evidence(item.get("evidence"))
        flags = triage_flags(item, evidence)
        if flags:
            total_flagged += 1
            triage_counter.update(flags)
    return {
        "total_flagged": total_flagged,
        "by_flag": [
            {"flag": flag, "cnt": cnt}
            for flag, cnt in sorted(
                triage_counter.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


async def get_alert_summary(conn, *, since: "datetime | None" = None) -> dict:
    """Get aggregated alert summary for reporting.

    Returns counts by severity, venue, rule_id, top markets, review queue,
    latest review outcomes, and data-gap summaries.
    """
    from datetime import datetime, timezone, timedelta
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=24)

    total_row = await conn.fetchrow(
        "SELECT COUNT(*) AS total FROM alerts WHERE created_at >= $1", since
    )
    by_severity = await conn.fetch(
        "SELECT severity, COUNT(*) AS cnt FROM alerts WHERE created_at >= $1 GROUP BY severity ORDER BY cnt DESC",
        since,
    )
    by_rule = await conn.fetch(
        "SELECT rule_key, COUNT(*) AS cnt FROM alerts WHERE created_at >= $1 GROUP BY rule_key ORDER BY cnt DESC",
        since,
    )
    by_venue = await conn.fetch(
        "SELECT venue_code, COUNT(*) AS cnt FROM alerts WHERE created_at >= $1 GROUP BY venue_code ORDER BY cnt DESC",
        since,
    )
    top_markets = await conn.fetch(
        """SELECT title, COUNT(*) AS cnt,
                  CASE MAX(CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END)
                       WHEN 3 THEN 'high' WHEN 2 THEN 'medium' WHEN 1 THEN 'low' ELSE 'info' END AS max_severity
           FROM alerts WHERE created_at >= $1 GROUP BY title ORDER BY cnt DESC LIMIT 10""",
        since,
    )
    recent_high = await conn.fetch(
        """SELECT rule_key, rule_version, severity, data_quality, title, created_at FROM alerts
           WHERE created_at >= $1 AND severity IN ('high', 'medium')
           ORDER BY created_at DESC LIMIT 10""",
        since,
    )
    review_queue_total = await conn.fetchval(
        """SELECT COUNT(*)
           FROM alerts a
           WHERE a.created_at >= $1
             AND NOT EXISTS (
                 SELECT 1 FROM alert_reviews ar WHERE ar.alert_id = a.alert_id
             )""",
        since,
    )
    review_queue_alerts = await conn.fetch(
        """SELECT a.alert_id::text AS alert_id,
                  a.created_at,
                  a.rule_key,
                  a.severity,
                  a.venue_code,
                  a.data_quality,
                  a.evidence,
                  a.raw_event_id,
                  a.trade_id::text AS trade_id,
                  COALESCE(m.title, a.title) AS title
           FROM alerts a
           LEFT JOIN markets m ON m.market_id = a.market_id
           WHERE a.created_at >= $1
             AND NOT EXISTS (
                 SELECT 1 FROM alert_reviews ar WHERE ar.alert_id = a.alert_id
             )
           ORDER BY a.created_at DESC
           LIMIT 10""",
        since,
    )
    review_queue_triage_rows = await conn.fetch(
        """SELECT a.data_quality,
                  a.evidence,
                  a.raw_event_id,
                  a.trade_id::text AS trade_id
           FROM alerts a
           WHERE a.created_at >= $1
             AND NOT EXISTS (
                 SELECT 1 FROM alert_reviews ar WHERE ar.alert_id = a.alert_id
             )""",
        since,
    )
    review_queue_items = [
        _review_queue_item_with_flags(row)
        for row in review_queue_alerts
    ]
    review_queue_triage_summary = _triage_flag_summary(review_queue_triage_rows)

    review_outcomes = await conn.fetch(
        """WITH latest_reviews AS (
               SELECT DISTINCT ON (ar.alert_id)
                      ar.alert_id,
                      ar.label,
                      ar.false_positive_category,
                      ar.reviewed_at
               FROM alert_reviews ar
               ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
           )
           SELECT lr.label, COUNT(*) AS cnt
           FROM alerts a
           JOIN latest_reviews lr ON lr.alert_id = a.alert_id
           WHERE a.created_at >= $1
           GROUP BY lr.label
           ORDER BY cnt DESC, lr.label""",
        since,
    )
    false_positive_categories = await conn.fetch(
        """WITH latest_reviews AS (
               SELECT DISTINCT ON (ar.alert_id)
                      ar.alert_id,
                      ar.label,
                      ar.false_positive_category,
                      ar.reviewed_at
               FROM alert_reviews ar
               ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
           )
           SELECT COALESCE(lr.false_positive_category, 'uncategorized') AS category,
                  COUNT(*) AS cnt
           FROM alerts a
           JOIN latest_reviews lr ON lr.alert_id = a.alert_id
           WHERE a.created_at >= $1
             AND lr.label = 'fp'
           GROUP BY COALESCE(lr.false_positive_category, 'uncategorized')
           ORDER BY cnt DESC, category""",
        since,
    )
    unresolved_dead_letters_total = await conn.fetchval(
        """SELECT COUNT(*)
           FROM dead_letters
           WHERE resolved = false
             AND created_at >= $1""",
        since,
    )
    unresolved_dead_letters_by_stage = await conn.fetch(
        """SELECT failure_stage,
                  COALESCE(error_class, 'unknown') AS error_class,
                  COUNT(*) AS cnt
           FROM dead_letters
           WHERE resolved = false
             AND created_at >= $1
           GROUP BY failure_stage, COALESCE(error_class, 'unknown')
           ORDER BY cnt DESC, failure_stage, error_class
           LIMIT 10""",
        since,
    )
    recent_dead_letters = await conn.fetch(
        """SELECT created_at,
                  venue_code,
                  failure_stage,
                  COALESCE(error_class, 'unknown') AS error_class,
                  error_message
           FROM dead_letters
           WHERE resolved = false
             AND created_at >= $1
           ORDER BY created_at DESC
           LIMIT 10""",
        since,
    )
    open_incidents_total = await conn.fetchval(
        "SELECT COUNT(*) FROM data_quality_incidents WHERE status = 'open'"
    )
    open_incidents_by_type = await conn.fetch(
        """SELECT severity, incident_type, COUNT(*) AS cnt
           FROM data_quality_incidents
           WHERE status = 'open'
           GROUP BY severity, incident_type
           ORDER BY cnt DESC, severity, incident_type
           LIMIT 10"""
    )
    open_incidents = await conn.fetch(
        """SELECT incident_id::text AS incident_id,
                  started_at,
                  venue_code,
                  severity,
                  incident_type,
                  summary
           FROM data_quality_incidents
           WHERE status = 'open'
           ORDER BY started_at DESC
           LIMIT 10"""
    )
    return {
        "total": total_row["total"] if total_row else 0,
        "by_severity": [dict(r) for r in by_severity],
        "by_rule": [dict(r) for r in by_rule],
        "by_venue": [dict(r) for r in by_venue],
        "top_markets": [dict(r) for r in top_markets],
        "recent_high": [dict(r) for r in recent_high],
        "review_queue": {
            "total": int(review_queue_total or 0),
            "alerts": review_queue_items,
            "triage_flags": review_queue_triage_summary,
        },
        "review_outcomes": {
            "reviewed_total": sum(int(r["cnt"]) for r in review_outcomes),
            "by_label": [dict(r) for r in review_outcomes],
            "false_positive_categories": [dict(r) for r in false_positive_categories],
        },
        "data_gaps": {
            "unresolved_dead_letters": {
                "total": int(unresolved_dead_letters_total or 0),
                "by_stage": [dict(r) for r in unresolved_dead_letters_by_stage],
                "recent": [dict(r) for r in recent_dead_letters],
            },
            "open_data_quality_incidents": {
                "total": int(open_incidents_total or 0),
                "by_type": [dict(r) for r in open_incidents_by_type],
                "recent": [dict(r) for r in open_incidents],
            },
        },
        "since": since.isoformat(),
    }


async def load_suppression_cache(
    conn,
    *,
    window_seconds: int = 300,
) -> dict[tuple[str, str, str, str], "datetime"]:
    """Load recent alert history from DB to pre-populate the in-memory suppression cache.

    Returns {(venue_code, market_id_str, rule_id, outcome_key_or_empty): last_fired_at}
    for all alerts fired within the last `window_seconds` seconds.

    Key shape matches the live suppression key in runner.py:
    (venue_code, str(market_id), rule_id, outcome_key or "").
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    rows = await conn.fetch(
        """SELECT venue_code, market_id::text, rule_key,
                  COALESCE(outcome_key, '') AS outcome_key,
                  MAX(created_at) AS last_fired_at
           FROM alerts
           WHERE created_at >= $1
           GROUP BY venue_code, market_id, rule_key, outcome_key""",
        cutoff,
    )
    return {
        (row["venue_code"], row["market_id"], row["rule_key"], row["outcome_key"]): row["last_fired_at"]
        for row in rows
    }
