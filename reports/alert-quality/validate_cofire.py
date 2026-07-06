from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pmfi.pipeline.cofire import derive_event_ticker, group_cofire  # noqa: E402


DEFAULT_INPUT = ROOT / "reports" / "alert-quality" / "outcomes-2026-07-02.json"
DEFAULT_OUTPUT = (
    ROOT / "reports" / "alert-quality" / "co-fire-validation-2026-07-06.md"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate offline co-fire grouping against the labeled cohort."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--window-s", type=float, default=900.0)
    args = parser.parse_args(argv)

    input_path = _resolve_input_path(args.input)
    alerts = _load_alerts(input_path)
    groups = group_cofire(
        [_cofire_item(alert) for alert in alerts],
        window_s=args.window_s,
    )
    metrics = _build_metrics(alerts, groups, args.window_s)
    report = _render_report(input_path, args.window_s, metrics)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")

    if metrics["hard_failures"]:
        for failure in metrics["hard_failures"]:
            print(f"FAIL: {failure}")
        print(f"wrote {args.output}")
        return 1

    print(
        "PASS: "
        f"removed_tp={metrics['removed_tp_count']} "
        f"tp_visible={metrics['tp_visible_count']}/{metrics['tp_count']} "
        f"missed_labeled_hedge_fp={metrics['missed_hedge_pair_count']} "
        f"fp_group_reduction={metrics['fp_operator_item_reduction']} "
        f"queue_reduction={metrics['queue_item_reduction']}"
    )
    print(f"wrote {args.output}")
    return 0


def _load_alerts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"missing cohort input: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    alerts = data.get("alerts") if isinstance(data, dict) else None
    if not isinstance(alerts, list) or not alerts:
        raise SystemExit(f"empty or invalid cohort input: {path}")
    return alerts


def _resolve_input_path(path: Path) -> Path:
    if path.exists() or path != DEFAULT_INPUT:
        return path

    workspace_root = ROOT.parents[1] if len(ROOT.parents) > 1 else ROOT
    root_input = workspace_root / "reports" / "alert-quality" / path.name
    if root_input.exists():
        return root_input
    return path


def _cofire_item(alert: dict[str, Any]) -> dict[str, Any]:
    venue = str(alert.get("venue") or "")
    market = str(alert.get("market") or "")
    return {
        "short_id": str(alert["short_id"]),
        "id": str(alert["short_id"]),
        "venue": venue,
        "venue_code": venue,
        "market": market,
        "venue_market_id": market,
        "event_ticker": derive_event_ticker(market, venue),
        "rule": str(alert.get("rule") or ""),
        "rule_key": str(alert.get("rule") or ""),
        "outcome_key": alert.get("outcome_key"),
        "fired_at": str(alert["fired_at"]),
        "label": alert.get("proposed_label"),
        "category": alert.get("proposed_category"),
    }


def _build_metrics(
    alerts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    window_s: float,
) -> dict[str, Any]:
    alerts_by_id = {str(alert["short_id"]): alert for alert in alerts}
    derived_by_id = {
        str(alert["short_id"]): derive_event_ticker(
            str(alert.get("market") or ""),
            str(alert.get("venue") or ""),
        )
        for alert in alerts
    }
    group_index = _group_index(groups)

    labels = Counter(str(alert.get("proposed_label") or "") for alert in alerts)
    categories = Counter(
        str(alert.get("proposed_category") or "none") for alert in alerts
    )

    tp_ids = [
        str(alert["short_id"])
        for alert in alerts
        if alert.get("proposed_label") == "tp"
    ]
    visible_tp_ids = sorted(tp_id for tp_id in tp_ids if tp_id in group_index)
    removed_tp = sorted(set(tp_ids) - set(visible_tp_ids))

    fp_ids = [
        str(alert["short_id"])
        for alert in alerts
        if alert.get("proposed_label") == "fp"
    ]
    fp_group_ids = {group_index[fp_id] for fp_id in fp_ids if fp_id in group_index}

    hedge_fp_ids = [
        str(alert["short_id"])
        for alert in alerts
        if alert.get("proposed_label") == "fp"
        and alert.get("proposed_category") == "cross_market_hedge"
    ]
    hedge_fp_group_ids = {
        group_index[fp_id] for fp_id in hedge_fp_ids if fp_id in group_index
    }
    hedge_fp_in_multi = [
        fp_id
        for fp_id in hedge_fp_ids
        if _group_by_id(groups, group_index[fp_id])["leg_count"] > 1
    ]
    hedge_fp_singletons = sorted(set(hedge_fp_ids) - set(hedge_fp_in_multi))

    event_mismatches = _event_ticker_mismatches(alerts, derived_by_id)
    parent_label_conflicts = _parent_label_conflicts(groups)
    hedge_pairs = _candidate_hedge_pairs(alerts, derived_by_id, window_s)
    missed_hedge_pairs = [
        pair
        for pair in hedge_pairs
        if group_index[pair["left_id"]] != group_index[pair["right_id"]]
    ]

    g39_623_grouped = (
        "39bd1f35" in group_index
        and "623164c5" in group_index
        and group_index["39bd1f35"] == group_index["623164c5"]
    )

    hard_failures = []
    if event_mismatches:
        hard_failures.append("derived_event_ticker_mismatches")
    if removed_tp:
        hard_failures.append("removed_tp_nonzero")
    if len(visible_tp_ids) != len(tp_ids):
        hard_failures.append("tp_leg_visibility_loss")
    if missed_hedge_pairs:
        hard_failures.append("missed_labeled_hedge_fp")
    if not g39_623_grouped:
        hard_failures.append("required_39bd1f35_623164c5_pair_not_grouped")

    return {
        "alerts": alerts,
        "alerts_by_id": alerts_by_id,
        "groups": groups,
        "labels": labels,
        "categories": categories,
        "event_mismatches": event_mismatches,
        "parent_label_conflicts": parent_label_conflicts,
        "hedge_pairs": hedge_pairs,
        "missed_hedge_pairs": missed_hedge_pairs,
        "group_index": group_index,
        "tp_count": len(tp_ids),
        "tp_visible_count": len(visible_tp_ids),
        "removed_tp": removed_tp,
        "removed_tp_count": len(removed_tp),
        "fp_count": len(fp_ids),
        "fp_operator_item_after": len(fp_group_ids),
        "fp_operator_item_reduction": len(fp_ids) - len(fp_group_ids),
        "hedge_fp_count": len(hedge_fp_ids),
        "hedge_fp_group_after": len(hedge_fp_group_ids),
        "hedge_fp_group_reduction": len(hedge_fp_ids) - len(hedge_fp_group_ids),
        "hedge_fp_in_multi": sorted(hedge_fp_in_multi),
        "hedge_fp_singletons": hedge_fp_singletons,
        "queue_items_before": len(alerts),
        "queue_items_after": len(groups),
        "queue_item_reduction": len(alerts) - len(groups),
        "missed_hedge_pair_count": len(missed_hedge_pairs),
        "required_39_623_grouped": g39_623_grouped,
        "hard_failures": hard_failures,
    }


def _group_index(groups: list[dict[str, Any]]) -> dict[str, int]:
    index: dict[str, int] = {}
    for group_idx, group in enumerate(groups):
        for leg in group["legs"]:
            index[str(leg["short_id"])] = group_idx
    return index


def _group_by_id(groups: list[dict[str, Any]], group_idx: int) -> dict[str, Any]:
    return groups[group_idx]


def _event_ticker_mismatches(
    alerts: list[dict[str, Any]],
    derived_by_id: dict[str, str | None],
) -> list[dict[str, str | None]]:
    mismatches = []
    for alert in alerts:
        facts = alert.get("facts")
        if not isinstance(facts, dict):
            continue
        expected = facts.get("event_ticker")
        if not expected:
            continue
        short_id = str(alert["short_id"])
        derived = derived_by_id[short_id]
        if derived != expected:
            mismatches.append(
                {
                    "short_id": short_id,
                    "market": str(alert.get("market") or ""),
                    "expected": str(expected),
                    "derived": derived,
                }
            )
    return mismatches


def _parent_label_conflicts(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts = []
    for idx, group in enumerate(groups):
        legs = list(group["legs"])
        labels = {str(leg.get("label") or "") for leg in legs}
        if "tp" not in labels or not (labels - {"tp"}):
            continue
        conflicts.append(
            {
                "group_idx": idx,
                "event_ticker": group["event_ticker"],
                "leg_count": group["leg_count"],
                "labels": sorted(labels),
                "members": [
                    {
                        "short_id": leg["short_id"],
                        "label": leg["label"],
                        "category": leg["category"],
                        "market": leg["market"],
                        "rule": leg["rule"],
                    }
                    for leg in legs
                ],
            }
        )
    return conflicts


def _candidate_hedge_pairs(
    alerts: list[dict[str, Any]],
    derived_by_id: dict[str, str | None],
    window_s: float,
) -> list[dict[str, Any]]:
    hedge_alerts = [
        alert
        for alert in alerts
        if alert.get("proposed_label") == "fp"
        and alert.get("proposed_category") == "cross_market_hedge"
    ]
    pairs = []
    for left, right in itertools.combinations(hedge_alerts, 2):
        left_id = str(left["short_id"])
        right_id = str(right["short_id"])
        event_ticker = derived_by_id[left_id]
        if event_ticker is None or event_ticker != derived_by_id[right_id]:
            continue
        if left.get("market") == right.get("market"):
            continue
        delta_s = abs(
            (
                _parse_dt(left["fired_at"]) - _parse_dt(right["fired_at"])
            ).total_seconds()
        )
        if delta_s >= window_s:
            continue
        pairs.append(
            {
                "left_id": left_id,
                "right_id": right_id,
                "event_ticker": event_ticker,
                "delta_s": round(delta_s, 3),
                "left_market": left.get("market"),
                "right_market": right.get("market"),
            }
        )
    return pairs


def _parse_dt(value: Any) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _render_report(
    input_path: Path,
    window_s: float,
    metrics: dict[str, Any],
) -> str:
    status = "PASS" if not metrics["hard_failures"] else "FAIL"
    lines = [
        "# Co-Fire Validation - 2026-07-06",
        "",
        f"Verdict: **{status}** for offline Candidate-B co-fire grouping.",
        "",
        "Live emission remains out of scope: this branch adds a standalone "
        "primitive plus this harness/report only; it does not wire `cofire` "
        "into runner, engine, rules, scoring, SQL, or CLI paths.",
        "",
        "## Inputs",
        "",
        _table(
            ["Field", "Value"],
            [
                ["cohort", str(input_path)],
                ["window_s", _fmt_num(window_s)],
                ["alerts", metrics["queue_items_before"]],
                ["labels", _counter_text(metrics["labels"])],
                ["categories", _counter_text(metrics["categories"])],
            ],
        ),
        "",
        "## Hard Gates",
        "",
        _table(
            ["Gate", "Value", "Status"],
            [
                [
                    "derived_event_ticker_mismatches",
                    len(metrics["event_mismatches"]),
                    _pass_fail(not metrics["event_mismatches"]),
                ],
                [
                    "removed_tp",
                    metrics["removed_tp_count"],
                    _pass_fail(metrics["removed_tp_count"] == 0),
                ],
                [
                    "tp_leg_visible_after",
                    f"{metrics['tp_visible_count']}/{metrics['tp_count']}",
                    _pass_fail(metrics["tp_visible_count"] == metrics["tp_count"]),
                ],
                [
                    "missed_labeled_hedge_fp",
                    metrics["missed_hedge_pair_count"],
                    _pass_fail(metrics["missed_hedge_pair_count"] == 0),
                ],
                [
                    "39bd1f35 <-> 623164c5 grouped",
                    metrics["required_39_623_grouped"],
                    _pass_fail(metrics["required_39_623_grouped"]),
                ],
            ],
        ),
        "",
        "## Reduction Metrics",
        "",
        _table(
            ["Metric", "Before", "After", "Reduction"],
            [
                [
                    "queue items",
                    metrics["queue_items_before"],
                    metrics["queue_items_after"],
                    metrics["queue_item_reduction"],
                ],
                [
                    "fp operator items",
                    metrics["fp_count"],
                    metrics["fp_operator_item_after"],
                    metrics["fp_operator_item_reduction"],
                ],
                [
                    "cross_market_hedge fp groups",
                    metrics["hedge_fp_count"],
                    metrics["hedge_fp_group_after"],
                    metrics["hedge_fp_group_reduction"],
                ],
            ],
        ),
        "",
        "## Leg Visibility Diagnostics",
        "",
        "Candidate B drops no legs. The conflicts below show where any "
        "single parent label or majority-label suppression would be unsafe.",
        "",
        _table(
            ["Metric", "Value"],
            [
                ["parent_label_conflicts", len(metrics["parent_label_conflicts"])],
                [
                    "cross_market_hedge fp in multi-leg groups",
                    len(metrics["hedge_fp_in_multi"]),
                ],
                [
                    "cross_market_hedge fp singleton legs",
                    _csv(metrics["hedge_fp_singletons"]),
                ],
            ],
        ),
        "",
        "## Parent Label Conflicts",
        "",
        _conflict_table(metrics["parent_label_conflicts"]),
        "",
        "## Labeled Hedge Pair Coverage",
        "",
        _hedge_pair_table(metrics["hedge_pairs"], metrics["group_index"]),
        "",
        "## Missed Labeled Hedge Pairs",
        "",
        _missed_pair_table(metrics["missed_hedge_pairs"]),
        "",
        "## Multi-Leg Groups",
        "",
        _multi_group_table(metrics["groups"]),
        "",
        "## Notes",
        "",
        "- The source cohort is read-only; this harness writes only this report.",
        "- No DB, live API, seeding, or artifact generation is used.",
        "- Parent label conflicts are not Candidate-B failures because every "
        "leg remains visible with its own label and category.",
        "",
    ]
    return "\n".join(lines)


def _conflict_table(conflicts: list[dict[str, Any]]) -> str:
    if not conflicts:
        return "None."
    rows = []
    for conflict in conflicts:
        rows.append(
            [
                conflict["group_idx"],
                conflict["event_ticker"],
                conflict["leg_count"],
                ", ".join(conflict["labels"]),
                _member_text(conflict["members"]),
            ]
        )
    return _table(["Group", "Event", "Legs", "Labels", "Members"], rows)


def _hedge_pair_table(
    pairs: list[dict[str, Any]],
    group_index: dict[str, int],
) -> str:
    if not pairs:
        return "None."
    rows = []
    for pair in pairs:
        grouped = group_index[pair["left_id"]] == group_index[pair["right_id"]]
        rows.append(
            [
                f"{pair['left_id']} <-> {pair['right_id']}",
                pair["event_ticker"],
                pair["delta_s"],
                grouped,
            ]
        )
    return _table(["Pair", "Event", "Delta s", "Grouped"], rows)


def _missed_pair_table(pairs: list[dict[str, Any]]) -> str:
    if not pairs:
        return "None."
    rows = [
        [
            f"{pair['left_id']} <-> {pair['right_id']}",
            pair["event_ticker"],
            pair["delta_s"],
        ]
        for pair in pairs
    ]
    return _table(["Pair", "Event", "Delta s"], rows)


def _multi_group_table(groups: list[dict[str, Any]]) -> str:
    rows = []
    for idx, group in enumerate(groups):
        if group["leg_count"] <= 1:
            continue
        labels = Counter(str(leg.get("label") or "") for leg in group["legs"])
        categories = Counter(
            str(leg.get("category") or "none") for leg in group["legs"]
        )
        rows.append(
            [
                idx,
                group["event_ticker"],
                group["leg_count"],
                _counter_text(labels),
                _counter_text(categories),
                _member_text(group["legs"]),
            ]
        )
    if not rows:
        return "None."
    return _table(
        ["Group", "Event", "Legs", "Labels", "Categories", "Members"],
        rows,
    )


def _member_text(members: list[dict[str, Any]]) -> str:
    values = []
    for member in members:
        category = member.get("category") or "none"
        values.append(
            f"{member['short_id']}:{member.get('label')}:{category}:"
            f"{member.get('market')}"
        )
    return "; ".join(values)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    rendered = [
        "| " + " | ".join(_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        rendered.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(rendered)


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _counter_text(counter: Counter[str]) -> str:
    return ", ".join(f"{key}={counter[key]}" for key in sorted(counter))


def _pass_fail(value: bool) -> str:
    return "PASS" if value else "FAIL"


def _csv(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _fmt_num(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
