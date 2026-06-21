"""Core domain objects.

These classes are intentionally small. They define the first stable contracts Codex should build upon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

VenueCode = Literal["polymarket", "kalshi"]
Side = Literal["buy", "sell", "unknown"]
DirectionalSide = Literal["yes", "no", "unknown"]
Confidence = Literal["low", "medium", "high", "unknown"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RawEvent:
    venue_code: VenueCode
    source_channel: str
    source_event_type: str
    payload: dict[str, Any]
    source_event_id: str | None = None
    venue_market_id: str | None = None
    exchange_ts: datetime | None = None
    received_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class NormalizedTrade:
    venue_code: VenueCode
    venue_market_id: str
    outcome_key: str
    price: Decimal
    contracts: Decimal
    capital_at_risk_usd: Decimal
    payout_notional_usd: Decimal
    fee_usd: Decimal | None = None
    directional_side: DirectionalSide = "unknown"
    aggressor_side: Side = "unknown"
    side_confidence: Confidence = "unknown"
    venue_trade_id: str | None = None
    exchange_ts: datetime | None = None
    open_interest_contracts: Decimal | None = None
    received_at: datetime = field(default_factory=utc_now)
    warnings: tuple[str, ...] = ()
    source_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertDecision:
    emit_alert: bool
    rule_id: str
    rule_version: str
    severity: str
    confidence: Confidence
    score: Decimal
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    data_quality: str = "unverified"
