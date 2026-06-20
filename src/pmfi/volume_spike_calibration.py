from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pmfi.calibration import (
    VOLUME_SPIKE_RULE,
    VolumeSpikeCandidate,
    build_volume_spike_candidate_rules,
    summarize_volume_spike_calibration,
)


async def fetch_volume_spike_review_index(
    pool: object,
    *,
    since_dt: datetime,
    until_dt: datetime | None,
    venue: str | None,
    market: str | None,
) -> dict[int, dict[str, Any]]:
    conditions = [
        "a.rule_key = $1",
        "a.raw_event_id IS NOT NULL",
        "COALESCE(r.exchange_ts, r.received_at) >= $2",
    ]
    params: list[object] = [VOLUME_SPIKE_RULE, since_dt]

    def _add(expr: str, value: object) -> None:
        params.append(value)
        conditions.append(expr.replace("?", f"${len(params)}"))

    if until_dt is not None:
        _add("COALESCE(r.exchange_ts, r.received_at) <= ?", until_dt)
    if venue is not None:
        _add("r.venue_code = ?", venue)
    if market is not None:
        _add("r.venue_market_id = ?", market)

    where_sql = " AND ".join(conditions)
    rows = await pool.fetch(  # type: ignore[attr-defined]
        f"""
        WITH latest_reviews AS (
            SELECT DISTINCT ON (ar.alert_id)
                   ar.alert_id,
                   ar.label AS review_label,
                   ar.false_positive_category AS review_category,
                   ar.reviewed_at,
                   ar.review_id
            FROM alert_reviews ar
            ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC
        )
        SELECT a.raw_event_id,
               a.alert_id::text AS alert_id,
               a.trade_id::text AS trade_id,
               lr.review_label,
               lr.review_category,
               lr.reviewed_at
        FROM alerts a
        JOIN raw_events r ON r.raw_event_id = a.raw_event_id
        LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id
        WHERE {where_sql}
        ORDER BY a.raw_event_id, a.fired_at, a.alert_id
        """,
        *params,
    )
    index: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        raw_event_id = item.get("raw_event_id")
        if raw_event_id is None:
            continue
        item["raw_event_id"] = int(raw_event_id)
        index.setdefault(int(raw_event_id), item)
    return index


async def run_volume_spike_calibration_replay(
    pool: object,
    *,
    base_rules_config: dict[str, Any],
    since_dt: datetime,
    until_dt: datetime | None,
    limit: int,
    venue: str | None,
    market: str | None,
    candidate: VolumeSpikeCandidate,
    cold_start: bool = False,
    details_limit: int = 10,
    delta_records_limit: int | None = None,
) -> dict[str, Any]:
    from pmfi import replay

    candidate_rules = build_volume_spike_candidate_rules(base_rules_config, candidate)
    replay_kwargs = {
        "limit": limit,
        "start_ts": since_dt,
        "end_ts": until_dt,
        "venue": venue,
        "market": market,
        "persist": False,
        "seed": not cold_start,
        "print_summary": False,
    }
    current_results = await replay.replay_from_db(
        pool,
        rules_config=base_rules_config,
        **replay_kwargs,
    )
    candidate_results = await replay.replay_from_db(
        pool,
        rules_config=candidate_rules,
        **replay_kwargs,
    )
    review_index = await fetch_volume_spike_review_index(
        pool,
        since_dt=since_dt,
        until_dt=until_dt,
        venue=venue,
        market=market,
    )
    summary = summarize_volume_spike_calibration(
        current_results,
        candidate_results,
        candidate=candidate,
        review_index_by_raw_event_id=review_index,
        details_limit=details_limit,
        delta_records_limit=delta_records_limit,
    )
    summary["filters"] = {
        "since": since_dt.isoformat(),
        "until": until_dt.isoformat() if until_dt else None,
        "limit": limit,
        "venue": venue,
        "market": market,
        "cold_start": bool(cold_start),
        "details_limit": details_limit,
    }
    return summary


def build_volume_spike_calibration_packet(
    summary: dict[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    comparison = summary.get("comparison") or {}
    return {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "local_only": True,
            "validate_only": True,
            "generated_at": generated.isoformat(),
            "source_schema_version": summary.get("schema_version"),
            "filters": summary.get("filters") or {},
            "candidate": summary.get("candidate") or {},
            "record_counts": {
                "removed_volume_spike_alerts": comparison.get("removed_volume_spike_alerts", 0),
                "added_volume_spike_alerts": comparison.get("added_volume_spike_alerts", 0),
                "removed_volume_spike_records": len(
                    comparison.get("removed_volume_spike_records") or []
                ),
                "added_volume_spike_records": len(
                    comparison.get("added_volume_spike_records") or []
                ),
                "removed_delta_records_truncated": bool(
                    comparison.get("removed_delta_records_truncated")
                ),
                "added_delta_records_truncated": bool(
                    comparison.get("added_delta_records_truncated")
                ),
            },
        },
        "calibration_summary": summary,
    }


def insufficient_volume_spike_evidence_reason(summary: dict[str, Any]) -> str | None:
    current = summary.get("current") or {}
    if int(current.get("normalized_trades") or 0) == 0:
        return "no normalized trades"
    if int(current.get("volume_spike_alerts") or 0) == 0:
        return "no current volume_spike_v1 alerts"
    return None
