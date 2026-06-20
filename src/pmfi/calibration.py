from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pmfi.alert_triage import triage_flags
from pmfi.replay import ReplayResult


VOLUME_SPIKE_RULE = "volume_spike_v1"
TRADE_USD_BUCKETS = (
    "unknown",
    "lt_500",
    "500_to_799",
    "800_to_999",
    "gte_1000",
)
SPIKE_MULTIPLIER_BUCKETS = (
    "unknown",
    "lt_5x",
    "5_to_9x",
    "10_to_24x",
    "gte_25x",
)


@dataclass(frozen=True)
class VolumeSpikeCandidate:
    min_trade_usd: Decimal | None = None
    min_spike_multiplier: Decimal | None = None
    min_baseline_trades: int | None = None
    low_notional_min_baseline_trades: int | None = None
    low_notional_min_baseline_median_usd: Decimal | None = None
    low_notional_max_spike_multiplier: Decimal | None = None
    low_notional_threshold_usd: Decimal | None = None
    history_max: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_trade_usd": float(self.min_trade_usd) if self.min_trade_usd is not None else None,
            "min_spike_multiplier": (
                float(self.min_spike_multiplier)
                if self.min_spike_multiplier is not None
                else None
            ),
            "min_baseline_trades": self.min_baseline_trades,
            "low_notional_min_baseline_trades": self.low_notional_min_baseline_trades,
            "low_notional_min_baseline_median_usd": (
                float(self.low_notional_min_baseline_median_usd)
                if self.low_notional_min_baseline_median_usd is not None
                else None
            ),
            "low_notional_max_spike_multiplier": (
                float(self.low_notional_max_spike_multiplier)
                if self.low_notional_max_spike_multiplier is not None
                else None
            ),
            "low_notional_threshold_usd": (
                float(self.low_notional_threshold_usd)
                if self.low_notional_threshold_usd is not None
                else None
            ),
            "history_max": self.history_max,
        }


def build_volume_spike_candidate_rules(
    base_rules: dict[str, Any],
    candidate: VolumeSpikeCandidate,
) -> dict[str, Any]:
    rules = deepcopy(base_rules)
    rules.setdefault("version", "alert_rules.v1")
    rules.setdefault("rules", {})
    spike = rules["rules"].setdefault(VOLUME_SPIKE_RULE, {})

    if candidate.min_trade_usd is not None:
        spike["min_trade_usd"] = float(candidate.min_trade_usd)
    if candidate.min_spike_multiplier is not None:
        spike["min_spike_multiplier"] = float(candidate.min_spike_multiplier)
    if candidate.min_baseline_trades is not None:
        spike["min_baseline_trades"] = int(candidate.min_baseline_trades)
    if candidate.low_notional_min_baseline_trades is not None:
        spike["low_notional_min_baseline_trades"] = int(
            candidate.low_notional_min_baseline_trades
        )
    if candidate.low_notional_min_baseline_median_usd is not None:
        spike["low_notional_min_baseline_median_usd"] = float(
            candidate.low_notional_min_baseline_median_usd
        )
    if candidate.low_notional_max_spike_multiplier is not None:
        spike["low_notional_max_spike_multiplier"] = float(
            candidate.low_notional_max_spike_multiplier
        )
    if candidate.low_notional_threshold_usd is not None:
        spike["low_notional_threshold_usd"] = float(candidate.low_notional_threshold_usd)
    if candidate.history_max is not None:
        spike["history_max"] = int(candidate.history_max)

    return rules


def summarize_volume_spike_calibration(
    current_results: list[ReplayResult],
    candidate_results: list[ReplayResult],
    *,
    candidate: VolumeSpikeCandidate,
    review_index_by_raw_event_id: (
        Mapping[Any, Any] | Iterable[Mapping[str, Any]] | None
    ) = None,
    review_index: Mapping[Any, Any] | Iterable[Mapping[str, Any]] | None = None,
    details_limit: int = 10,
    delta_records_limit: int | None = None,
) -> dict[str, Any]:
    current = _summarize_results(current_results)
    proposed = _summarize_results(candidate_results)
    review_source = (
        review_index_by_raw_event_id
        if review_index_by_raw_event_id is not None
        else review_index
    )
    normalized_review_index = _normalize_review_index(review_source)
    current_records = current.pop("_volume_spike_records")
    candidate_records = proposed.pop("_volume_spike_records")
    current_spikes = {item["fingerprint"]: item for item in current_records}
    candidate_spikes = {item["fingerprint"]: item for item in candidate_records}

    removed_keys = sorted(set(current_spikes) - set(candidate_spikes))
    added_keys = sorted(set(candidate_spikes) - set(current_spikes))
    removed_key_set = set(removed_keys)
    added_key_set = set(added_keys)
    removed = [record for record in current_records if record["fingerprint"] in removed_key_set]
    added = [record for record in candidate_records if record["fingerprint"] in added_key_set]
    review_comparison = _review_comparison(removed, added, normalized_review_index)
    bounded_details_limit = _bounded_details_limit(details_limit)

    comparison = {
        "normalized_trades_delta": (
            proposed["normalized_trades"] - current["normalized_trades"]
        ),
        "alerts_delta": proposed["alerts"] - current["alerts"],
        "volume_spike_delta": (
            proposed["volume_spike_alerts"] - current["volume_spike_alerts"]
        ),
        "removed_volume_spike_alerts": len(removed),
        "added_volume_spike_alerts": len(added),
        "removed_low_notional_thin_baseline": _count_with_flags(
            removed,
            {"low_notional", "thin_baseline"},
        ),
        "added_low_notional_thin_baseline": _count_with_flags(
            added,
            {"low_notional", "thin_baseline"},
        ),
        "removed_trade_usd_buckets": _trade_usd_buckets(removed),
        "added_trade_usd_buckets": _trade_usd_buckets(added),
        "removed_shape_profile": _volume_spike_delta_shape_profile(removed),
        "added_shape_profile": _volume_spike_delta_shape_profile(added),
        "details_limit": bounded_details_limit,
        "removed_volume_spike_samples": _delta_samples(
            removed,
            normalized_review_index,
            limit=bounded_details_limit,
        ),
        "added_volume_spike_samples": _delta_samples(
            added,
            normalized_review_index,
            limit=bounded_details_limit,
        ),
        **review_comparison,
    }
    if delta_records_limit is not None:
        comparison.update(
            _delta_records_payload(
                removed,
                added,
                normalized_review_index,
                limit=delta_records_limit,
            )
        )

    return {
        "schema_version": "volume_spike_calibration.v1",
        "local_only": True,
        "validate_only": True,
        "candidate": candidate.as_dict(),
        "current": current,
        "candidate_replay": proposed,
        "comparison": comparison,
    }


def summarize_volume_spike_floor_audit(
    results: list[ReplayResult],
    *,
    configured_min_trade_usd: Decimal,
) -> dict[str, Any]:
    current = _summarize_results(results)
    records = current.pop("_volume_spike_records")
    below_floor, unknown_trade_usd = _partition_floor_records(
        records,
        floor=configured_min_trade_usd,
    )
    floor_check = {
        "configured_min_trade_usd": float(configured_min_trade_usd),
        "below_floor_volume_spike_alerts": len(below_floor),
        "unknown_trade_usd_volume_spike_alerts": len(unknown_trade_usd),
        "below_floor_trade_usd_buckets": _trade_usd_buckets(below_floor),
        "unknown_trade_usd_buckets": _trade_usd_buckets(unknown_trade_usd),
        "passed": len(below_floor) == 0 and len(unknown_trade_usd) == 0,
    }
    return {
        "schema_version": "volume_spike_floor_audit.v1",
        "local_only": True,
        "validate_only": True,
        "configured_rule": {
            "rule_id": VOLUME_SPIKE_RULE,
            "min_trade_usd": float(configured_min_trade_usd),
        },
        "current": current,
        "floor_check": floor_check,
        "evidence_status": _floor_evidence_status(current, floor_check),
    }


def _summarize_results(results: list[ReplayResult]) -> dict[str, Any]:
    alerts_by_rule: Counter[str] = Counter()
    volume_spike_flags: Counter[str] = Counter()
    volume_spike_records: list[dict[str, Any]] = []
    alert_count = 0
    markets = set()

    for result_index, result in enumerate(results):
        markets.add((result.trade.venue_code, result.trade.venue_market_id))
        for alert_index, decision in enumerate(result.alerts):
            if not decision.emit_alert:
                continue
            alert_count += 1
            alerts_by_rule[decision.rule_id] += 1
            if decision.rule_id != VOLUME_SPIKE_RULE:
                continue
            flags = triage_flags({"data_quality": decision.data_quality}, decision.evidence)
            for flag in flags:
                volume_spike_flags[flag] += 1
            volume_spike_records.append(
                {
                    "fingerprint": _fingerprint(
                        result,
                        decision.rule_id,
                        result_index,
                        alert_index,
                    ),
                    "triage_flags": flags,
                    "raw_event_id": getattr(result, "raw_event_id", None),
                    "venue_trade_id": result.trade.venue_trade_id,
                    "venue": result.trade.venue_code,
                    "market": result.trade.venue_market_id,
                    "this_trade_usd": decision.evidence.get("this_trade_usd"),
                    "baseline_median_usd": decision.evidence.get("baseline_median_usd"),
                    "spike_multiplier": decision.evidence.get("spike_multiplier"),
                }
            )

    return {
        "normalized_trades": len(results),
        "markets": len(markets),
        "alerts": alert_count,
        "alerts_by_rule": dict(sorted(alerts_by_rule.items())),
        "volume_spike_alerts": alerts_by_rule.get(VOLUME_SPIKE_RULE, 0),
        "volume_spike_triage_flags": dict(sorted(volume_spike_flags.items())),
        "volume_spike_trade_usd_buckets": _trade_usd_buckets(volume_spike_records),
        "_volume_spike_records": volume_spike_records,
    }


def _fingerprint(
    result: ReplayResult,
    rule_id: str,
    result_index: int,
    alert_index: int,
) -> str:
    trade = result.trade
    event_ts = trade.exchange_ts or trade.received_at
    parts = [
        rule_id,
        str(result_index),
        str(alert_index),
        trade.venue_code,
        trade.venue_market_id,
        trade.venue_trade_id or "",
        event_ts.isoformat() if event_ts else "",
        trade.outcome_key,
        str(trade.capital_at_risk_usd),
    ]
    return "|".join(parts)


def _count_with_flags(records: list[dict[str, Any]], required: set[str]) -> int:
    return sum(1 for record in records if required.issubset(set(record["triage_flags"])))


def _volume_spike_delta_shape_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    flag_counts: Counter[str] = Counter()
    for record in records:
        for flag in record.get("triage_flags") or []:
            flag_counts[str(flag)] += 1
    return {
        "total": len(records),
        "trade_usd_buckets": _trade_usd_buckets(records),
        "spike_multiplier_buckets": _spike_multiplier_buckets(records),
        "triage_flag_counts": dict(sorted(flag_counts.items())),
        "near_threshold_count": int(flag_counts.get("near_threshold") or 0),
        "low_notional_thin_baseline_count": _count_with_flags(
            records,
            {"low_notional", "thin_baseline"},
        ),
    }


def _trade_usd_buckets(records: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {key: 0 for key in TRADE_USD_BUCKETS}
    for record in records:
        buckets[_trade_usd_bucket(record.get("this_trade_usd"))] += 1
    return buckets


def _spike_multiplier_buckets(records: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {key: 0 for key in SPIKE_MULTIPLIER_BUCKETS}
    for record in records:
        buckets[_spike_multiplier_bucket(record.get("spike_multiplier"))] += 1
    return buckets


def _normalize_review_index(
    review_index_by_raw_event_id: Mapping[Any, Any] | Iterable[Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]] | None:
    if review_index_by_raw_event_id is None:
        return None
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(review_index_by_raw_event_id, Mapping):
        if "raw_event_id" in review_index_by_raw_event_id:
            row = _review_row_dict(review_index_by_raw_event_id)
            key = _raw_event_key(row.get("raw_event_id"))
            if key is not None:
                normalized[key] = row
            return normalized
        for raw_event_id, row_value in review_index_by_raw_event_id.items():
            key = _raw_event_key(raw_event_id)
            row = _review_row_dict(row_value)
            if key is None:
                key = _raw_event_key(row.get("raw_event_id"))
            if key is not None:
                normalized[key] = row
        return normalized

    for row_value in review_index_by_raw_event_id:
        row = _review_row_dict(row_value)
        key = _raw_event_key(row.get("raw_event_id"))
        if key is not None:
            normalized[key] = row
    return normalized


def _review_comparison(
    removed: list[dict[str, Any]],
    added: list[dict[str, Any]],
    review_index: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    removed_summary = _review_match_summary(removed, review_index)
    added_summary = _review_match_summary(added, review_index)
    return {
        "review_data_provided": review_index is not None,
        "removed_review_matches": removed_summary["matches"],
        "removed_review_unmatched": removed_summary["unmatched"],
        "removed_review_labels": removed_summary["labels"],
        "removed_review_categories": removed_summary["categories"],
        "added_review_matches": added_summary["matches"],
        "added_review_unmatched": added_summary["unmatched"],
        "added_review_labels": added_summary["labels"],
        "added_review_categories": added_summary["categories"],
    }


def _review_match_summary(
    records: list[dict[str, Any]],
    review_index: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    labels: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    matches = 0
    unmatched = 0
    if review_index is None:
        return {
            "matches": 0,
            "unmatched": 0,
            "labels": {},
            "categories": {},
        }

    for record in records:
        key = _raw_event_key(record.get("raw_event_id"))
        review = review_index.get(key) if key is not None else None
        if review is None:
            unmatched += 1
            continue
        matches += 1
        label = _review_value(review, "review_label", "label")
        if label:
            labels[str(label)] += 1
        else:
            labels["unreviewed"] += 1
        category = _review_value(review, "review_category", "category", "false_positive_category")
        if category:
            categories[str(category)] += 1
        else:
            categories["uncategorized"] += 1

    return {
        "matches": matches,
        "unmatched": unmatched,
        "labels": dict(sorted(labels.items())),
        "categories": dict(sorted(categories.items())),
    }


def _bounded_details_limit(value: int) -> int:
    return max(0, min(int(value), 50))


def _delta_records_payload(
    removed: list[dict[str, Any]],
    added: list[dict[str, Any]],
    review_index: dict[str, dict[str, Any]] | None,
    *,
    limit: int,
) -> dict[str, Any]:
    parsed_limit = int(limit)
    if parsed_limit < 0:
        raise ValueError("delta_records_limit must be >= 0")
    removed_records = _delta_records(removed, review_index, limit=parsed_limit)
    added_records = _delta_records(added, review_index, limit=parsed_limit)
    return {
        "delta_records_limit": parsed_limit,
        "removed_volume_spike_records": removed_records,
        "added_volume_spike_records": added_records,
        "removed_delta_records_truncated": _records_truncated(removed, parsed_limit),
        "added_delta_records_truncated": _records_truncated(added, parsed_limit),
    }


def _records_truncated(records: list[dict[str, Any]], limit: int) -> bool:
    return limit > 0 and len(records) > limit


def _delta_records(
    records: list[dict[str, Any]],
    review_index: dict[str, dict[str, Any]] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected = records if limit == 0 else records[:limit]
    return [_delta_sample(record, review_index) for record in selected]


def _delta_samples(
    records: list[dict[str, Any]],
    review_index: dict[str, dict[str, Any]] | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    return [
        _delta_sample(record, review_index)
        for record in records[:limit]
    ]


def _delta_sample(
    record: dict[str, Any],
    review_index: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    review = None
    key = _raw_event_key(record.get("raw_event_id"))
    if review_index is not None and key is not None:
        review = review_index.get(key)
    return {
        "raw_event_id": _json_value(record.get("raw_event_id")),
        "venue_trade_id": _json_value(record.get("venue_trade_id")),
        "venue": _json_value(record.get("venue")),
        "market": _json_value(record.get("market")),
        "this_trade_usd": _json_value(record.get("this_trade_usd")),
        "baseline_median_usd": _json_value(record.get("baseline_median_usd")),
        "spike_multiplier": _json_value(record.get("spike_multiplier")),
        "triage_flags": list(record.get("triage_flags") or []),
        "review": _review_sample(review),
    }


def _review_sample(review: dict[str, Any] | None) -> dict[str, Any]:
    if review is None:
        return {
            "matched": False,
            "alert_id": None,
            "trade_id": None,
            "label": None,
            "category": None,
            "reviewed_at": None,
        }
    label = _review_value(review, "review_label", "label") or "unreviewed"
    category = (
        _review_value(review, "review_category", "category", "false_positive_category")
        or "uncategorized"
    )
    reviewed_at = _review_value(review, "reviewed_at")
    if hasattr(reviewed_at, "isoformat"):
        reviewed_at = reviewed_at.isoformat()
    return {
        "matched": True,
        "alert_id": _json_value(_review_value(review, "alert_id")),
        "trade_id": _json_value(_review_value(review, "trade_id")),
        "label": str(label),
        "category": str(category),
        "reviewed_at": _json_value(reviewed_at),
    }


def _review_row_dict(row_value: Any) -> dict[str, Any]:
    if row_value is None:
        return {}
    if isinstance(row_value, Mapping):
        return dict(row_value)
    try:
        return dict(row_value)
    except (TypeError, ValueError):
        return {"review_label": row_value}


def _review_value(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _raw_event_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return text


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _partition_floor_records(
    records: list[dict[str, Any]],
    *,
    floor: Decimal,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    below_floor: list[dict[str, Any]] = []
    unknown_trade_usd: list[dict[str, Any]] = []
    for record in records:
        amount = _trade_usd_decimal(record.get("this_trade_usd"))
        if amount is None:
            unknown_trade_usd.append(record)
        elif amount < floor:
            below_floor.append(record)
    return below_floor, unknown_trade_usd


def _floor_evidence_status(
    current: dict[str, Any],
    floor_check: dict[str, Any],
) -> str:
    if floor_check["unknown_trade_usd_volume_spike_alerts"]:
        return "unknown_trade_usd"
    if floor_check["below_floor_volume_spike_alerts"]:
        return "below_floor_volume_spikes"
    if current["volume_spike_alerts"] == 0:
        return "no_current_volume_spikes"
    return "current_floor_clean"


def _trade_usd_bucket(value: Any) -> str:
    amount = _trade_usd_decimal(value)
    if amount is None:
        return "unknown"
    if amount < Decimal("500"):
        return "lt_500"
    if amount < Decimal("800"):
        return "500_to_799"
    if amount < Decimal("1000"):
        return "800_to_999"
    return "gte_1000"


def _spike_multiplier_bucket(value: Any) -> str:
    amount = _trade_usd_decimal(value)
    if amount is None:
        return "unknown"
    if amount < Decimal("5"):
        return "lt_5x"
    if amount < Decimal("10"):
        return "5_to_9x"
    if amount < Decimal("25"):
        return "10_to_24x"
    return "gte_25x"


def _trade_usd_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount
