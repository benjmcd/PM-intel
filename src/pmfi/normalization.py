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


def parse_optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


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
    open_interest_contracts: Decimal | None = None,
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
        open_interest_contracts=open_interest_contracts,
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
    outcome = str(p.get("outcome", "yes")).lower()
    # buy YES or sell NO = net bullish; sell YES or buy NO = net bearish
    if side == "buy" and outcome == "yes":
        direction = "yes"
    elif side == "sell" and outcome == "no":
        direction = "yes"
    elif side == "sell" and outcome == "yes":
        direction = "no"
    elif side == "buy" and outcome == "no":
        direction = "no"
    else:
        direction = "unknown"
    oi = parse_optional_decimal(p.get("open_interest"))
    return make_trade(
        raw=raw,
        venue_market_id=str(p.get("market", raw.venue_market_id or "unknown")),
        venue_trade_id=str(p.get("trade_id")) if p.get("trade_id") is not None else None,
        outcome_key=outcome if outcome in {"yes", "no"} else "yes",
        price=price,
        contracts=contracts,
        directional_side=direction,
        aggressor_side=side if side in {"buy", "sell"} else "unknown",
        side_confidence="medium" if side in {"buy", "sell"} and direction != "unknown" else "low",
        open_interest_contracts=oi,
    )


def normalize_kalshi_fixture(raw: RawEvent) -> NormalizedTrade:
    """Normalize Kalshi trade payloads.

    Handles both decimal (0.37) and integer-cent (37) price formats from Kalshi WS.
    """
    p = raw.payload
    contracts = parse_decimal(p.get("count", p.get("contracts")), "count")
    taker_side = str(p.get("taker_side", "unknown")).lower()

    # Determine directional side before extracting price so we can pick the
    # correct yes_price vs no_price when both fields are present (live WS format).
    # Live Kalshi WS uses taker_side as "yes"/"no" (direction, not buy/sell).
    # Fixture format uses yes_no + taker_side as "buy"/"sell".
    yes_no_raw = p.get("yes_no") or p.get("side")
    if yes_no_raw is None and taker_side in {"yes", "no"}:
        yes_no = taker_side
        aggressor = "unknown"
        confidence = "medium"
    else:
        yes_no = str(yes_no_raw or "unknown").lower()
        aggressor = taker_side if taker_side in {"buy", "sell"} else "unknown"
        confidence = "medium" if aggressor != "unknown" and yes_no in {"yes", "no"} else "low"

    # Price: prefer explicit "price" field; when absent pick yes_price or no_price
    # according to the taker's side so capital_at_risk reflects the taker's cost.
    if p.get("price") is not None:
        price = parse_decimal(p["price"], "price")
    elif yes_no == "yes" and p.get("yes_price") is not None:
        price = parse_decimal(p["yes_price"], "price")
    elif yes_no == "no" and p.get("no_price") is not None:
        price = parse_decimal(p["no_price"], "price")
    else:
        price = parse_decimal(p.get("yes_price", p.get("no_price")), "price")
    if price > 1:
        # Kalshi WS sends price in integer cents (0-100); convert to decimal fraction.
        price = price / Decimal("100")

    oi = parse_optional_decimal(p.get("open_interest"))
    return make_trade(
        raw=raw,
        venue_market_id=str(p.get("ticker", p.get("market_ticker", raw.venue_market_id or "unknown"))),
        venue_trade_id=str(p.get("trade_id")) if p.get("trade_id") is not None else None,
        outcome_key=yes_no if yes_no in {"yes", "no"} else "yes",
        price=price,
        contracts=contracts,
        directional_side=yes_no if yes_no in {"yes", "no"} else "unknown",
        aggressor_side=aggressor,
        side_confidence=confidence,
        open_interest_contracts=oi,
        warnings=() if yes_no in {"yes", "no"} else ("directional side unverified",),
    )
