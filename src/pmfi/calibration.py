from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pmfi.alert_triage import triage_flags
from pmfi.replay import ReplayResult


VOLUME_SPIKE_RULE = "volume_spike_v1"


@dataclass(frozen=True)
class VolumeSpikeCandidate:
    min_trade_usd: Decimal | None = None
    min_spike_multiplier: Decimal | None = None
    min_baseline_trades: int | None = None
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
    if candidate.history_max is not None:
        spike["history_max"] = int(candidate.history_max)

    return rules


def summarize_volume_spike_calibration(
    current_results: list[ReplayResult],
    candidate_results: list[ReplayResult],
    *,
    candidate: VolumeSpikeCandidate,
) -> dict[str, Any]:
    current = _summarize_results(current_results)
    proposed = _summarize_results(candidate_results)
    current_spikes = {
        item["fingerprint"]: item for item in current.pop("_volume_spike_records")
    }
    candidate_spikes = {
        item["fingerprint"]: item for item in proposed.pop("_volume_spike_records")
    }

    removed_keys = sorted(set(current_spikes) - set(candidate_spikes))
    added_keys = sorted(set(candidate_spikes) - set(current_spikes))
    removed = [current_spikes[key] for key in removed_keys]
    added = [candidate_spikes[key] for key in added_keys]

    return {
        "schema_version": "volume_spike_calibration.v1",
        "local_only": True,
        "validate_only": True,
        "candidate": candidate.as_dict(),
        "current": current,
        "candidate_replay": proposed,
        "comparison": {
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
        },
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
