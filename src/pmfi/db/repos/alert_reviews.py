from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


async def fetch_alert_context(conn: "asyncpg.Connection", alert_id: str) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT
            a.alert_id::text AS alert_id,
            a.rule_key,
            a.severity,
            a.confidence,
            a.title,
            a.summary,
            a.status,
            a.venue_code,
            a.market_id::text AS market_id,
            a.outcome_key,
            a.fired_at,
            a.acknowledged_at,
            a.resolved_at,
            m.title AS market_title
        FROM alerts a
        LEFT JOIN markets m ON m.market_id = a.market_id
        WHERE a.alert_id = $1::uuid
        """,
        alert_id,
    )
    return dict(row) if row else None


async def insert_alert_review(
    conn: "asyncpg.Connection",
    *,
    alert_id: str,
    label: str,
    false_positive_category: str | None,
    notes: str | None,
    reviewed_by: str | None,
) -> dict:
    row = await conn.fetchrow(
        """
        INSERT INTO alert_reviews
            (alert_id, label, false_positive_category, notes, reviewed_by)
        VALUES ($1::uuid, $2, $3, $4, $5)
        RETURNING
            review_id::text AS review_id,
            alert_id::text AS alert_id,
            label,
            false_positive_category,
            notes,
            reviewed_by,
            reviewed_at
        """,
        alert_id,
        label,
        false_positive_category,
        notes,
        reviewed_by,
    )
    return dict(row)


async def list_alert_reviews(
    conn: "asyncpg.Connection",
    *,
    limit: int,
    alert_id: str | None = None,
    label: str | None = None,
) -> list[dict]:
    conditions: list[str] = []
    params: list[object] = []
    if alert_id:
        params.append(alert_id)
        conditions.append(f"ar.alert_id = ${len(params)}::uuid")
    if label:
        params.append(label)
        conditions.append(f"ar.label = ${len(params)}")
    params.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await conn.fetch(
        f"""
        SELECT
            ar.review_id::text AS review_id,
            ar.alert_id::text AS alert_id,
            ar.label,
            ar.false_positive_category,
            ar.notes,
            ar.reviewed_by,
            ar.reviewed_at,
            a.rule_key,
            a.severity,
            a.confidence,
            a.title,
            a.summary,
            a.status,
            a.venue_code,
            a.market_id::text AS market_id,
            a.outcome_key,
            a.fired_at,
            m.title AS market_title
        FROM alert_reviews ar
        JOIN alerts a ON a.alert_id = ar.alert_id
        LEFT JOIN markets m ON m.market_id = a.market_id
        {where}
        ORDER BY ar.reviewed_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [dict(row) for row in rows]


async def summarize_false_positive_rate(
    conn: "asyncpg.Connection",
    *,
    since,
    bucket: str,
    limit: int,
    rule_key: str | None = None,
) -> list[dict]:
    bucket_expr = "NULL::timestamptz" if bucket == "all" else f"date_trunc('{bucket}', a.fired_at)"
    conditions = ["a.fired_at >= $1"]
    params: list[object] = [since]
    if rule_key:
        params.append(rule_key)
        conditions.append(f"a.rule_key = ${len(params)}")
    params.append(limit)
    where = " AND ".join(conditions)
    rows = await conn.fetch(
        f"""
        WITH latest_reviews AS (
            SELECT DISTINCT ON (ar.alert_id)
                ar.alert_id,
                ar.label,
                ar.reviewed_at,
                ar.review_id
            FROM alert_reviews ar
            ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
        )
        SELECT
            a.rule_key,
            {bucket_expr} AS bucket_start,
            COUNT(*)::int AS reviewed_count,
            COUNT(*) FILTER (WHERE lr.label = 'false_positive')::int AS false_positive_count,
            COUNT(*) FILTER (WHERE lr.label = 'true_positive')::int AS true_positive_count,
            COUNT(*) FILTER (WHERE lr.label = 'noise')::int AS noise_count,
            COUNT(*) FILTER (WHERE lr.label = 'unsure')::int AS unsure_count,
            COALESCE(
                COUNT(*) FILTER (WHERE lr.label = 'false_positive')::numeric
                / NULLIF(COUNT(*)::numeric, 0),
                0
            ) AS false_positive_rate
        FROM alerts a
        JOIN latest_reviews lr ON lr.alert_id = a.alert_id
        WHERE {where}
        GROUP BY a.rule_key, bucket_start
        ORDER BY bucket_start DESC NULLS LAST, a.rule_key
        LIMIT ${len(params)}
        """,
        *params,
    )
    return [dict(row) for row in rows]
