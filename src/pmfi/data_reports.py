from __future__ import annotations

from typing import Any, Iterable, Mapping


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
}


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except KeyError:
        return default


def _is_skipped_non_trade(venue_code: str, source_event_type: str) -> bool:
    return source_event_type in NON_TRADE_RAW_EVENT_TYPES_BY_VENUE.get(venue_code, frozenset())


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


def summarize_data_coverage_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {bucket: 0 for bucket in DATA_COVERAGE_BUCKETS}
    event_type_rows: list[dict[str, Any]] = []

    for row in rows:
        count = int(_row_value(row, "cnt", 0) or 0)
        bucket = classify_data_coverage_row(row)
        counts[bucket] += count
        event_type_rows.append(
            {
                "bucket": bucket,
                "venue_code": str(_row_value(row, "venue_code", "") or ""),
                "source_event_type": str(_row_value(row, "source_event_type", "") or ""),
                "has_normalized": bool(_row_value(row, "has_normalized", False)),
                "has_dead_letter": bool(_row_value(row, "has_dead_letter", False)),
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
        "has_unaccounted_warning": counts["unaccounted"] > 0,
        "unaccounted_event_types": unaccounted_event_types,
        "by_event_type": event_type_rows,
        "skipped_non_trade_types": {
            venue: sorted(types)
            for venue, types in sorted(NON_TRADE_RAW_EVENT_TYPES_BY_VENUE.items())
        },
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
        "Skipped non-trade types: "
        + "; ".join(
            f"{venue}={','.join(types)}"
            for venue, types in (report.get("skipped_non_trade_types") or {}).items()
        )
    )
    if report.get("has_unaccounted_warning"):
        lines.append("WARNING: unaccounted trade-type raw events detected.")
        for row in report.get("unaccounted_event_types") or []:
            lines.append(
                "  "
                f"{row.get('venue_code')} {row.get('source_event_type')}: "
                f"{int(row.get('count') or 0)}"
            )
    return "\n".join(lines)
