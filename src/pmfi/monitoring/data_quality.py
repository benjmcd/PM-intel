"""Data-quality degradation monitor (alert type #6).

For each venue, checks:
  (a) feed_silent  — no raw_event received within venue_stale_seconds of `now`,
                     but the venue HAS historically produced events.
  (b) dead_letter_spike — recent-window dead-letter count >= dead_letter_spike_min
                          AND >= dead_letter_spike_ratio * prior-window count.

Each firing:
  - Writes a data_quality_incidents row via record_incident.
  - Writes an alert row via emit_monitor_alert (deduped per hour-bucket).
  - Appends an incident dict to the returned list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pmfi.domain import AlertDecision
from pmfi.monitoring.base import emit_monitor_alert, record_incident

logger = logging.getLogger(__name__)

_RULE_ID = "data_quality_degradation_v1"
_RULE_VERSION = "alert_rules.v1"

# Dead-letter spike look-back windows
_RECENT_MINUTES = 10
_PRIOR_MINUTES = 60


async def check_data_quality(
    pool: Any,
    *,
    now: datetime,
    venue_stale_seconds: int = 600,
    dead_letter_spike_min: int = 5,
    dead_letter_spike_ratio: float = 3.0,
    active_venue_codes: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    """Check data-quality conditions for enabled venues.

    When active_venue_codes is provided, only enabled venues in that active set
    are checked. This keeps live-ingest monitors scoped to feeds actually being
    consumed while preserving all-enabled behavior for manual monitor runs.
    Returns a list of incident dicts (one per fired condition per venue).
    The pool is used to acquire a single connection for all queries.
    """
    incidents: list[dict] = []
    if active_venue_codes is not None:
        active_venue_codes = tuple(sorted({str(v) for v in active_venue_codes if v}))
        if not active_venue_codes:
            return incidents

    async with pool.acquire() as conn:
        if active_venue_codes is None:
            venues = await conn.fetch(
                "SELECT venue_code FROM venues WHERE enabled = true ORDER BY venue_code"
            )
        else:
            venues = await conn.fetch(
                """SELECT venue_code
                   FROM venues
                   WHERE enabled = true
                     AND venue_code = ANY($1::text[])
                   ORDER BY venue_code""",
                list(active_venue_codes),
            )

        for venue_row in venues:
            venue_code: str = venue_row["venue_code"]

            # ----------------------------------------------------------------
            # (a) feed_silent check
            # ----------------------------------------------------------------
            try:
                last_row = await conn.fetchrow(
                    """SELECT MAX(received_at) AS last_event
                       FROM raw_events
                       WHERE venue_code = $1""",
                    venue_code,
                )
                last_event: datetime | None = last_row["last_event"] if last_row else None

                if last_event is not None:
                    # Venue has history — check staleness
                    if last_event.tzinfo is None:
                        last_event = last_event.replace(tzinfo=timezone.utc)
                    if now.tzinfo is None:
                        now = now.replace(tzinfo=timezone.utc)
                    stale_seconds = (now - last_event).total_seconds()
                    if stale_seconds >= venue_stale_seconds:
                        evidence = {
                            "venue_code": venue_code,
                            "last_event_at": last_event.isoformat(),
                            "stale_seconds": int(stale_seconds),
                            "threshold_seconds": venue_stale_seconds,
                        }
                        decision = AlertDecision(
                            emit_alert=True,
                            rule_id=_RULE_ID,
                            rule_version=_RULE_VERSION,
                            severity="high",
                            confidence="high",
                            score=Decimal("0.9"),
                            reason_codes=("feed_silent",),
                            evidence=evidence,
                            data_quality="degraded",
                        )
                        summary = (
                            f"{venue_code}: no raw events for {int(stale_seconds)}s "
                            f"(threshold {venue_stale_seconds}s)"
                        )
                        incident_id = await record_incident(
                            conn,
                            venue_code=venue_code,
                            market_id=None,
                            incident_type="feed_silent",
                            severity="high",
                            summary=summary,
                            details=evidence,
                        )
                        await emit_monitor_alert(
                            conn,
                            decision,
                            title=f"Feed silent: {venue_code}",
                            summary=summary,
                            venue_code=venue_code,
                            market_id=None,
                            dedupe_context="feed_silent",
                        )
                        incident = {
                            "incident_id": incident_id,
                            "venue_code": venue_code,
                            "incident_type": "feed_silent",
                            "severity": "high",
                            "summary": summary,
                            "details": evidence,
                        }
                        incidents.append(incident)
                        logger.warning("[data_quality] %s", summary)
            except Exception as exc:
                logger.warning(
                    "[data_quality] feed_silent check failed for %s (non-fatal): %s",
                    venue_code, exc,
                )

            # ----------------------------------------------------------------
            # (b) dead_letter_spike check
            # ----------------------------------------------------------------
            try:
                recent_cutoff = now - timedelta(minutes=_RECENT_MINUTES)
                prior_cutoff = now - timedelta(minutes=_PRIOR_MINUTES)

                recent_row = await conn.fetchrow(
                    """SELECT COUNT(*) AS cnt
                       FROM dead_letters
                       WHERE venue_code = $1
                         AND created_at >= $2""",
                    venue_code, recent_cutoff,
                )
                recent_count: int = int(recent_row["cnt"]) if recent_row else 0

                prior_row = await conn.fetchrow(
                    """SELECT COUNT(*) AS cnt
                       FROM dead_letters
                       WHERE venue_code = $1
                         AND created_at >= $2
                         AND created_at < $3""",
                    venue_code, prior_cutoff, recent_cutoff,
                )
                prior_count: int = int(prior_row["cnt"]) if prior_row else 0

                if (
                    recent_count >= dead_letter_spike_min
                    and (
                        prior_count == 0
                        or recent_count >= dead_letter_spike_ratio * prior_count
                    )
                ):
                    ratio = (
                        recent_count / prior_count if prior_count > 0 else float("inf")
                    )
                    evidence = {
                        "venue_code": venue_code,
                        "recent_count": recent_count,
                        "prior_count": prior_count,
                        "ratio": round(ratio, 2) if ratio != float("inf") else None,
                        "recent_window_minutes": _RECENT_MINUTES,
                        "prior_window_minutes": _PRIOR_MINUTES,
                        "threshold_min": dead_letter_spike_min,
                        "threshold_ratio": dead_letter_spike_ratio,
                    }
                    decision = AlertDecision(
                        emit_alert=True,
                        rule_id=_RULE_ID,
                        rule_version=_RULE_VERSION,
                        severity="medium",
                        confidence="medium",
                        score=Decimal("0.7"),
                        reason_codes=("dead_letter_spike",),
                        evidence=evidence,
                        data_quality="degraded",
                    )
                    summary = (
                        f"{venue_code}: dead-letter spike {recent_count} in last "
                        f"{_RECENT_MINUTES}m (prior {prior_count} in {_PRIOR_MINUTES}m)"
                    )
                    incident_id = await record_incident(
                        conn,
                        venue_code=venue_code,
                        market_id=None,
                        incident_type="dead_letter_spike",
                        severity="medium",
                        summary=summary,
                        details=evidence,
                    )
                    await emit_monitor_alert(
                        conn,
                        decision,
                        title=f"Dead-letter spike: {venue_code}",
                        summary=summary,
                        venue_code=venue_code,
                        market_id=None,
                        dedupe_context="dead_letter_spike",
                    )
                    incident = {
                        "incident_id": incident_id,
                        "venue_code": venue_code,
                        "incident_type": "dead_letter_spike",
                        "severity": "medium",
                        "summary": summary,
                        "details": evidence,
                    }
                    incidents.append(incident)
                    logger.warning("[data_quality] %s", summary)
            except Exception as exc:
                logger.warning(
                    "[data_quality] dead_letter_spike check failed for %s (non-fatal): %s",
                    venue_code, exc,
                )

    return incidents
