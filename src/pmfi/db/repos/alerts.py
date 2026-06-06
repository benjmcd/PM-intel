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
    title: str,
    summary: str,
    venue_code: str,
    market_id: str | None = None,
    outcome_key: str | None = None,
) -> str | None:
    if not decision.emit_alert:
        return None
    hour_bucket = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
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
