from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REVIEW_GROUPS = (
    "matched_noise",
    "matched_fp",
    "matched_tp",
    "matched_unreviewed",
    "matched_other",
    "unmatched_replay_only",
)
_QUEUE_STATES = ("removed", "added", "all")
_QUEUE_REVIEW_GROUPS = (*_REVIEW_GROUPS, "all")


def calibration_packet_root() -> Path:
    from pmfi.config import ROOT

    return ROOT / "reports" / "calibration-packets"


def resolve_calibration_packet_file(name: str) -> Path:
    if not name or "/" in name or "\\" in name:
        raise ValueError("packet name must be a direct filename")
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".json":
        raise ValueError("packet name must be a direct .json filename")

    root = calibration_packet_root().resolve()
    packet_path = (root / candidate.name).resolve()
    try:
        packet_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("packet name must stay inside calibration packet root") from exc
    return packet_path


def list_calibration_packet_files() -> list[dict[str, Any]]:
    root = calibration_packet_root()
    if not root.is_dir():
        return []

    packets: list[tuple[float, str, dict[str, Any]]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        stat = path.stat()
        packets.append((
            stat.st_mtime,
            path.name,
            {
                "name": path.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat(),
            },
        ))
    packets.sort(key=lambda item: (-item[0], item[1]))
    return [packet for _, _, packet in packets]


def load_calibration_packet(name: str) -> dict[str, Any]:
    packet_path = resolve_calibration_packet_file(name)
    if not packet_path.is_file():
        raise FileNotFoundError(name)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if not isinstance(packet, dict):
        raise TypeError("calibration packet must be a JSON object")
    return packet


def _packet_records(packet: dict[str, Any], key: str) -> list[dict[str, Any]]:
    summary = packet.get("calibration_summary") or {}
    comparison = summary.get("comparison") or {}
    records = comparison.get(key) or []
    return [record for record in records if isinstance(record, dict)]


def _packet_review_count(records: list[dict[str, Any]], *, matched: bool) -> int:
    count = 0
    for record in records:
        review = record.get("review") or {}
        if bool(review.get("matched")) is matched:
            count += 1
    return count


def _packet_review_labels(records: list[dict[str, Any]]) -> dict[str, int]:
    labels: Counter[str] = Counter()
    for record in records:
        review = record.get("review") or {}
        if review.get("matched"):
            labels[str(review.get("label") or "unreviewed")] += 1
        else:
            labels["unmatched"] += 1
    return dict(sorted(labels.items()))


def _packet_review_categories(records: list[dict[str, Any]]) -> dict[str, int]:
    categories: Counter[str] = Counter()
    for record in records:
        review = record.get("review") or {}
        if review.get("matched"):
            categories[str(review.get("category") or "uncategorized")] += 1
        else:
            categories["unmatched"] += 1
    return dict(sorted(categories.items()))


def _review_value(review: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = review.get(key)
        if value is not None and value != "":
            return value
    return None


def _normalized_review_label(value: Any) -> str:
    label = str(value or "").strip().lower().replace("_", "-")
    if label in {"noise"}:
        return "noise"
    if label in {"fp", "false-positive"}:
        return "fp"
    if label in {"tp", "true-positive"}:
        return "tp"
    if label in {"", "unreviewed"}:
        return "unreviewed"
    return "other"


def _review_group(record: dict[str, Any]) -> str:
    review = record.get("review") or {}
    if not review.get("matched"):
        return "unmatched_replay_only"

    label = _normalized_review_label(_review_value(review, "label", "review_label"))
    return f"matched_{label}"


def _sample_record(packet_name: str, record: dict[str, Any]) -> dict[str, Any]:
    review = record.get("review") or {}
    sample: dict[str, Any] = {
        "packet_name": packet_name,
        "raw_event_id": record.get("raw_event_id"),
        "venue": record.get("venue"),
        "review": {
            "matched": bool(review.get("matched")),
            "label": _review_value(review, "label", "review_label"),
            "category": _review_value(
                review,
                "category",
                "review_category",
                "false_positive_category",
            ),
        },
    }
    for key in (
        "market",
        "market_slug",
        "market_title",
        "title",
        "this_trade_usd",
        "trade_usd",
    ):
        if key in record:
            sample[key] = record.get(key)
    return sample


def _review_group_summary(
    named_packets: list[tuple[str, dict[str, Any]]],
    record_key: str,
) -> dict[str, Any]:
    counts = {group: 0 for group in _REVIEW_GROUPS}
    samples: dict[str, list[dict[str, Any]]] = {group: [] for group in _REVIEW_GROUPS}
    for packet_name, packet in named_packets:
        for record in _packet_records(packet, record_key):
            group = _review_group(record)
            counts[group] += 1
            samples[group].append(_sample_record(packet_name, record))
    return {
        "counts": counts,
        "samples": samples,
    }


def _summary_recommendation(comparison: dict[str, Any]) -> str:
    aggregate = comparison.get("aggregate") or {}
    packet_count = int(comparison.get("packet_count") or 0)
    candidate_groups = int(comparison.get("candidate_groups") or 0)
    removed_records = int(aggregate.get("removed_records") or 0)
    added_records = int(aggregate.get("added_records") or 0)
    removed_labels = aggregate.get("removed_review_labels") or {}
    added_labels = aggregate.get("added_review_labels") or {}
    removed_unmatched = int(aggregate.get("removed_review_unmatched") or 0)
    removed_label_groups = Counter({
        _normalized_review_label(label): int(count or 0)
        for label, count in removed_labels.items()
    })
    added_label_groups = Counter({
        _normalized_review_label(label): int(count or 0)
        for label, count in added_labels.items()
    })
    reviewed_noise_fp = (
        removed_label_groups["noise"] + removed_label_groups["fp"]
    )
    removed_tp = removed_label_groups["tp"]
    added_reviewed_noise_fp = added_label_groups["noise"] + added_label_groups["fp"]
    added_tp = added_label_groups["tp"]
    added_unsafe = added_records - added_reviewed_noise_fp

    if packet_count == 0:
        return "insufficient-packets"
    if candidate_groups > 1:
        return "mixed-candidates"
    if removed_records == 0 and added_records == 0:
        return "no-candidate-effect"
    if removed_tp > 0 or added_tp > 0:
        return "blocked-by-true-positive-risk"
    if added_unsafe > 0:
        return "needs-more-evidence"
    if reviewed_noise_fp == 0:
        return "needs-persisted-review-evidence"
    if (
        reviewed_noise_fp == removed_records
        and removed_unmatched == 0
        and added_reviewed_noise_fp == added_records
    ):
        return "change-ready-candidate"
    return "needs-more-evidence"


def _summary_rationale(recommendation: str) -> str:
    return {
        "insufficient-packets": "No calibration packets were available to evaluate.",
        "mixed-candidates": "Selected packets do not describe one consistent candidate.",
        "no-candidate-effect": "The candidate did not remove any volume_spike_v1 records.",
        "blocked-by-true-positive-risk": (
            "At least one changed record matches a reviewed true-positive alert."
        ),
        "needs-persisted-review-evidence": (
            "Removed records are replay-only or otherwise lack reviewed persisted "
            "noise/false-positive evidence."
        ),
        "change-ready-candidate": (
            "Removed records are reviewed noise or false positives with no "
            "unmatched removals or true-positive removals."
        ),
        "needs-more-evidence": (
            "The packet set contains mixed reviewed, replay-only, or added "
            "alert evidence; more persisted review evidence is needed before "
            "a config change."
        ),
    }.get(recommendation, "Review the packet evidence before making a config change.")


def _risk_counts(
    removed: dict[str, Any],
    added: dict[str, Any],
) -> dict[str, int]:
    def count(summary: dict[str, Any], group: str) -> int:
        counts = summary.get("counts") or {}
        return int(counts.get(group) or 0)

    removed_noise = count(removed, "matched_noise")
    removed_fp = count(removed, "matched_fp")
    added_noise = count(added, "matched_noise")
    added_fp = count(added, "matched_fp")
    return {
        "removed_reviewed_noise": removed_noise,
        "removed_reviewed_fp": removed_fp,
        "removed_reviewed_noise_or_fp": removed_noise + removed_fp,
        "removed_reviewed_tp": count(removed, "matched_tp"),
        "removed_reviewed_unreviewed": count(removed, "matched_unreviewed"),
        "removed_reviewed_other": count(removed, "matched_other"),
        "removed_unmatched": count(removed, "unmatched_replay_only"),
        "added_reviewed_noise": added_noise,
        "added_reviewed_fp": added_fp,
        "added_reviewed_noise_or_fp": added_noise + added_fp,
        "added_reviewed_tp": count(added, "matched_tp"),
        "added_reviewed_unreviewed": count(added, "matched_unreviewed"),
        "added_reviewed_other": count(added, "matched_other"),
        "added_unmatched": count(added, "unmatched_replay_only"),
    }


def _flatten_review_samples(
    state: str,
    grouped: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    samples = grouped.get("samples") or {}
    for group in _REVIEW_GROUPS:
        for sample in samples.get(group) or []:
            row = dict(sample)
            row["state"] = state
            row["review_group"] = group
            row["risk"] = f"{state}_{group}"
            rows.append(row)
    return rows


def _queue_review_object(record: dict[str, Any]) -> dict[str, Any]:
    review = record.get("review") or {}
    return {
        "matched": bool(review.get("matched")),
        "alert_id": _review_value(review, "alert_id"),
        "trade_id": _review_value(review, "trade_id"),
        "label": _review_value(review, "label", "review_label"),
        "category": _review_value(
            review,
            "category",
            "review_category",
            "false_positive_category",
        ),
        "reviewed_at": _review_value(review, "reviewed_at"),
    }


def _queue_review_action(
    *,
    state: str,
    review_group: str,
    review: dict[str, Any],
) -> str:
    if review_group == "unmatched_replay_only":
        return (
            "manual packet/raw-event inspection required; this replay-only row "
            "has no persisted alert target, so do not treat it as an alert "
            "review write."
        )
    if review_group == "matched_tp":
        return (
            "review persisted true-positive alert evidence before suppressing "
            f"this {state} row; no queue mutation is performed."
        )
    if review_group in {"matched_noise", "matched_fp"}:
        return (
            "review persisted noise/false-positive alert evidence as candidate "
            f"context for this {state} row; no queue mutation is performed."
        )
    if review_group == "matched_unreviewed":
        alert_id = review.get("alert_id") or "matched alert"
        return (
            f"inspect persisted {alert_id} before any separate alert review "
            "command; this queue is validate-only."
        )
    return (
        "manual persisted-alert and packet inspection required before any "
        "calibration decision; this queue is validate-only."
    )


def _queue_record(
    packet_name: str,
    state: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    review_group = _review_group(record)
    review = _queue_review_object(record)
    row: dict[str, Any] = {
        "packet_name": packet_name,
        "state": state,
        "review_group": review_group,
        "risk": f"{state}_{review_group}",
        "raw_event_id": record.get("raw_event_id"),
        "venue": record.get("venue"),
        "venue_trade_id": record.get("venue_trade_id"),
        "baseline_median_usd": record.get("baseline_median_usd"),
        "spike_multiplier": record.get("spike_multiplier"),
        "triage_flags": list(record.get("triage_flags") or []),
        "review": review,
        "persisted_alert_reviewable": bool(review.get("matched")),
    }
    row["review_action"] = _queue_review_action(
        state=state,
        review_group=review_group,
        review=review,
    )
    for key in (
        "market",
        "venue_market_id",
        "market_slug",
        "market_title",
        "title",
        "outcome_key",
    ):
        if key in record:
            row[key] = record.get(key)
    row["market_cluster"] = _market_cluster_key(row)

    this_trade_usd = record.get("this_trade_usd")
    trade_usd = record.get("trade_usd")
    if this_trade_usd is None:
        this_trade_usd = trade_usd
    if trade_usd is None:
        trade_usd = this_trade_usd
    row["this_trade_usd"] = this_trade_usd
    row["trade_usd"] = trade_usd
    return row


def _queue_rows(
    named_packets: list[tuple[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    rows: list[dict[str, Any]] = []
    groups = {
        "removed": {group: 0 for group in _REVIEW_GROUPS},
        "added": {group: 0 for group in _REVIEW_GROUPS},
    }
    for packet_name, packet in named_packets:
        for state, record_key in (
            ("removed", "removed_volume_spike_records"),
            ("added", "added_volume_spike_records"),
        ):
            for record in _packet_records(packet, record_key):
                row = _queue_record(packet_name, state, record)
                groups[state][row["review_group"]] += 1
                rows.append(row)
    return rows, groups


def _market_cluster_key(row: dict[str, Any]) -> str:
    for key in ("market", "venue_market_id", "market_slug", "market_title", "title"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "unknown"


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _cluster_min_max(rows: list[dict[str, Any]], key: str) -> tuple[float | None, float | None]:
    values = [
        numeric
        for row in rows
        if (numeric := _numeric_value(row.get(key))) is not None
    ]
    if not values:
        return None, None
    return min(values), max(values)


def _market_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_market_cluster_key(row), []).append(row)

    clusters: list[dict[str, Any]] = []
    for market_key, cluster_rows in grouped.items():
        packets = sorted({
            str(row.get("packet_name"))
            for row in cluster_rows
            if row.get("packet_name") is not None
        })
        venues = sorted({
            str(row.get("venue") or "unknown")
            for row in cluster_rows
        })
        states = Counter(str(row.get("state") or "unknown") for row in cluster_rows)
        review_groups = Counter(
            str(row.get("review_group") or "unknown") for row in cluster_rows
        )
        raw_event_ids: list[Any] = []
        seen_raw_event_ids: set[str] = set()
        for row in cluster_rows:
            raw_event_id = row.get("raw_event_id")
            raw_key = "" if raw_event_id is None else str(raw_event_id)
            if not raw_key or raw_key in seen_raw_event_ids:
                continue
            seen_raw_event_ids.add(raw_key)
            raw_event_ids.append(raw_event_id)

        triage_flags: Counter[str] = Counter()
        for row in cluster_rows:
            triage_flags.update(str(flag) for flag in row.get("triage_flags") or [])

        this_min, this_max = _cluster_min_max(cluster_rows, "this_trade_usd")
        baseline_min, baseline_max = _cluster_min_max(
            cluster_rows, "baseline_median_usd"
        )
        spike_min, spike_max = _cluster_min_max(cluster_rows, "spike_multiplier")
        persisted_count = sum(
            1
            for row in cluster_rows
            if bool(row.get("persisted_alert_reviewable"))
        )
        clusters.append({
            "market_key": market_key,
            "venues": venues,
            "row_count": len(cluster_rows),
            "packet_count": len(packets),
            "packet_names": packets,
            "states": dict(sorted(states.items())),
            "review_groups": dict(sorted(review_groups.items())),
            "raw_event_id_count": len(raw_event_ids),
            "raw_event_ids_sample": raw_event_ids[:10],
            "this_trade_usd_min": this_min,
            "this_trade_usd_max": this_max,
            "baseline_median_usd_min": baseline_min,
            "baseline_median_usd_max": baseline_max,
            "spike_multiplier_min": spike_min,
            "spike_multiplier_max": spike_max,
            "persisted_alert_reviewable_count": persisted_count,
            "replay_only_count": len(cluster_rows) - persisted_count,
            "top_triage_flags": [
                {"flag": flag, "count": count}
                for flag, count in sorted(
                    triage_flags.items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            ],
        })

    return sorted(
        clusters,
        key=lambda cluster: (-int(cluster["row_count"]), str(cluster["market_key"])),
    )


def calibration_packet_review_queue(
    named_packets: list[tuple[str, dict[str, Any]]],
    *,
    state: str = "all",
    review_group: str = "all",
    market_cluster: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    """Build a read-only operator queue from calibration packet delta records."""
    if state not in _QUEUE_STATES:
        raise ValueError(f"state must be one of: {', '.join(_QUEUE_STATES)}")
    if review_group not in _QUEUE_REVIEW_GROUPS:
        raise ValueError(
            "review_group must be one of: "
            f"{', '.join(_QUEUE_REVIEW_GROUPS)}"
        )
    try:
        limit_value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if limit_value < 0:
        raise ValueError("limit must be >= 0")
    cluster_filter = None
    if market_cluster is not None:
        cluster_filter = str(market_cluster).strip() or None

    rows, groups = _queue_rows(named_packets)
    filtered = [
        row for row in rows
        if (state == "all" or row["state"] == state)
        and (review_group == "all" or row["review_group"] == review_group)
    ]
    if cluster_filter is not None:
        filtered = [
            row for row in filtered
            if _market_cluster_key(row) == cluster_filter
        ]
    market_clusters = _market_clusters(filtered)
    returned = filtered if limit_value == 0 else filtered[:limit_value]
    candidate_counts = Counter(_candidate_key(packet) for _, packet in named_packets)

    return {
        "schema_version": "calibration_packet_review_queue.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "packet_count": len(named_packets),
        "candidate_groups": len(candidate_counts),
        "filters": {
            "state": state,
            "review_group": review_group,
            "market_cluster": cluster_filter,
            "limit": limit_value,
        },
        "totals": {
            "available_rows": len(rows),
            "filtered_rows": len(filtered),
            "returned_rows": len(returned),
            "truncated": len(returned) < len(filtered),
            "truncated_rows": max(0, len(filtered) - len(returned)),
        },
        "groups": groups,
        "market_clusters": market_clusters,
        "rows": returned,
    }


def _raw_event_key(record: dict[str, Any]) -> str | None:
    value = record.get("raw_event_id")
    if value is None or value == "":
        return None
    return str(value)


def _candidate_key(packet: dict[str, Any]) -> str:
    metadata = packet.get("export_metadata") or {}
    summary = packet.get("calibration_summary") or {}
    candidate = metadata.get("candidate") or summary.get("candidate") or {}
    return json.dumps(candidate, sort_keys=True, default=str)


def _summarize_packet_for_comparison(
    name: str,
    packet: dict[str, Any],
) -> dict[str, Any]:
    metadata = packet.get("export_metadata") or {}
    summary = packet.get("calibration_summary") or {}
    comparison = summary.get("comparison") or {}
    current = summary.get("current") or {}
    candidate_replay = summary.get("candidate_replay") or {}
    removed = _packet_records(packet, "removed_volume_spike_records")
    added = _packet_records(packet, "added_volume_spike_records")
    return {
        "name": name,
        "schema_version": metadata.get("schema_version"),
        "filters": metadata.get("filters") or summary.get("filters") or {},
        "candidate": metadata.get("candidate") or summary.get("candidate") or {},
        "current_volume_spike_alerts": current.get("volume_spike_alerts"),
        "candidate_volume_spike_alerts": candidate_replay.get("volume_spike_alerts"),
        "volume_spike_delta": comparison.get("volume_spike_delta"),
        "removed_records": len(removed),
        "added_records": len(added),
        "removed_review_matches": _packet_review_count(removed, matched=True),
        "removed_review_unmatched": _packet_review_count(removed, matched=False),
        "added_review_matches": _packet_review_count(added, matched=True),
        "added_review_unmatched": _packet_review_count(added, matched=False),
        "removed_review_labels": _packet_review_labels(removed),
        "added_review_labels": _packet_review_labels(added),
        "removed_delta_records_truncated": bool(
            comparison.get("removed_delta_records_truncated")
        ),
        "added_delta_records_truncated": bool(
            comparison.get("added_delta_records_truncated")
        ),
    }


def calibration_packet_comparison(
    named_packets: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    packet_summaries: list[dict[str, Any]] = []
    removed_raw_ids: Counter[str] = Counter()
    added_raw_ids: Counter[str] = Counter()
    removed_label_counts: Counter[str] = Counter()
    added_label_counts: Counter[str] = Counter()
    removed_category_counts: Counter[str] = Counter()
    added_category_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    totals = Counter()

    for name, packet in named_packets:
        packet_summaries.append(_summarize_packet_for_comparison(name, packet))
        candidate_counts[_candidate_key(packet)] += 1
        removed = _packet_records(packet, "removed_volume_spike_records")
        added = _packet_records(packet, "added_volume_spike_records")
        for record in removed:
            raw_key = _raw_event_key(record)
            if raw_key is not None:
                removed_raw_ids[raw_key] += 1
        for record in added:
            raw_key = _raw_event_key(record)
            if raw_key is not None:
                added_raw_ids[raw_key] += 1
        removed_label_counts.update(_packet_review_labels(removed))
        added_label_counts.update(_packet_review_labels(added))
        removed_category_counts.update(_packet_review_categories(removed))
        added_category_counts.update(_packet_review_categories(added))
        totals["removed_records"] += len(removed)
        totals["added_records"] += len(added)
        totals["removed_review_matches"] += _packet_review_count(
            removed, matched=True
        )
        totals["removed_review_unmatched"] += _packet_review_count(
            removed, matched=False
        )
        totals["added_review_matches"] += _packet_review_count(
            added, matched=True
        )
        totals["added_review_unmatched"] += _packet_review_count(
            added, matched=False
        )

    return {
        "schema_version": "calibration_packet_comparison.v1",
        "local_only": True,
        "validate_only": True,
        "packet_count": len(named_packets),
        "candidate_groups": len(candidate_counts),
        "packets": packet_summaries,
        "aggregate": {
            "removed_records": totals["removed_records"],
            "added_records": totals["added_records"],
            "removed_review_matches": totals["removed_review_matches"],
            "removed_review_unmatched": totals["removed_review_unmatched"],
            "added_review_matches": totals["added_review_matches"],
            "added_review_unmatched": totals["added_review_unmatched"],
            "removed_review_labels": dict(sorted(removed_label_counts.items())),
            "added_review_labels": dict(sorted(added_label_counts.items())),
            "removed_review_categories": dict(sorted(removed_category_counts.items())),
            "added_review_categories": dict(sorted(added_category_counts.items())),
            "unique_removed_raw_event_ids": len(removed_raw_ids),
            "unique_added_raw_event_ids": len(added_raw_ids),
            "repeated_removed_raw_event_ids": [
                {"raw_event_id": raw_id, "packets": count}
                for raw_id, count in sorted(removed_raw_ids.items())
                if count > 1
            ],
            "repeated_added_raw_event_ids": [
                {"raw_event_id": raw_id, "packets": count}
                for raw_id, count in sorted(added_raw_ids.items())
                if count > 1
            ],
        },
    }


def calibration_packet_review_summary(
    named_packets: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    comparison = calibration_packet_comparison(named_packets)
    removed = _review_group_summary(named_packets, "removed_volume_spike_records")
    added = _review_group_summary(named_packets, "added_volume_spike_records")
    recommendation = _summary_recommendation(comparison)
    samples = (
        _flatten_review_samples("removed", removed)
        + _flatten_review_samples("added", added)
    )

    return {
        "schema_version": "calibration_packet_review_summary.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "recommendation": recommendation,
        "rationale": _summary_rationale(recommendation),
        "risk_counts": _risk_counts(removed, added),
        "samples": samples,
        "comparison": comparison,
        "removed_volume_spike_records": removed,
        "added_volume_spike_records": added,
    }
