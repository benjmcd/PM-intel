from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import asyncpg

from pmfi.alert_triage import parse_evidence, triage_flags
from pmfi.domain import AlertDecision

ALLOWED_REVIEW_LABELS = {"tp", "fp", "noise"}
ALLOWED_PACKET_REVIEW_STATES = {"reviewed", "unreviewed"}
DIRECTIONAL_OUTCOME_RULES = ("directional_cluster_v1", "momentum_v1")


def _dedupe_key(
    decision: AlertDecision,
    *,
    venue_code: str,
    market_id: str | None,
    outcome_key: str | None,
    hour_bucket: str | None = None,
    window_bucket: int | str | None = None,
) -> str:
    bucket = window_bucket if window_bucket is not None else hour_bucket
    if bucket is None:
        raise ValueError("hour_bucket or window_bucket is required")
    raw = f"{venue_code}:{market_id}:{outcome_key}:{decision.rule_id}:{decision.rule_version}:{bucket}"
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
    suppression_window_seconds: int = 300,
) -> str | None:
    if not decision.emit_alert:
        return None
    ts = event_ts or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    window_seconds = max(1, int(suppression_window_seconds))
    window_bucket = int(ts.timestamp() // window_seconds)
    window_delta = timedelta(seconds=window_seconds)
    candidate_dedupe_keys = [
        _dedupe_key(
            decision,
            venue_code=venue_code,
            market_id=market_id,
            outcome_key=outcome_key,
            window_bucket=bucket,
        )
        for bucket in (window_bucket - 1, window_bucket, window_bucket + 1)
    ]
    existing = await conn.fetchrow(
        """WITH candidates AS (
               SELECT a.alert_id::text AS alert_id,
                      a.created_at,
                      COALESCE(
                          nt.exchange_ts,
                          nt.received_at,
                          re.exchange_ts,
                          re.received_at,
                          NULLIF(a.evidence->>'suppression_event_ts', '')::timestamptz
                      ) AS suppression_event_ts
               FROM alerts a
               LEFT JOIN normalized_trades nt ON a.trade_id = nt.trade_id
               LEFT JOIN raw_events re ON a.raw_event_id = re.raw_event_id
               WHERE a.dedupe_key = ANY($1::text[])
           )
           SELECT alert_id
           FROM candidates
           WHERE suppression_event_ts >= $2
             AND suppression_event_ts <= $3
           ORDER BY suppression_event_ts DESC, created_at DESC
           LIMIT 1""",
        candidate_dedupe_keys,
        ts - window_delta,
        ts + window_delta,
    )
    if existing:
        return None
    dedupe = _dedupe_key(
        decision,
        venue_code=venue_code,
        market_id=market_id,
        outcome_key=outcome_key,
        window_bucket=window_bucket,
    )
    existing = await conn.fetchrow("SELECT alert_id::text FROM alerts WHERE dedupe_key=$1", dedupe)
    if existing:
        return None
    # Coerce trade_id to string for uuid cast; None stays None.
    trade_id_str = str(trade_id) if trade_id is not None else None
    evidence = dict(decision.evidence)
    evidence["suppression_event_ts"] = ts.isoformat()
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
            json.dumps(evidence), decision.data_quality,
            raw_event_id, trade_id_str,
        )
        return str(row["alert_id"])
    except asyncpg.UniqueViolationError:
        return None


async def resolve_alert_id(conn, alert_id_or_prefix: str) -> str | None:
    """Resolve a full UUID or short prefix to a full alert_id string, or None."""
    if len(alert_id_or_prefix) == 36 and alert_id_or_prefix.count('-') == 4:
        return alert_id_or_prefix
    rows = await conn.fetch(
        "SELECT alert_id::text FROM alerts WHERE alert_id::text LIKE $1 || '%' ORDER BY fired_at DESC LIMIT 2",
        alert_id_or_prefix,
    )
    if len(rows) != 1:
        return None
    return rows[0]["alert_id"]


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


def _iso_or_none(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _review_packet_alert(row) -> dict:  # noqa: ANN001
    from pmfi.dashboard.queries import _summarize_evidence

    item = dict(row)
    evidence = parse_evidence(item.pop("evidence", None))
    alert = {
        "alert_id": item["alert_id"],
        "short_id": str(item["alert_id"])[:8],
        "fired_at": _iso_or_none(item.get("fired_at")),
        "created_at": _iso_or_none(item.get("created_at")),
        "rule_key": item.get("rule_key"),
        "rule_version": item.get("rule_version"),
        "severity": item.get("severity"),
        "confidence": item.get("confidence"),
        "score": item.get("score"),
        "venue_code": item.get("venue_code"),
        "outcome_key": item.get("outcome_key"),
        "outcome_label": item.get("outcome_label"),
        "data_quality": item.get("data_quality"),
        "title": item.get("market_title") or item.get("title"),
        "venue_market_id": item.get("venue_market_id"),
        "raw_event_id": item.get("raw_event_id"),
        "trade_id": item.get("trade_id"),
        "evidence_summary": _summarize_evidence(evidence),
        "evidence": evidence,
        "triage_flags": triage_flags(item, evidence),
        "latest_review": {
            "review_id": item.get("review_id"),
            "label": item.get("review_label"),
            "category": item.get("review_category"),
            "notes": item.get("review_notes"),
            "reviewed_by": item.get("reviewed_by"),
            "reviewed_at": _iso_or_none(item.get("reviewed_at")),
        },
    }
    return alert


def _packet_where(
    *,
    since: datetime,
    rule: str | None,
    review_state: str,
    review_label: str | None,
    category: str | None,
) -> tuple[str, list]:
    conditions = ["a.created_at >= $1"]
    params: list = [since]
    idx = 2
    if rule:
        conditions.append(f"a.rule_key = ${idx}")
        params.append(rule)
        idx += 1
    if review_state == "unreviewed":
        conditions.append("lr.alert_id IS NULL")
    else:
        conditions.append("lr.alert_id IS NOT NULL")
    if review_label:
        conditions.append(f"lr.label = ${idx}")
        params.append(review_label)
        idx += 1
    if category:
        conditions.append(f"lr.false_positive_category = ${idx}")
        params.append(category)
    return " AND ".join(conditions), params


def _normalized_outcome(value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() if text else None


def _directional_outcome_audit_row(row) -> dict:  # noqa: ANN001
    item = dict(row)
    evidence = parse_evidence(item.pop("evidence", None))
    stored_outcome = item.get("outcome_key")
    dominant_side = evidence.get("dominant_side")
    stored_norm = _normalized_outcome(stored_outcome)
    dominant_norm = _normalized_outcome(dominant_side)
    if dominant_norm is None:
        status = "missing_dominant_side"
    elif stored_norm == dominant_norm:
        status = "match"
    else:
        status = "mismatch"
    return {
        "alert_id": item["alert_id"],
        "short_id": str(item["alert_id"])[:8],
        "fired_at": _iso_or_none(item.get("fired_at")),
        "rule_key": item.get("rule_key"),
        "venue_code": item.get("venue_code"),
        "market_id": item.get("market_id"),
        "title": item.get("market_title") or item.get("title"),
        "stored_outcome_key": stored_outcome,
        "dominant_side": dominant_side,
        "evidence_outcome_key": evidence.get("outcome_key"),
        "directional_side": evidence.get("directional_side"),
        "status": status,
    }


async def get_directional_outcome_audit(
    conn,
    *,
    since: datetime,
    until: datetime | None = None,
    rules: list[str] | tuple[str, ...] | None = None,
    limit: int = 50,
) -> dict:
    """Audit directional alert rows against detected dominant_side evidence."""
    if since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    if until is not None and until.tzinfo is None:
        raise ValueError("until must be timezone-aware")
    if until is not None and since >= until:
        raise ValueError("since must be before until")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")
    selected_rules = list(rules or DIRECTIONAL_OUTCOME_RULES)
    if not selected_rules:
        raise ValueError("at least one rule is required")

    conditions = ["a.rule_key = ANY($1::text[])", "a.fired_at >= $2"]
    params: list = [selected_rules, since]
    if until is not None:
        params.append(until)
        conditions.append(f"a.fired_at <= ${len(params)}")
    params.append(limit)
    where = " AND ".join(conditions)
    rows = await conn.fetch(
        """SELECT a.alert_id::text AS alert_id,
                  a.fired_at,
                  a.rule_key,
                  a.venue_code,
                  a.market_id::text AS market_id,
                  a.outcome_key,
                  a.title,
                  a.evidence,
                  COALESCE(m.title, a.title) AS market_title
           FROM alerts a
           LEFT JOIN markets m ON m.market_id = a.market_id
           WHERE """
        + where
        + f" ORDER BY a.fired_at DESC LIMIT ${len(params)}",
        *params,
    )
    audited_rows = [_directional_outcome_audit_row(row) for row in rows]
    status_counts = Counter(row["status"] for row in audited_rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": {
            "since": since.isoformat(),
            "until": until.isoformat() if until else None,
            "rules": selected_rules,
            "limit": limit,
        },
        "totals": {
            "checked": len(audited_rows),
            "matched": int(status_counts.get("match", 0)),
            "mismatches": int(status_counts.get("mismatch", 0)),
            "missing_dominant_side": int(status_counts.get("missing_dominant_side", 0)),
        },
        "rows": audited_rows,
    }


def _lineage_integrity_row(row) -> dict:  # noqa: ANN001
    item = dict(row)
    alert_id = str(item["alert_id"])
    fired_at = item.get("fired_at")
    return {
        "alert_id": alert_id,
        "short_id": alert_id[:8],
        "fired_at": _iso_or_none(fired_at),
        "rule_key": item.get("rule_key"),
        "venue_code": item.get("venue_code"),
        "raw_event_id": item.get("raw_event_id"),
        "trade_id": item.get("trade_id"),
        "raw_event_missing": bool(item.get("raw_event_missing")),
        "trade_missing": bool(item.get("trade_missing")),
    }


async def get_alert_lineage_integrity(
    conn,
    *,
    since: datetime | None = None,
    limit: int = 50,
) -> dict:
    """Report alerts whose informational raw/trade lineage references are dangling."""
    if since is not None and since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")

    conditions: list[str] = []
    params: list = []
    if since is not None:
        params.append(since)
        conditions.append(f"a.created_at >= ${len(params)}")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    candidates_cte = (
        "WITH candidates AS ("
        "SELECT a.alert_id::text AS alert_id, "
        "a.fired_at, "
        "a.created_at, "
        "a.rule_key, "
        "a.venue_code, "
        "a.raw_event_id, "
        "a.trade_id::text AS trade_id, "
        "(a.raw_event_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM raw_events r WHERE r.raw_event_id = a.raw_event_id"
        ")) AS raw_event_missing, "
        "(a.trade_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM normalized_trades nt WHERE nt.trade_id = a.trade_id"
        ")) AS trade_missing "
        f"FROM alerts a {where}"
        ") "
    )
    totals_row = await conn.fetchrow(
        candidates_cte
        + "SELECT "
        + "COUNT(*) FILTER (WHERE raw_event_id IS NOT NULL OR trade_id IS NOT NULL) AS alerts_with_lineage, "
        + "COUNT(*) FILTER (WHERE raw_event_missing OR trade_missing) AS alerts_with_orphans, "
        + "COUNT(*) FILTER (WHERE raw_event_missing) AS raw_event_orphans, "
        + "COUNT(*) FILTER (WHERE trade_missing) AS trade_orphans "
        + "FROM candidates",
        *params,
    )
    row_params = [*params, limit]
    rows = await conn.fetch(
        candidates_cte
        + "SELECT alert_id, fired_at, rule_key, venue_code, raw_event_id, trade_id, "
        + "raw_event_missing, trade_missing "
        + "FROM candidates "
        + "WHERE raw_event_missing OR trade_missing "
        + f"ORDER BY fired_at DESC, alert_id DESC LIMIT ${len(row_params)}",
        *row_params,
    )
    totals = {
        "alerts_with_lineage": int(totals_row["alerts_with_lineage"] or 0),
        "alerts_with_orphans": int(totals_row["alerts_with_orphans"] or 0),
        "raw_event_orphans": int(totals_row["raw_event_orphans"] or 0),
        "trade_orphans": int(totals_row["trade_orphans"] or 0),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": totals["alerts_with_orphans"] == 0,
        "filters": {
            "since": since.isoformat() if since else None,
            "limit": limit,
        },
        "totals": totals,
        "rows": [_lineage_integrity_row(row) for row in rows],
    }


async def get_review_packet(
    conn,
    *,
    since: datetime,
    rule: str | None = None,
    review_state: str = "reviewed",
    review_label: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> dict:
    """Build a read-only local review packet for reviewed or unreviewed alert cohorts."""
    if review_state not in ALLOWED_PACKET_REVIEW_STATES:
        raise ValueError("review_state must be one of: reviewed, unreviewed")
    if review_label is not None and review_label not in ALLOWED_REVIEW_LABELS:
        raise ValueError("review_label must be one of: tp, fp, noise")
    if review_state == "unreviewed" and (review_label is not None or category is not None):
        raise ValueError("unreviewed packets cannot filter by review label or category")
    if limit <= 0:
        raise ValueError("limit must be a positive integer")

    where, params = _packet_where(
        since=since,
        rule=rule,
        review_state=review_state,
        review_label=review_label,
        category=category,
    )
    latest_reviews_cte = (
        "WITH latest_reviews AS ("
        "SELECT DISTINCT ON (ar.alert_id) "
        "ar.review_id::text AS review_id, "
        "ar.alert_id, "
        "ar.label, "
        "ar.false_positive_category, "
        "ar.notes, "
        "ar.reviewed_by, "
        "ar.reviewed_at "
        "FROM alert_reviews ar "
        "ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC"
        ") "
    )
    joined_from = (
        "FROM alerts a "
        "LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id "
    )
    rows = await conn.fetch(
        latest_reviews_cte
        + """SELECT a.alert_id::text AS alert_id,
                    a.fired_at,
                    a.created_at,
                    a.rule_key,
                    a.rule_version,
                    a.severity,
                    a.confidence,
                    a.score,
                    a.venue_code,
                    a.outcome_key,
                    a.data_quality,
                    a.title,
                    a.evidence,
                    a.raw_event_id,
                    a.trade_id::text AS trade_id,
                    COALESCE(m.title, a.title) AS market_title,
                    m.venue_market_id,
                    mo.outcome_label,
                    lr.review_id,
                    lr.label AS review_label,
                    lr.false_positive_category AS review_category,
                    lr.notes AS review_notes,
                    lr.reviewed_by,
                    lr.reviewed_at
             """
        + joined_from
        + """LEFT JOIN markets m ON m.market_id = a.market_id
             LEFT JOIN market_outcomes mo
               ON mo.market_id = a.market_id AND mo.outcome_key = a.outcome_key
             WHERE """
        + where
        + f" ORDER BY lr.reviewed_at DESC NULLS LAST, a.created_at DESC LIMIT ${len(params) + 1}",
        *params,
        limit,
    )
    total = await conn.fetchval(
        latest_reviews_cte
        + "SELECT COUNT(*) "
        + joined_from
        + "WHERE "
        + where,
        *params,
    )
    by_label = await conn.fetch(
        latest_reviews_cte
        + "SELECT COALESCE(lr.label, 'unreviewed') AS label, COUNT(*) AS cnt "
        + joined_from
        + "WHERE "
        + where
        + " GROUP BY COALESCE(lr.label, 'unreviewed') ORDER BY cnt DESC, label",
        *params,
    )
    by_category = await conn.fetch(
        latest_reviews_cte
        + """SELECT CASE
                      WHEN lr.alert_id IS NULL THEN 'unreviewed'
                      ELSE COALESCE(lr.false_positive_category, 'uncategorized')
                    END AS category,
                    COUNT(*) AS cnt """
        + joined_from
        + "WHERE "
        + where
        + """ GROUP BY CASE
                      WHEN lr.alert_id IS NULL THEN 'unreviewed'
                      ELSE COALESCE(lr.false_positive_category, 'uncategorized')
                    END
             ORDER BY cnt DESC, category""",
        *params,
    )
    by_rule = await conn.fetch(
        latest_reviews_cte
        + "SELECT a.rule_key, COUNT(*) AS cnt "
        + joined_from
        + "WHERE "
        + where
        + " GROUP BY a.rule_key ORDER BY cnt DESC, a.rule_key",
        *params,
    )
    by_venue = await conn.fetch(
        latest_reviews_cte
        + "SELECT a.venue_code, COUNT(*) AS cnt "
        + joined_from
        + "WHERE "
        + where
        + " GROUP BY a.venue_code ORDER BY cnt DESC, a.venue_code",
        *params,
    )
    triage_rows = await conn.fetch(
        latest_reviews_cte
        + """SELECT a.alert_id::text AS alert_id,
                    a.data_quality,
                    a.evidence,
                    a.raw_event_id,
                    a.trade_id::text AS trade_id """
        + joined_from
        + "WHERE "
        + where,
        *params,
    )

    alert_count = await conn.fetchval(
        "SELECT COUNT(*) FROM alerts WHERE created_at >= $1",
        since,
    )
    raw_events = await conn.fetchval(
        "SELECT COUNT(*) FROM raw_events WHERE received_at >= $1",
        since,
    )
    normalized_trades = await conn.fetchval(
        "SELECT COUNT(*) FROM normalized_trades WHERE received_at >= $1",
        since,
    )
    unresolved_dead_letters = await conn.fetchval(
        "SELECT COUNT(*) FROM dead_letters WHERE resolved = false AND created_at >= $1",
        since,
    )
    open_incidents = await conn.fetchval(
        "SELECT COUNT(*) FROM data_quality_incidents WHERE status = 'open'"
    )

    packet = {
        "export_metadata": {
            "schema_version": "review_packet.v1",
            "local_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "since": since.isoformat(),
                "rule": rule,
                "review_state": review_state,
                "review_label": review_label,
                "category": category,
                "limit": limit,
            },
        },
        "cohort_totals": {
            "alerts": int(total or 0),
            "by_label": [dict(r) for r in by_label],
            "by_category": [dict(r) for r in by_category],
            "by_rule": [dict(r) for r in by_rule],
            "by_venue": [dict(r) for r in by_venue],
            "triage_flags": _triage_flag_summary(triage_rows),
        },
        "report_context": {
            "since": since.isoformat(),
            "alert_count": int(alert_count or 0),
            "raw_events": int(raw_events or 0),
            "normalized_trades": int(normalized_trades or 0),
            "unresolved_dead_letters": int(unresolved_dead_letters or 0),
            "open_data_quality_incidents": int(open_incidents or 0),
        },
        "alerts": [_review_packet_alert(row) for row in rows],
    }
    packet["reviewed_cohort_totals"] = packet["cohort_totals"]
    return packet


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
