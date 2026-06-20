from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any

from pmfi.calibration_packets import calibration_packet_review_queue

CLUSTER_REVIEW_ASSESSMENTS = (
    "noise",
    "false-positive",
    "true-positive-risk",
    "uncertain",
)


def calibration_cluster_review_root() -> Path:
    from pmfi.config import ROOT

    return ROOT / "reports" / "calibration-cluster-reviews"


def resolve_calibration_cluster_review_file(name: str) -> Path:
    if not name or "/" in name or "\\" in name:
        raise ValueError("cluster review name must be a direct filename")
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".json":
        raise ValueError("cluster review name must be a direct .json filename")

    root = calibration_cluster_review_root().resolve()
    review_path = (root / candidate.name).resolve()
    try:
        review_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "cluster review name must stay inside calibration cluster review root"
        ) from exc
    return review_path


def list_calibration_cluster_review_files() -> list[dict[str, Any]]:
    root = calibration_cluster_review_root()
    if not root.is_dir():
        return []

    reviews: list[tuple[float, str, dict[str, Any]]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        stat = path.stat()
        reviews.append((
            stat.st_mtime,
            path.name,
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime,
                    timezone.utc,
                ).isoformat(),
            },
        ))
    reviews.sort(key=lambda item: (-item[0], item[1]))
    return [review for _, _, review in reviews]


def load_calibration_cluster_review(name: str) -> dict[str, Any]:
    review_path = resolve_calibration_cluster_review_file(name)
    if not review_path.is_file():
        raise FileNotFoundError(name)
    record = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise TypeError("calibration cluster review must be a JSON object")
    return record


def _assessment_implication(assessment: str) -> str:
    if assessment in {"noise", "false-positive"}:
        return (
            "packet-level support only; candidate still needs all unmatched "
            "rows reviewed and a separate calibration decision before any "
            "config mutation."
        )
    if assessment == "true-positive-risk":
        return (
            "candidate is blocked for this cluster until the rule shape is "
            "narrowed or stronger evidence disproves true-positive risk."
        )
    return "needs more packet/raw-event evidence before candidate readiness."


def _raw_event_ids(rows: list[dict[str, Any]]) -> list[Any]:
    values: list[Any] = []
    seen: set[str] = set()
    for row in rows:
        raw_event_id = row.get("raw_event_id")
        if raw_event_id is None:
            continue
        key = str(raw_event_id)
        if key in seen:
            continue
        seen.add(key)
        values.append(raw_event_id)
    return values


def _raw_event_id_set(values: list[Any]) -> set[str]:
    return {str(value) for value in values if value is not None and value != ""}


def _packet_names_from_record(record: dict[str, Any]) -> list[str]:
    selection = record.get("packet_selection") or {}
    names = selection.get("names") or []
    return [str(name) for name in names if name is not None]


def _generated_at_key(record: dict[str, Any]) -> str:
    return str(record.get("generated_at") or "")


def _numeric_value(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _range(values: list[Any]) -> tuple[Any, Any]:
    present = [value for value in values if value is not None and value != ""]
    if not present:
        return None, None
    return min(present), max(present)


def _raw_lookup_trade_profile(raw_event_lookup: dict[str, Any]) -> dict[str, Any]:
    rows = raw_event_lookup.get("rows") or []
    if not isinstance(rows, list):
        rows = []

    side_counts: Counter[str] = Counter()
    outcome_counts: Counter[str] = Counter()
    capital_values: list[float] = []
    price_values: list[float] = []
    exchange_ts_values: list[Any] = []
    trade_row_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        trade = row.get("trade") or {}
        if not isinstance(trade, dict):
            trade = {}
        has_trade_fact = any(
            trade.get(key) not in (None, "")
            for key in (
                "trade_id",
                "venue_trade_id",
                "outcome_key",
                "directional_side",
                "price",
                "contracts",
                "capital_at_risk_usd",
                "payout_notional_usd",
            )
        )
        if not has_trade_fact:
            continue
        trade_row_count += 1
        directional_side = trade.get("directional_side")
        if directional_side not in (None, ""):
            side_counts[str(directional_side)] += 1
        outcome_key = trade.get("outcome_key")
        if outcome_key not in (None, ""):
            outcome_counts[str(outcome_key)] += 1
        capital = _numeric_value(trade.get("capital_at_risk_usd"))
        if capital is not None:
            capital_values.append(capital)
        price = _numeric_value(trade.get("price"))
        if price is not None:
            price_values.append(price)
        exchange_ts = row.get("exchange_ts")
        if hasattr(exchange_ts, "isoformat"):
            exchange_ts = exchange_ts.isoformat()
        if exchange_ts not in (None, ""):
            exchange_ts_values.append(str(exchange_ts))

    capital_min, capital_max = _range(capital_values)
    price_min, price_max = _range(price_values)
    exchange_min, exchange_max = _range(exchange_ts_values)
    return {
        "raw_event_lookup_trade_row_count": trade_row_count,
        "raw_event_lookup_directional_side_counts": dict(sorted(side_counts.items())),
        "raw_event_lookup_outcome_key_counts": dict(sorted(outcome_counts.items())),
        "raw_event_lookup_capital_at_risk_usd_min": capital_min,
        "raw_event_lookup_capital_at_risk_usd_max": capital_max,
        "raw_event_lookup_price_min": price_min,
        "raw_event_lookup_price_max": price_max,
        "raw_event_lookup_exchange_ts_min": exchange_min,
        "raw_event_lookup_exchange_ts_max": exchange_max,
    }


def _raw_lookup_payload_status(summary: dict[str, Any]) -> str:
    if not summary.get("raw_event_lookup_embedded"):
        return "not-embedded"
    if summary.get("raw_event_lookup_include_payload") is True:
        return "full-payload"
    return "preview-only"


def _candidate_readiness(summary: dict[str, Any]) -> dict[str, Any]:
    assessment = str(summary.get("assessment") or "")
    raw_lookup_embedded = bool(summary.get("raw_event_lookup_embedded"))
    missing_count = int(summary.get("raw_event_lookup_missing_count") or 0)
    trade_row_count = int(summary.get("raw_event_lookup_trade_row_count") or 0)
    side_counts = summary.get("raw_event_lookup_directional_side_counts") or {}
    outcome_counts = summary.get("raw_event_lookup_outcome_key_counts") or {}

    blockers: list[str] = []
    signals: list[str] = []

    if assessment in {"", "uncertain"}:
        blockers.append("assessment_uncertain")
    elif assessment == "true-positive-risk":
        blockers.append("assessment_true_positive_risk")

    if not raw_lookup_embedded:
        blockers.append("raw_lookup_not_embedded")
    elif missing_count:
        blockers.append("raw_lookup_missing_events")

    if trade_row_count == 0:
        blockers.append("raw_lookup_no_trade_facts")
    if len(side_counts) > 1:
        signals.append("mixed_directional_sides")
    elif len(side_counts) == 1:
        signals.append("single_directional_side")
    if len(outcome_counts) > 1:
        signals.append("mixed_outcome_keys")
    elif len(outcome_counts) == 1:
        signals.append("single_outcome_key")

    if summary.get("persisted_alert_review") is False:
        blockers.append("packet_review_only")

    if "assessment_true_positive_risk" in blockers:
        readiness = "blocked-true-positive-risk"
    elif blockers == ["packet_review_only"]:
        readiness = "packet-review-only"
    elif blockers:
        readiness = "needs-more-evidence"
    else:
        readiness = "review-supported"

    payload_status = str(summary.get("raw_event_lookup_payload_status") or "")
    next_action_reasons = list(blockers)
    if payload_status == "preview-only":
        next_action_reasons.append("payload_preview_only")
    for signal in signals:
        if signal.startswith("mixed_"):
            next_action_reasons.append(signal)

    if "assessment_true_positive_risk" in blockers:
        next_action = "narrow-rule-before-config-review"
    elif any(
        blocker in blockers
        for blocker in (
            "raw_lookup_not_embedded",
            "raw_lookup_missing_events",
            "raw_lookup_no_trade_facts",
        )
    ):
        next_action = "embed-raw-lookup"
    elif payload_status == "preview-only":
        next_action = "rerun-with-full-payload"
    elif "assessment_uncertain" in blockers:
        next_action = "classify-cluster"
    elif "packet_review_only" in blockers:
        next_action = "collect-persisted-review-evidence"
    else:
        next_action = "review-calibration-decision"

    return {
        "calibration_candidate_readiness": readiness,
        "calibration_candidate_blockers": blockers,
        "calibration_candidate_signals": signals,
        "calibration_candidate_next_action": next_action,
        "calibration_candidate_next_action_reasons": next_action_reasons,
    }


def summarize_calibration_cluster_review_record(
    name: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    assessment = record.get("assessment") or {}
    cluster = record.get("cluster") or {}
    raw_event_ids = list(record.get("raw_event_ids") or [])
    raw_event_lookup = record.get("raw_event_lookup") or {}
    if not isinstance(raw_event_lookup, dict):
        raw_event_lookup = {}
    missing_raw_event_ids = list(raw_event_lookup.get("missing_raw_event_ids") or [])
    summary = {
        "name": name,
        "schema_version": record.get("schema_version"),
        "generated_at": record.get("generated_at"),
        "market_cluster": record.get("market_cluster"),
        "assessment": assessment.get("label"),
        "rationale": assessment.get("rationale"),
        "reviewed_by": assessment.get("reviewed_by"),
        "local_only": record.get("local_only"),
        "validate_only": record.get("validate_only"),
        "config_mutation": record.get("config_mutation"),
        "db_mutation": record.get("db_mutation"),
        "live_calls": record.get("live_calls"),
        "persisted_alert_review": record.get("persisted_alert_review"),
        "packet_names": _packet_names_from_record(record),
        "row_count": cluster.get("row_count"),
        "raw_event_id_count": len(raw_event_ids),
        "raw_event_lookup_embedded": bool(raw_event_lookup),
        "raw_event_lookup_found_count": raw_event_lookup.get("found_count"),
        "raw_event_lookup_missing_count": len(missing_raw_event_ids),
        "raw_event_lookup_include_payload": raw_event_lookup.get("include_payload"),
    }
    summary.update(_raw_lookup_trade_profile(raw_event_lookup))
    summary["raw_event_lookup_payload_status"] = _raw_lookup_payload_status(summary)
    summary.update(_candidate_readiness(summary))
    return summary


def build_calibration_cluster_review_record(
    named_packets: list[tuple[str, dict[str, Any]]],
    *,
    market_cluster: str,
    assessment: str,
    rationale: str,
    state: str = "removed",
    review_group: str = "unmatched_replay_only",
    reviewed_by: str | None = None,
    generated_at: datetime | None = None,
    output_artifact_path: str | None = None,
    output_artifact_name: str | None = None,
) -> dict[str, Any]:
    from pmfi.review_metadata import normalize_reviewed_by

    cluster_key = str(market_cluster or "").strip()
    if not cluster_key:
        raise ValueError("market_cluster is required")
    if assessment not in CLUSTER_REVIEW_ASSESSMENTS:
        raise ValueError(
            "assessment must be one of: "
            f"{', '.join(CLUSTER_REVIEW_ASSESSMENTS)}"
        )
    rationale_text = str(rationale or "").strip()
    if not rationale_text:
        raise ValueError("rationale is required")
    reviewed_by = normalize_reviewed_by(reviewed_by)

    queue = calibration_packet_review_queue(
        named_packets,
        state=state,
        review_group=review_group,
        market_cluster=cluster_key,
        limit=0,
    )
    rows = list(queue.get("rows") or [])
    if not rows:
        raise ValueError("no queue rows matched cluster review filters")

    clusters = list(queue.get("market_clusters") or [])
    cluster_summary = next(
        (
            cluster
            for cluster in clusters
            if cluster.get("market_key") == cluster_key
        ),
        clusters[0] if clusters else {},
    )
    generated = generated_at or datetime.now(timezone.utc)
    packet_names = [name for name, _ in named_packets]
    review: dict[str, Any] = {
        "schema_version": "calibration_cluster_review.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "persisted_alert_review": False,
        "generated_at": generated.isoformat(),
        "packet_selection": {
            "names": packet_names,
            "count": len(packet_names),
        },
        "filters": deepcopy(queue.get("filters") or {}),
        "market_cluster": cluster_key,
        "assessment": {
            "label": assessment,
            "rationale": rationale_text,
            "reviewed_by": reviewed_by,
            "implication": _assessment_implication(assessment),
        },
        "queue_totals": deepcopy(queue.get("totals") or {}),
        "cluster": deepcopy(dict(cluster_summary)),
        "raw_event_ids": _raw_event_ids(rows),
        "rows": deepcopy(rows),
    }
    if output_artifact_path is not None or output_artifact_name is not None:
        review["output_artifact"] = {
            "path": output_artifact_path,
            "name": output_artifact_name,
        }
    # Keep the helper honest about JSON artifact compatibility.
    json.dumps(review, default=str)
    return review


def calibration_cluster_review_coverage(
    named_packets: list[tuple[str, dict[str, Any]]],
    review_records: list[tuple[str, dict[str, Any]]],
    *,
    state: str = "removed",
    review_group: str = "unmatched_replay_only",
    market_cluster: str | None = None,
) -> dict[str, Any]:
    cluster_filter = str(market_cluster).strip() if market_cluster is not None else None
    cluster_filter = cluster_filter or None
    queue = calibration_packet_review_queue(
        named_packets,
        state=state,
        review_group=review_group,
        market_cluster=cluster_filter,
        limit=0,
    )
    selected_packet_names = {name for name, _ in named_packets}

    latest_by_cluster: dict[str, tuple[str, dict[str, Any]]] = {}
    considered_reviews = 0
    for name, record in review_records:
        if record.get("schema_version") != "calibration_cluster_review.v1":
            continue
        review_cluster = str(record.get("market_cluster") or "").strip()
        if not review_cluster:
            continue
        if cluster_filter is not None and review_cluster != cluster_filter:
            continue
        review_packet_names = set(_packet_names_from_record(record))
        if selected_packet_names and review_packet_names:
            if not selected_packet_names.intersection(review_packet_names):
                continue
        considered_reviews += 1
        current = latest_by_cluster.get(review_cluster)
        if current is None or _generated_at_key(record) >= _generated_at_key(current[1]):
            latest_by_cluster[review_cluster] = (name, record)

    rows_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for row in queue.get("rows") or []:
        key = str(row.get("market_cluster") or row.get("market") or "unknown")
        rows_by_cluster.setdefault(key, []).append(row)

    assessment_counts: Counter[str] = Counter()
    readiness_counts: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()
    payload_status_counts: Counter[str] = Counter()
    next_action_counts: Counter[str] = Counter()
    cluster_rows: list[dict[str, Any]] = []
    for cluster in queue.get("market_clusters") or []:
        key = str(cluster.get("market_key") or "unknown")
        queue_rows = rows_by_cluster.get(key, [])
        queue_raw_ids = _raw_event_id_set(_raw_event_ids(queue_rows))
        latest = latest_by_cluster.get(key)
        latest_summary = None
        review_raw_ids: set[str] = set()
        if latest is not None:
            latest_name, latest_record = latest
            latest_summary = summarize_calibration_cluster_review_record(
                latest_name,
                latest_record,
            )
            label = str(latest_summary.get("assessment") or "unknown")
            assessment_counts[label] += 1
            readiness = str(
                latest_summary.get("calibration_candidate_readiness") or "unknown"
            )
            readiness_counts[readiness] += 1
            for signal in latest_summary.get("calibration_candidate_signals") or []:
                signal_counts[str(signal)] += 1
            payload_status = str(
                latest_summary.get("raw_event_lookup_payload_status") or "unknown"
            )
            payload_status_counts[payload_status] += 1
            next_action = str(
                latest_summary.get("calibration_candidate_next_action") or "unknown"
            )
            next_action_counts[next_action] += 1
            review_raw_ids = _raw_event_id_set(
                list(latest_record.get("raw_event_ids") or [])
            )

        missing_raw_ids = sorted(queue_raw_ids - review_raw_ids)
        covered = latest_summary is not None and not missing_raw_ids
        cluster_rows.append({
            "market_key": key,
            "row_count": int(cluster.get("row_count") or len(queue_rows)),
            "raw_event_id_count": len(queue_raw_ids),
            "latest_review": latest_summary,
            "covered": covered,
            "missing_raw_event_id_count": len(missing_raw_ids),
            "missing_raw_event_ids_sample": missing_raw_ids[:10],
        })

    covered_count = sum(1 for row in cluster_rows if row["covered"])
    return {
        "schema_version": "calibration_cluster_review_coverage.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "persisted_alert_review": False,
        "packet_count": len(named_packets),
        "review_artifact_count": len(review_records),
        "considered_review_artifact_count": considered_reviews,
        "filters": {
            "state": state,
            "review_group": review_group,
            "market_cluster": cluster_filter,
        },
        "queue_totals": deepcopy(queue.get("totals") or {}),
        "totals": {
            "market_cluster_count": len(cluster_rows),
            "covered_market_cluster_count": covered_count,
            "uncovered_market_cluster_count": len(cluster_rows) - covered_count,
            "assessment_counts": dict(sorted(assessment_counts.items())),
            "candidate_readiness_counts": dict(sorted(readiness_counts.items())),
            "candidate_signal_counts": dict(sorted(signal_counts.items())),
            "candidate_next_action_counts": dict(sorted(next_action_counts.items())),
            "raw_event_lookup_payload_status_counts": dict(
                sorted(payload_status_counts.items())
            ),
        },
        "market_clusters": cluster_rows,
    }
