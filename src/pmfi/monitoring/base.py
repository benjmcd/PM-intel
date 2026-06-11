"""Tiny monitor framework.

Provides:
- record_incident   — writes a row to data_quality_incidents
- emit_monitor_alert — calls insert_alert for a monitor-sourced decision
- run_monitors       — runs all registered monitors; never raises
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg

from pmfi.domain import AlertDecision
from pmfi.db.repos.alerts import insert_alert

logger = logging.getLogger(__name__)


async def record_incident(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    market_id: str | None,
    incident_type: str,
    severity: str,
    summary: str,
    details: dict[str, Any],
) -> str:
    """Insert a row into data_quality_incidents and return the incident_id."""
    row = await conn.fetchrow(
        """INSERT INTO data_quality_incidents
               (venue_code, market_id, incident_type, severity, summary, details)
           VALUES ($1, $2::uuid, $3, $4, $5, $6::jsonb)
           RETURNING incident_id::text""",
        venue_code,
        market_id,
        incident_type,
        severity,
        summary,
        json.dumps(details),
    )
    return str(row["incident_id"])


async def emit_monitor_alert(
    conn: asyncpg.Connection,
    decision: AlertDecision,
    *,
    title: str,
    summary: str,
    venue_code: str,
    market_id: str | None = None,
) -> str | None:
    """Call insert_alert for a monitor-sourced decision."""
    return await insert_alert(
        conn,
        decision,
        title=title,
        summary=summary,
        venue_code=venue_code,
        market_id=market_id,
    )


async def run_monitors(
    pool: Any,
    *,
    now: datetime,
    venue_stale_seconds: int = 600,
    dead_letter_spike_min: int = 5,
    dead_letter_spike_ratio: float = 3.0,
) -> None:
    """Run all registered monitors.  Never raises — all errors are logged as warnings."""
    from pmfi.monitoring.data_quality import check_data_quality

    try:
        incidents = await check_data_quality(
            pool,
            now=now,
            venue_stale_seconds=venue_stale_seconds,
            dead_letter_spike_min=dead_letter_spike_min,
            dead_letter_spike_ratio=dead_letter_spike_ratio,
        )
        if incidents:
            logger.info("[monitors] data_quality check emitted %d incident(s)", len(incidents))
    except Exception as exc:
        logger.warning("[monitors] data_quality monitor failed (non-fatal): %s", exc)
