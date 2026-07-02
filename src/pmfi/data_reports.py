from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


DEFAULT_FP_RATE_MIN_REVIEWED = 5

DATA_COVERAGE_BUCKETS = (
    "normalized",
    "dead_lettered",
    "skipped_non_trade",
    "unaccounted",
)

# Raw event types intentionally skipped by normalization because they are venue
# state frames, not trade events. Keep this explicit so gaps are auditable.
NON_TRADE_RAW_EVENT_TYPES_BY_VENUE: dict[str, frozenset[str]] = {
    "polymarket": frozenset({"price_change", "book", "best_bid_ask", "new_market"}),
    "kalshi": frozenset(),
}

# Synthetic fixture markets use compact, explicit venue-specific markers so
# operator coverage can measure real captured data without deleting old test rows.
SYNTHETIC_MARKET_PREFIXES_BY_VENUE: dict[str, tuple[str, ...]] = {
    "polymarket": ("pm-",),
}


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except KeyError:
        return default


def _is_skipped_non_trade(venue_code: str, source_event_type: str) -> bool:
    return source_event_type in NON_TRADE_RAW_EVENT_TYPES_BY_VENUE.get(venue_code, frozenset())


def _has_synthetic_marker(row: Mapping[str, Any]) -> bool:
    explicit = _row_value(row, "is_synthetic", None)
    if explicit is not None:
        return bool(explicit)

    venue_code = str(_row_value(row, "venue_code", "") or "")
    prefixes = SYNTHETIC_MARKET_PREFIXES_BY_VENUE.get(venue_code, ())
    if not prefixes:
        return False

    marker_values = (
        _row_value(row, "venue_market_id", None),
        _row_value(row, "payload_market", None),
    )
    for value in marker_values:
        text = str(value or "")
        if any(text.startswith(prefix) for prefix in prefixes):
            return True
    return False


def classify_data_coverage_row(row: Mapping[str, Any]) -> str:
    """Classify an aggregate raw-event row into a single disposition bucket."""
    if bool(_row_value(row, "has_normalized", False)):
        return "normalized"
    if bool(_row_value(row, "has_dead_letter", False)):
        return "dead_lettered"

    venue_code = str(_row_value(row, "venue_code", "") or "")
    source_event_type = str(_row_value(row, "source_event_type", "") or "")
    if _is_skipped_non_trade(venue_code, source_event_type):
        return "skipped_non_trade"
    return "unaccounted"


def summarize_data_coverage_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    exclude_synthetic: bool = True,
    dead_letter_reconciliation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    counts = {bucket: 0 for bucket in DATA_COVERAGE_BUCKETS}
    event_type_rows: list[dict[str, Any]] = []
    excluded_synthetic = 0

    for row in rows:
        count = int(_row_value(row, "cnt", 0) or 0)
        is_synthetic = _has_synthetic_marker(row)
        if exclude_synthetic and is_synthetic:
            excluded_synthetic += count
            continue
        bucket = classify_data_coverage_row(row)
        counts[bucket] += count
        event_type_rows.append(
            {
                "bucket": bucket,
                "venue_code": str(_row_value(row, "venue_code", "") or ""),
                "source_event_type": str(_row_value(row, "source_event_type", "") or ""),
                "has_normalized": bool(_row_value(row, "has_normalized", False)),
                "has_dead_letter": bool(_row_value(row, "has_dead_letter", False)),
                "is_synthetic": is_synthetic,
                "count": count,
            }
        )

    total = sum(counts.values())
    accounted = counts["normalized"] + counts["dead_lettered"] + counts["skipped_non_trade"]
    coverage_percent = 100.0 if total == 0 else round(accounted / total * 100, 1)
    bucket_percentages = {
        bucket: (0.0 if total == 0 else round(value / total * 100, 1))
        for bucket, value in counts.items()
    }
    unaccounted_event_types = [
        {
            "venue_code": row["venue_code"],
            "source_event_type": row["source_event_type"],
            "count": row["count"],
        }
        for row in event_type_rows
        if row["bucket"] == "unaccounted" and row["count"] > 0
    ]
    unaccounted_event_types.sort(
        key=lambda row: (-int(row["count"]), str(row["venue_code"]), str(row["source_event_type"]))
    )

    return {
        "total_raw_events": total,
        "accounted_raw_events": accounted,
        "coverage_percent": coverage_percent,
        "counts": counts,
        "bucket_percentages": bucket_percentages,
        "exclude_synthetic": exclude_synthetic,
        "excluded_synthetic_raw_events": excluded_synthetic,
        "has_unaccounted_warning": counts["unaccounted"] > 0,
        "unaccounted_event_types": unaccounted_event_types,
        "by_event_type": event_type_rows,
        "skipped_non_trade_types": {
            venue: sorted(types)
            for venue, types in sorted(NON_TRADE_RAW_EVENT_TYPES_BY_VENUE.items())
        },
        "synthetic_marker_policy": {
            venue: list(prefixes)
            for venue, prefixes in sorted(SYNTHETIC_MARKET_PREFIXES_BY_VENUE.items())
        },
        "dead_letter_reconciliation": dict(dead_letter_reconciliation or {}),
        "dead_letter_invariant": (
            "trade-type raw events must have a normalized_trade or a dead_letter; "
            "unaccounted trade-type rows indicate silent trade loss"
        ),
    }


def format_data_coverage_text(report: Mapping[str, Any]) -> str:
    counts = report.get("counts") or {}
    bucket_percentages = report.get("bucket_percentages") or {}
    lines = [
        "PMFI data coverage",
        f"Raw events: {int(report.get('total_raw_events') or 0)}",
        (
            f"Accounted: {int(report.get('accounted_raw_events') or 0)} "
            f"({float(report.get('coverage_percent') or 0.0):.1f}%)"
        ),
    ]
    for bucket in DATA_COVERAGE_BUCKETS:
        lines.append(
            f"  {bucket}: {int(counts.get(bucket) or 0)} "
            f"({float(bucket_percentages.get(bucket) or 0.0):.1f}%)"
        )
    lines.append(
        f"Synthetic raw events excluded: {int(report.get('excluded_synthetic_raw_events') or 0)} "
        f"(exclude_synthetic={bool(report.get('exclude_synthetic', True))})"
    )
    lines.append(
        "Skipped non-trade types: "
        + "; ".join(
            f"{venue}={','.join(types)}"
            for venue, types in (report.get("skipped_non_trade_types") or {}).items()
        )
    )
    reconciliation = report.get("dead_letter_reconciliation") or {}
    if reconciliation:
        lines.append(
            "Dead letters: "
            f"total={int(reconciliation.get('total_dead_letters') or 0)} "
            f"linked_raw_event_id={int(reconciliation.get('linked_dead_letters') or 0)} "
            f"unlinked_legacy={int(reconciliation.get('unlinked_dead_letters') or 0)} "
            f"resolved={int(reconciliation.get('resolved_dead_letters') or 0)}"
        )
    if report.get("dead_letter_invariant"):
        lines.append(f"Invariant: {report['dead_letter_invariant']}")
    if report.get("has_unaccounted_warning"):
        lines.append("WARNING: unaccounted trade-type raw events detected.")
        for row in report.get("unaccounted_event_types") or []:
            lines.append(
                "  "
                f"{row.get('venue_code')} {row.get('source_event_type')}: "
                f"{int(row.get('count') or 0)}"
            )
    return "\n".join(lines)


def build_fp_rate_governance_rows(
    rule_totals: Mapping[str, Mapping[str, int]],
    *,
    fp_rate_targets: Mapping[str, float],
    min_reviewed_by_rule: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Apply the shared per-rule FP+Noise governance contract."""
    rows: list[dict[str, Any]] = []
    for rule_key in sorted(rule_totals):
        stats = rule_totals[rule_key]
        reviewed = int(stats.get("reviewed", 0) or 0)
        tp = int(stats.get("tp", 0) or 0)
        fp = int(stats.get("fp", 0) or 0)
        noise = int(stats.get("noise", 0) or 0)
        not_actionable = fp + noise
        not_actionable_rate = (
            not_actionable / reviewed * 100
            if reviewed > 0
            else 0.0
        )
        target = fp_rate_targets.get(rule_key)
        min_reviewed = (
            int(min_reviewed_by_rule.get(rule_key, DEFAULT_FP_RATE_MIN_REVIEWED))
            if target is not None
            else 0
        )
        has_enough_reviews = target is None or reviewed >= min_reviewed
        breach = (
            target is not None
            and has_enough_reviews
            and not_actionable_rate > target
        )
        if target is None:
            status = "NO TARGET"
        elif not has_enough_reviews:
            status = "INSUFFICIENT"
        else:
            status = "BREACH" if breach else "OK"
        rows.append(
            {
                "rule_key": rule_key,
                "reviewed": reviewed,
                "tp": tp,
                "fp": fp,
                "noise": noise,
                "not_actionable_rate": round(not_actionable_rate, 1),
                "target": target,
                "min_reviewed": min_reviewed,
                "status": status,
            }
        )
    return rows


def _parse_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return parsed
    return {}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_volume_spike_current_floor_governance(
    review_rows: Iterable[Mapping[str, Any]],
    *,
    current_min_trade_usd: float,
    target: float | None,
    min_reviewed: int = DEFAULT_FP_RATE_MIN_REVIEWED,
) -> dict[str, Any]:
    """Summarize reviewed volume_spike alerts that satisfy the current floor."""
    current_totals = {
        "volume_spike_v1": {"reviewed": 0, "tp": 0, "fp": 0, "noise": 0}
    }
    all_reviewed = 0
    below_current_floor = 0
    unknown_trade_usd = 0

    for row in review_rows:
        if str(row.get("rule_key") or "") != "volume_spike_v1":
            continue

        all_reviewed += 1
        label = str(row.get("label") or "")
        evidence = _parse_mapping(row.get("evidence"))
        trade_usd = _as_float(evidence.get("this_trade_usd"))

        if trade_usd is None:
            unknown_trade_usd += 1
            continue
        if trade_usd < current_min_trade_usd:
            below_current_floor += 1
            continue

        stats = current_totals["volume_spike_v1"]
        stats["reviewed"] += 1
        if label in {"tp", "fp", "noise"}:
            stats[label] += 1

    row = build_fp_rate_governance_rows(
        current_totals,
        fp_rate_targets={"volume_spike_v1": target} if target is not None else {},
        min_reviewed_by_rule={"volume_spike_v1": min_reviewed},
    )[0]
    row["cohort"] = "current_floor"
    row["current_min_trade_usd"] = float(current_min_trade_usd)
    row["all_reviewed"] = all_reviewed
    row["below_current_floor_reviewed"] = below_current_floor
    row["unknown_trade_usd_reviewed"] = unknown_trade_usd
    row["excluded_reviewed"] = below_current_floor + unknown_trade_usd
    return row


def apply_floor_gated_governance_headlines(
    governance_rows: Iterable[Mapping[str, Any]],
    *,
    current_floor_rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Promote enforceable floor cohorts while retaining all-time history."""
    promoted: list[dict[str, Any]] = []
    for row in governance_rows:
        rule_key = str(row.get("rule_key") or "")
        current_floor = current_floor_rows.get(rule_key)
        if current_floor is None:
            promoted.append(dict(row))
            continue

        all_time = dict(row)
        all_time["cohort"] = "all_time"
        headline = dict(current_floor)
        headline["cohort"] = "current_floor"
        headline["secondary_all_time"] = all_time
        promoted.append(headline)
    return promoted


def summarize_backtest_analytics(
    replay_results: Iterable[Any],
    *,
    review_index: Mapping[tuple[int, str], str],
    fp_rate_targets: Mapping[str, float],
    min_reviewed_by_rule: Mapping[str, int],
) -> dict[str, Any]:
    rule_totals: dict[str, dict[str, int]] = {
        str(rule_key): {"reviewed": 0, "tp": 0, "fp": 0, "noise": 0}
        for rule_key in fp_rate_targets
    }
    fire_counts: dict[str, int] = {}
    matched_reviews = 0
    unmatched_alerts = 0
    normalized_trades = 0

    for result in replay_results:
        normalized_trades += 1
        raw_event_id = getattr(result, "raw_event_id", None)
        for decision in getattr(result, "alerts", []) or []:
            if not bool(getattr(decision, "emit_alert", True)):
                continue
            rule_key = str(getattr(decision, "rule_id", ""))
            if not rule_key:
                continue
            fire_counts[rule_key] = fire_counts.get(rule_key, 0) + 1
            stats = rule_totals.setdefault(
                rule_key,
                {"reviewed": 0, "tp": 0, "fp": 0, "noise": 0},
            )
            label = None
            if raw_event_id is not None:
                label = review_index.get((int(raw_event_id), rule_key))
            if label in {"tp", "fp", "noise"}:
                matched_reviews += 1
                stats["reviewed"] += 1
                stats[label] += 1
            else:
                unmatched_alerts += 1

    governance_rows = build_fp_rate_governance_rows(
        rule_totals,
        fp_rate_targets=fp_rate_targets,
        min_reviewed_by_rule=min_reviewed_by_rule,
    )
    for row in governance_rows:
        row["fire_count"] = fire_counts.get(str(row["rule_key"]), 0)

    return {
        "normalized_trades_replayed": normalized_trades,
        "total_alerts": sum(fire_counts.values()),
        "review_match_count": matched_reviews,
        "unmatched_replay_alerts": unmatched_alerts,
        "per_rule": governance_rows,
    }


def _volume_spike_row(summary: Mapping[str, Any]) -> Mapping[str, Any]:
    for row in summary.get("per_rule") or []:
        if row.get("rule_key") == "volume_spike_v1":
            return row
    return {
        "rule_key": "volume_spike_v1",
        "fire_count": 0,
        "reviewed": 0,
        "not_actionable_rate": 0.0,
        "target": None,
        "min_reviewed": 0,
        "status": "NO TARGET",
    }


def build_volume_spike_sensitivity_rows(
    candidate_summaries: Iterable[Mapping[str, Any]],
    *,
    baseline_min_trade_usd: float,
) -> list[dict[str, Any]]:
    materialized = sorted(
        candidate_summaries,
        key=lambda row: float(row.get("min_trade_usd") or 0.0),
    )
    baseline_summary = None
    for row in materialized:
        if float(row.get("min_trade_usd") or 0.0) == float(baseline_min_trade_usd):
            baseline_summary = row.get("summary") or {}
            break
    if baseline_summary is None and materialized:
        baseline_summary = materialized[0].get("summary") or {}
    baseline = _volume_spike_row(baseline_summary or {})
    baseline_fire_count = int(baseline.get("fire_count") or 0)
    baseline_rate = float(baseline.get("not_actionable_rate") or 0.0)

    rows: list[dict[str, Any]] = []
    for candidate in materialized:
        min_trade_usd = float(candidate.get("min_trade_usd") or 0.0)
        summary = candidate.get("summary") or {}
        volume = _volume_spike_row(summary)
        fire_count = int(volume.get("fire_count") or 0)
        rate = float(volume.get("not_actionable_rate") or 0.0)
        rows.append(
            {
                "min_trade_usd": min_trade_usd,
                "fire_count": fire_count,
                "fire_count_delta": fire_count - baseline_fire_count,
                "reviewed": int(volume.get("reviewed") or 0),
                "not_actionable_rate": round(rate, 1),
                "not_actionable_rate_delta": round(rate - baseline_rate, 1),
                "target": volume.get("target"),
                "min_reviewed": int(volume.get("min_reviewed") or 0),
                "status": volume.get("status"),
            }
        )
    return rows


def format_backtest_analytics_text(report: Mapping[str, Any]) -> str:
    current = report.get("current") or {}
    lines = [
        "PMFI backtest analytics",
        f"Normalized trades replayed: {int(current.get('normalized_trades_replayed') or 0)}",
        f"Replay alerts: {int(current.get('total_alerts') or 0)}",
        "Per-rule:",
    ]
    for row in current.get("per_rule") or []:
        target = row.get("target")
        target_text = f"target<={float(target):.1f}%" if target is not None else "target=none"
        lines.append(
            "  "
            f"{row.get('rule_key')} fire_count={int(row.get('fire_count') or 0)} "
            f"reviewed={int(row.get('reviewed') or 0)} "
            f"fp_noise_rate={float(row.get('not_actionable_rate') or 0.0):.1f}% "
            f"{target_text} status={row.get('status')}"
        )
    sensitivity = report.get("volume_spike_sensitivity") or []
    if sensitivity:
        lines.append("volume_spike_v1 min_trade_usd sensitivity:")
        for row in sensitivity:
            lines.append(
                "  "
                f"min_trade_usd={float(row.get('min_trade_usd') or 0.0):.0f} "
                f"fire_count={int(row.get('fire_count') or 0)} "
                f"delta={int(row.get('fire_count_delta') or 0)} "
                f"fp_noise_rate={float(row.get('not_actionable_rate') or 0.0):.1f}% "
                f"rate_delta={float(row.get('not_actionable_rate_delta') or 0.0):.1f}% "
                f"status={row.get('status')}"
            )
    return "\n".join(lines)
