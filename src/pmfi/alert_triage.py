"""Pure alert evidence parsing and deterministic review triage helpers."""
from __future__ import annotations

import json
from typing import Any


LOW_NOTIONAL_USD = 5000.0
THIN_BASELINE_SAMPLE_SIZE = 10
THIN_BASELINE_TRADES = 20
NEAR_THRESHOLD_RATIO = 1.25
NORMAL_DATA_QUALITY = {
    "ok",
    "live",
    "verified",
    "baseline_available",
    "baseline_sufficient",
    "in_window",
    "oi_present",
}


def parse_evidence(evidence: Any) -> dict[str, Any]:
    if isinstance(evidence, dict):
        return evidence
    if isinstance(evidence, str):
        try:
            parsed = json.loads(evidence)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_degraded_reasons(evidence: dict[str, Any]) -> bool:
    reasons = evidence.get("degraded_reasons") or evidence.get("data_quality_reasons")
    if reasons in (None, "", [], ()):
        return False
    if isinstance(reasons, str):
        return reasons.strip() not in {"", "[]", "()"}
    return bool(reasons)


def _is_near_threshold(evidence: dict[str, Any]) -> bool:
    spike = _as_float(evidence.get("spike_multiplier"))
    min_spike = _as_float(evidence.get("min_spike_multiplier"))
    if spike is not None and min_spike and spike >= min_spike:
        return spike / min_spike <= NEAR_THRESHOLD_RATIO

    capital = _as_float(evidence.get("capital_at_risk_usd"))
    if capital is None:
        capital = _as_float(evidence.get("this_trade_usd"))
    if capital is None:
        return False

    for key in (
        "min_capital_threshold_usd",
        "min_capital_at_risk_usd",
        "threshold_usd",
        "p99_threshold_usd",
        "p99_trade_usd",
        "p995_trade_usd",
    ):
        threshold = _as_float(evidence.get(key))
        if threshold and capital >= threshold:
            return capital / threshold <= NEAR_THRESHOLD_RATIO
    return False


def triage_flags(row: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    flags: list[str] = []

    notional_values = (
        _as_float(evidence.get("this_trade_usd")),
        _as_float(evidence.get("capital_at_risk_usd")),
    )
    if any(value is not None and value < LOW_NOTIONAL_USD for value in notional_values):
        flags.append("low_notional")

    baseline_sample_size = _as_int(evidence.get("baseline_sample_size"))
    baseline_trades = _as_int(evidence.get("baseline_trades"))
    if (
        evidence.get("baseline_state") == "baseline_sparse"
        or (baseline_sample_size is not None and baseline_sample_size < THIN_BASELINE_SAMPLE_SIZE)
        or (baseline_trades is not None and baseline_trades <= THIN_BASELINE_TRADES)
    ):
        flags.append("thin_baseline")

    if _is_near_threshold(evidence):
        flags.append("near_threshold")

    data_quality = str(row.get("data_quality") or "").lower()
    if (
        _has_degraded_reasons(evidence)
        or (data_quality and data_quality not in NORMAL_DATA_QUALITY)
    ):
        flags.append("degraded_data_quality")

    if "raw_event_id" in row and "trade_id" in row and (
        not row.get("raw_event_id") or not row.get("trade_id")
    ):
        flags.append("missing_lineage")

    return flags
