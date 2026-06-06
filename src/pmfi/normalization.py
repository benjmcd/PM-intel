"""Fixture-oriented normalization helpers.

Live venue normalizers should grow from these small, tested contracts rather than replacing them with opaque parser logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pmfi.domain import NormalizedTrade, RawEvent


class NormalizationError(ValueError):
    """Raised when a payload cannot be normalized safely."""


def parse_decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise NormalizationError(f"invalid decimal for {field_name}: {value!r}") from exc
    return result


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Treat large integer timestamps as milliseconds; small ones as seconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise NormalizationError(f"invalid timestamp: {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise NormalizationError(f"invalid timestamp type: {type(value).__name__}")


def validate_price(price: Decimal) -> None:
    if price < 0 or price > 1:
        raise NormalizationError(f"price must be between 0 and 1, got {price}")


def make_trade(
    *,
    raw: RawEvent,
    venue_market_id: str,
    outcome_key: str,
    price: Decimal,
    contracts: Decimal,
    venue_trade_id: str | None = None,
    directional_side: str = "unknown",
    aggressor_side: str = "unknown",
    side_confidence: str = "unknown",
    warnings: tuple[str, ...] = (),
) -> NormalizedTrade:
    validate_price(price)
    if contracts < 0:
        raise NormalizationError(f"contracts must be nonnegative, got {contracts}")
    return NormalizedTrade(
        venue_code=raw.venue_code,
        venue_market_id=venue_market_id,
        venue_trade_id=venue_trade_id,
        outcome_key=outcome_key,
        price=price,
        contracts=contracts,
        capital_at_risk_usd=price * contracts,
        payout_notional_usd=contracts,
        directional_side=directional_side,  # type: ignore[arg-type]
        aggressor_side=aggressor_side,  # type: ignore[arg-type]
        side_confidence=side_confidence,  # type: ignore[arg-type]
        exchange_ts=raw.exchange_ts,
        received_at=raw.received_at,
        warnings=warnings,
        source_payload=raw.payload,
    )


def normalize_polymarket_fixture(raw: RawEvent) -> NormalizedTrade:
    """Normalize the local Polymarket fixture shape.

    This is not a claim that every live Polymarket payload has this shape.
    Codex must validate live shapes before broadening the normalizer.
    """
    p = raw.payload
    price = parse_decimal(p.get("price"), "price")
    contracts = parse_decimal(p.get("size", p.get("contracts")), "size")
    side = str(p.get("side", "unknown")).lower()
    direction = "yes" if side == "buy" else "unknown"
    return make_trade(
        raw=raw,
        venue_market_id=str(p.get("market", raw.venue_market_id or "unknown")),
        venue_trade_id=str(p.get("trade_id")) if p.get("trade_id") is not None else None,
        outcome_key=str(p.get("outcome", "yes")).lower(),
        price=price,
        contracts=contracts,
        directional_side=direction,
        aggressor_side=side if side in {"buy", "sell"} else "unknown",
        side_confidence="medium" if side in {"buy", "sell"} else "unknown",
    )


def normalize_kalshi_fixture(raw: RawEvent) -> NormalizedTrade:
    """Normalize the local Kalshi fixture shape.

    The fixture uses decimal price. Live Kalshi payloads may use cents depending on endpoint; verify before mapping.
    """
    p = raw.payload
    price = parse_decimal(p.get("price"), "price")
    contracts = parse_decimal(p.get("count", p.get("contracts")), "count")
    taker_side = str(p.get("taker_side", "unknown")).lower()
    yes_no = str(p.get("yes_no", p.get("side", "unknown"))).lower()
    return make_trade(
        raw=raw,
        venue_market_id=str(p.get("ticker", raw.venue_market_id or "unknown")),
        venue_trade_id=str(p.get("trade_id")) if p.get("trade_id") is not None else None,
        outcome_key=yes_no if yes_no in {"yes", "no"} else "yes",
        price=price,
        contracts=contracts,
        directional_side=yes_no if yes_no in {"yes", "no"} else "unknown",
        aggressor_side=taker_side if taker_side in {"buy", "sell"} else "unknown",
        side_confidence="medium" if taker_side in {"buy", "sell"} and yes_no in {"yes", "no"} else "low",
        warnings=() if yes_no in {"yes", "no"} else ("directional side unverified",),
    )
