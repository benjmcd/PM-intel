from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import asyncpg

_VALID_LABELS = {"false_positive", "true_positive", "needs_review"}


async def record_review(
    conn: asyncpg.Connection,
    *,
    alert_id: str,
    label: str,
    false_positive_category: str | None = None,
    notes: str | None = None,
    reviewed_by: str | None = None,
) -> str:
    """Insert a review row for an alert and return review_id as text.

    Raises ValueError if the label is invalid or the alert does not exist.
    """
    if label not in _VALID_LABELS:
        raise ValueError(f"label must be one of {sorted(_VALID_LABELS)!r}, got {label!r}")

    exists = await conn.fetchval(
        "SELECT 1 FROM alerts WHERE alert_id = $1::uuid", alert_id
    )
    if not exists:
        raise ValueError(f"alert not found: {alert_id!r}")

    row = await conn.fetchrow(
        """INSERT INTO alert_reviews
               (alert_id, label, false_positive_category, notes, reviewed_by)
           VALUES ($1::uuid, $2, $3, $4, $5)
           RETURNING review_id::text""",
        alert_id, label, false_positive_category, notes, reviewed_by,
    )
    return row["review_id"]


async def list_reviews(
    conn: asyncpg.Connection,
    *,
    limit: int = 50,
    label: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent reviews joined to alerts for rule_key + title."""
    params: list = []
    conditions: list[str] = []

    if label is not None:
        params.append(label)
        conditions.append(f"ar.label = ${len(params)}")

    params.append(limit)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT ar.review_id::text AS review_id,
               ar.alert_id::text  AS alert_id,
               ar.label,
               ar.false_positive_category,
               ar.notes,
               ar.reviewed_by,
               ar.reviewed_at,
               a.rule_key,
               a.title
          FROM alert_reviews ar
          JOIN alerts a ON a.alert_id = ar.alert_id
          {where}
         ORDER BY ar.reviewed_at DESC
         LIMIT ${len(params)}
    """
    rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def false_positive_rate_by_rule(
    conn: asyncpg.Connection,
    *,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per rule_key: total alerts vs false_positive reviews in the window, and the rate.

    Only alerts (and their reviews) within the `since` window are counted.
    When `since` is None the window is the last 30 days.
    """
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(days=30)

    rows = await conn.fetch(
        """
        SELECT a.rule_key,
               COUNT(DISTINCT a.alert_id)                          AS total_alerts,
               COUNT(DISTINCT ar.review_id) FILTER (
                   WHERE ar.label = 'false_positive'
               )                                                    AS false_positive_count,
               ROUND(
                   COUNT(DISTINCT ar.review_id) FILTER (
                       WHERE ar.label = 'false_positive'
                   )::numeric
                   / NULLIF(COUNT(DISTINCT a.alert_id), 0)
                   * 100,
                   2
               )                                                    AS false_positive_rate_pct
          FROM alerts a
          LEFT JOIN alert_reviews ar
               ON ar.alert_id = a.alert_id
         WHERE a.created_at >= $1
         GROUP BY a.rule_key
         ORDER BY false_positive_rate_pct DESC NULLS LAST, total_alerts DESC
        """,
        since,
    )
    return [dict(r) for r in rows]
