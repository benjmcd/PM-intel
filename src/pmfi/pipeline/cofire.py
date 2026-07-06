from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any


def derive_event_ticker(venue_market_id: str, venue_code: str) -> str | None:
    """Derive the Kalshi event ticker from a three-segment market ticker."""
    if str(venue_code).lower() != "kalshi":
        return None
    ticker = str(venue_market_id or "").strip()
    if len(ticker.split("-")) < 3:
        return None
    return ticker.rsplit("-", 1)[0]


def group_cofire(
    items: Iterable[Mapping[str, Any]],
    *,
    window_s: float = 900,
) -> list[dict[str, Any]]:
    """Group same-event legs by pairwise time radius while retaining every leg."""
    materialized = [_normalize_leg(item, idx) for idx, item in enumerate(items)]
    if not materialized:
        return []

    groups: list[dict[str, Any]] = []
    by_event: dict[str, list[dict[str, Any]]] = {}

    for leg in materialized:
        event_ticker = leg["event_ticker"]
        if event_ticker is None:
            groups.append(_build_group([leg], None))
            continue
        by_event.setdefault(str(event_ticker), []).append(leg)

    for event_ticker, legs in by_event.items():
        groups.extend(
            _event_groups(
                sorted(legs, key=lambda leg: (leg["_fired_at"], leg["_index"])),
                event_ticker,
                float(window_s),
            )
        )

    sorted_groups = sorted(
        groups,
        key=lambda group: (
            group["_sort_fired_at"],
            group["_sort_index"],
        ),
    )
    return [_public_group(group) for group in sorted_groups]


def _event_groups(
    legs: list[dict[str, Any]],
    event_ticker: str,
    window_s: float,
) -> list[dict[str, Any]]:
    parents = list(range(len(legs)))

    def find(idx: int) -> int:
        while parents[idx] != idx:
            parents[idx] = parents[parents[idx]]
            idx = parents[idx]
        return idx

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for i, left in enumerate(legs):
        for j in range(i + 1, len(legs)):
            right = legs[j]
            delta = abs((right["_fired_at"] - left["_fired_at"]).total_seconds())
            if delta < window_s:
                union(i, j)

    components: dict[int, list[dict[str, Any]]] = {}
    for idx, leg in enumerate(legs):
        components.setdefault(find(idx), []).append(leg)

    return [
        _build_group(
            sorted(component, key=lambda leg: (leg["_fired_at"], leg["_index"])),
            event_ticker,
        )
        for component in components.values()
    ]


def _normalize_leg(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    fired_at = _parse_fired_at(item.get("fired_at"))
    venue = str(item.get("venue_code") or item.get("venue") or "")
    market = str(item.get("venue_market_id") or item.get("market") or "")
    event_ticker = _item_event_ticker(item, venue, market)
    short_id = str(
        item.get("short_id")
        or item.get("id")
        or item.get("alert_id")
        or f"leg-{index}"
    )
    rule = str(item.get("rule") or item.get("rule_key") or "")
    label = item.get("label")
    if label is None:
        label = item.get("proposed_label") or item.get("review_label")
    category = item.get("category")
    if category is None:
        category = (
            item.get("proposed_category")
            or item.get("review_category")
            or item.get("false_positive_category")
        )

    return {
        "_index": index,
        "_fired_at": fired_at,
        "short_id": short_id,
        "id": str(item.get("id") or short_id),
        "venue": venue,
        "venue_code": venue,
        "market": market,
        "venue_market_id": market,
        "event_ticker": event_ticker,
        "rule": rule,
        "rule_key": str(item.get("rule_key") or rule),
        "outcome_key": item.get("outcome_key"),
        "fired_at": _format_fired_at(fired_at),
        "label": label,
        "category": category,
    }


def _item_event_ticker(
    item: Mapping[str, Any],
    venue: str,
    market: str,
) -> str | None:
    if venue.lower() != "kalshi":
        return None
    raw = item.get("event_ticker")
    if raw:
        return str(raw)
    facts = item.get("facts")
    if isinstance(facts, Mapping) and facts.get("event_ticker"):
        return str(facts["event_ticker"])
    return derive_event_ticker(market, venue)


def _parse_fired_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        raise ValueError("co-fire items require fired_at")
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _format_fired_at(value: datetime) -> str:
    return value.isoformat()


def _build_group(
    legs: list[dict[str, Any]],
    event_ticker: str | None,
) -> dict[str, Any]:
    public_legs = [_public_leg(leg) for leg in legs]
    return {
        "_sort_fired_at": legs[0]["_fired_at"],
        "_sort_index": min(int(leg["_index"]) for leg in legs),
        "event_ticker": event_ticker,
        "leg_count": len(legs),
        "is_cofire": len(legs) > 1,
        "started_at": _format_fired_at(legs[0]["_fired_at"]),
        "ended_at": _format_fired_at(legs[-1]["_fired_at"]),
        "legs": public_legs,
    }


def _public_leg(leg: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "short_id": leg["short_id"],
        "id": leg["id"],
        "venue": leg["venue"],
        "venue_code": leg["venue_code"],
        "market": leg["market"],
        "venue_market_id": leg["venue_market_id"],
        "event_ticker": leg["event_ticker"],
        "rule": leg["rule"],
        "rule_key": leg["rule_key"],
        "outcome_key": leg["outcome_key"],
        "fired_at": leg["fired_at"],
        "label": leg["label"],
        "category": leg["category"],
    }


def _public_group(group: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in group.items()
        if not str(key).startswith("_")
    }
