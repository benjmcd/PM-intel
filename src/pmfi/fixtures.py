"""Fixture loading utilities."""

from __future__ import annotations

import json
from pathlib import Path

from pmfi.domain import RawEvent
from pmfi.normalization import parse_ts


def load_raw_event(path: str | Path) -> RawEvent:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return RawEvent(
        venue_code=data["venue_code"],
        source_channel=data["source_channel"],
        source_event_type=data["source_event_type"],
        source_event_id=data.get("source_event_id"),
        venue_market_id=data.get("venue_market_id"),
        exchange_ts=parse_ts(data.get("exchange_ts")),
        payload=data["payload"],
    )
