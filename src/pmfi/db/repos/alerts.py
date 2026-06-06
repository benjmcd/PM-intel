from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from decimal import Decimal
import asyncpg
from pmfi.domain import AlertDecision

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
    try:
        row = await conn.fetchrow(
            """INSERT INTO alerts
               (dedupe_key, rule_key, rule_version, venue_code, market_id,
                outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
               VALUES ($1,$2,$3,$4,$5::uuid,$6,$7,$8,$9,$10,$11,$12::jsonb,$13)
               RETURNING alert_id::text""",
            dedupe, decision.rule_id, decision.rule_version, venue_code,
            market_id, outcome_key, decision.severity, decision.confidence,
            decision.score, title, summary,
            json.dumps(decision.evidence), decision.data_quality,
        )
        return str(row["alert_id"])
    except asyncpg.UniqueViolationError:
        return None


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
    sql = f"""SELECT alert_id, rule_id, severity, title, summary, venue_code, outcome_key,
                     confidence, data_quality, created_at, hour_bucket
              FROM alerts {where} ORDER BY created_at DESC LIMIT ${len(params)}"""
    rows = await conn.fetch(sql, *params)
    return [dict(row) for row in rows]


async def get_alert_summary(conn, *, since: "datetime | None" = None) -> dict:
    """Get aggregated alert summary for reporting.

    Returns counts by severity, venue, rule_id, and top markets.
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
        "SELECT rule_id, COUNT(*) AS cnt FROM alerts WHERE created_at >= $1 GROUP BY rule_id ORDER BY cnt DESC",
        since,
    )
    by_venue = await conn.fetch(
        "SELECT venue_code, COUNT(*) AS cnt FROM alerts WHERE created_at >= $1 GROUP BY venue_code ORDER BY cnt DESC",
        since,
    )
    top_markets = await conn.fetch(
        """SELECT title, COUNT(*) AS cnt, MAX(severity) AS max_severity
           FROM alerts WHERE created_at >= $1 GROUP BY title ORDER BY cnt DESC LIMIT 10""",
        since,
    )
    recent_high = await conn.fetch(
        """SELECT rule_id, severity, title, created_at FROM alerts
           WHERE created_at >= $1 AND severity IN ('high', 'medium')
           ORDER BY created_at DESC LIMIT 10""",
        since,
    )
    return {
        "total": total_row["total"] if total_row else 0,
        "by_severity": [dict(r) for r in by_severity],
        "by_rule": [dict(r) for r in by_rule],
        "by_venue": [dict(r) for r in by_venue],
        "top_markets": [dict(r) for r in top_markets],
        "recent_high": [dict(r) for r in recent_high],
        "since": since.isoformat(),
    }


async def load_suppression_cache(
    conn,
    *,
    window_seconds: int = 300,
) -> dict[tuple[str, str, str], "datetime"]:
    """Load recent alert history from DB to pre-populate the in-memory suppression cache.

    Returns {(venue_code, market_id_str, rule_id): last_fired_at} for all alerts
    fired within the last `window_seconds` seconds.
    """
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    rows = await conn.fetch(
        """SELECT venue_code, market_id::text, rule_id, MAX(created_at) AS last_fired_at
           FROM alerts
           WHERE created_at >= $1
           GROUP BY venue_code, market_id, rule_id""",
        cutoff,
    )
    return {
        (row["venue_code"], row["market_id"], row["rule_id"]): row["last_fired_at"]
        for row in rows
    }
