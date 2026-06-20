from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def calibration_decision_root() -> Path:
    from pmfi.config import ROOT

    return ROOT / "reports" / "calibration-decisions"


def resolve_calibration_decision_file(name: str) -> Path:
    if not name or "/" in name or "\\" in name:
        raise ValueError("decision name must be a direct filename")
    candidate = Path(name)
    if candidate.name != name or candidate.suffix != ".json":
        raise ValueError("decision name must be a direct .json filename")

    root = calibration_decision_root().resolve()
    decision_path = (root / candidate.name).resolve()
    try:
        decision_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "decision name must stay inside calibration decision root"
        ) from exc
    return decision_path


def list_calibration_decision_files() -> list[dict[str, Any]]:
    root = calibration_decision_root()
    if not root.is_dir():
        return []

    decisions: list[tuple[float, str, dict[str, Any]]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        stat = path.stat()
        decisions.append((
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
    decisions.sort(key=lambda item: (-item[0], item[1]))
    return [decision for _, _, decision in decisions]


def load_calibration_decision(name: str) -> dict[str, Any]:
    decision_path = resolve_calibration_decision_file(name)
    if not decision_path.is_file():
        raise FileNotFoundError(name)
    record = json.loads(decision_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise TypeError("calibration decision record must be a JSON object")
    return record


def _mapping_value(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _dict_value(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _count_value(counts: Mapping[str, Any], key: str) -> int:
    try:
        return int(counts.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decision_readiness(
    *,
    decision: Any,
    review_recommendation: Any,
    cluster_totals: Mapping[str, Any],
) -> str:
    readiness_counts = _dict_value(cluster_totals.get("candidate_readiness_counts"))
    assessment_counts = _dict_value(cluster_totals.get("assessment_counts"))
    if (
        _count_value(readiness_counts, "blocked-true-positive-risk") > 0
        or _count_value(assessment_counts, "true-positive-risk") > 0
    ):
        return "blocked-by-cluster-true-positive-risk"

    if review_recommendation == "blocked-by-true-positive-risk":
        return "blocked-by-reviewed-true-positive-risk"

    uncovered = _int_or_none(cluster_totals.get("uncovered_market_cluster_count"))
    if uncovered is not None and uncovered > 0:
        return "needs-cluster-review"

    if isinstance(review_recommendation, str) and review_recommendation:
        return review_recommendation

    if isinstance(decision, str) and decision:
        return decision

    return "unknown"


def summarize_calibration_decision_record(
    name: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    packet_selection = _mapping_value(record.get("packet_selection"))
    comparison = _mapping_value(record.get("comparison"))
    review_summary = _mapping_value(record.get("review_summary"))
    cluster_review_coverage = _mapping_value(record.get("cluster_review_coverage"))
    aggregate = _mapping_value(comparison.get("aggregate"))
    cluster_totals = _mapping_value(cluster_review_coverage.get("totals"))
    packet_names = _list_value(packet_selection.get("names"))
    review_recommendation = review_summary.get("recommendation")
    decision = record.get("decision")

    return {
        "name": name,
        "schema_version": record.get("schema_version"),
        "decision": decision,
        "decision_readiness": _decision_readiness(
            decision=decision,
            review_recommendation=review_recommendation,
            cluster_totals=cluster_totals,
        ),
        "generated_at": record.get("generated_at"),
        "rationale": record.get("rationale"),
        "local_only": record.get("local_only"),
        "validate_only": record.get("validate_only"),
        "config_mutation": record.get("config_mutation"),
        "db_mutation": record.get("db_mutation"),
        "live_calls": record.get("live_calls"),
        "packet_count": packet_selection.get("count", len(packet_names)),
        "packet_names": packet_names,
        "comparison_packet_count": comparison.get("packet_count"),
        "candidate_groups": comparison.get("candidate_groups"),
        "removed_records": aggregate.get("removed_records"),
        "added_records": aggregate.get("added_records"),
        "removed_review_labels": _dict_value(aggregate.get("removed_review_labels")),
        "added_review_labels": _dict_value(aggregate.get("added_review_labels")),
        "review_recommendation": review_recommendation,
        "review_risk_counts": _dict_value(review_summary.get("risk_counts")),
        "cluster_review_queue_clusters": cluster_totals.get("market_cluster_count"),
        "cluster_review_covered_clusters": cluster_totals.get(
            "covered_market_cluster_count"
        ),
        "cluster_review_uncovered_clusters": cluster_totals.get(
            "uncovered_market_cluster_count"
        ),
        "cluster_review_assessment_counts": _dict_value(
            cluster_totals.get("assessment_counts")
        ),
        "cluster_review_readiness_counts": _dict_value(
            cluster_totals.get("candidate_readiness_counts")
        ),
        "cluster_review_signal_counts": _dict_value(
            cluster_totals.get("candidate_signal_counts")
        ),
        "cluster_review_next_action_counts": _dict_value(
            cluster_totals.get("candidate_next_action_counts")
        ),
        "cluster_review_payload_status_counts": _dict_value(
            cluster_totals.get("raw_event_lookup_payload_status_counts")
        ),
        "repeated_removed_raw_event_ids": _list_value(
            aggregate.get("repeated_removed_raw_event_ids")
        ),
        "repeated_added_raw_event_ids": _list_value(
            aggregate.get("repeated_added_raw_event_ids")
        ),
    }


def build_calibration_decision_record(
    comparison: Mapping[str, Any],
    *,
    selected_packet_names: Iterable[str],
    decision: str,
    rationale: str,
    review_summary: Mapping[str, Any] | None = None,
    cluster_review_coverage: Mapping[str, Any] | None = None,
    output_artifact_path: str | None = None,
    output_artifact_name: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    packet_names = list(selected_packet_names)
    record: dict[str, Any] = {
        "schema_version": "calibration_decision_record.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "generated_at": generated.isoformat(),
        "decision": decision,
        "rationale": rationale,
        "packet_selection": {
            "names": packet_names,
            "count": len(packet_names),
        },
        "comparison": deepcopy(dict(comparison)),
    }
    if review_summary is not None:
        record["review_summary"] = deepcopy(dict(review_summary))
    if cluster_review_coverage is not None:
        record["cluster_review_coverage"] = deepcopy(dict(cluster_review_coverage))
    if output_artifact_path is not None or output_artifact_name is not None:
        record["output_artifact"] = {
            "path": output_artifact_path,
            "name": output_artifact_name,
        }
    return record
