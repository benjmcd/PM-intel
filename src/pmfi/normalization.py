"""Fixture-oriented normalization helpers.

Live venue normalizers should grow from these small, tested contracts rather than replacing them with opaque parser logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pmfi.domain import NormalizedTrade, RawEvent

logger = logging.getLogger(__name__)

# Sanity bounds for live-parsed timestamps.
_TS_FUTURE_LIMIT = timedelta(hours=1)
_TS_PAST_LIMIT = timedelta(days=30)


class NormalizationError(ValueError):
    """Raised when a payload cannot be normalized safely."""


def parse_decimal(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise NormalizationError(f"invalid decimal for {field_name}: {value!r}") from exc
    return result


def _check_ts_sanity(parsed: datetime, raw_value: Any) -> datetime | None:
    """Return parsed if it is within sane live bounds, else log a warning and return None."""
    now = datetime.now(timezone.utc)
    if parsed > now + _TS_FUTURE_LIMIT:
        logger.warning(
            "parse_ts: timestamp %r is >1h in the future (parsed=%s); returning None",
            raw_value, parsed.isoformat(),
        )
        return None
    if parsed < now - _TS_PAST_LIMIT:
        logger.warning(
            "parse_ts: timestamp %r is >30d in the past (parsed=%s); returning None",
            raw_value, parsed.isoformat(),
        )
        return None
    return parsed


def parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Treat large integer timestamps as milliseconds; small ones as seconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return _check_ts_sanity(parsed, value)
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise NormalizationError(f"invalid timestamp: {value!r}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        return _check_ts_sanity(parsed, value)
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


def validate_integral_count(contracts: Decimal, field_name: str) -> None:
    if contracts != contracts.to_integral_value():
        raise NormalizationError(f"invalid count for {field_name}: expected integer contracts, got {contracts}")


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
    outcome_raw = p.get("outcome")
    outcome = str(outcome_raw).lower() if outcome_raw is not None else "unknown"
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
        venue_trade_id=str(p["trade_id"]) if p.get("trade_id") is not None else (str(p["id"]) if p.get("id") is not None else None),
        outcome_key=outcome if outcome in {"yes", "no"} else "unknown",
        price=price,
        contracts=contracts,
        directional_side=direction,
        aggressor_side=side if side in {"buy", "sell"} else "unknown",
        side_confidence="medium" if side in {"buy", "sell"} and direction != "unknown" else "low",
        open_interest_contracts=oi,
    )


def normalize_kalshi_fixture(raw: RawEvent) -> NormalizedTrade:
    """Normalize Kalshi trade payloads.

    Handles both:
    - Real REST API shape: count_fp (string decimal), yes_price_dollars/no_price_dollars (string, already in [0,1])
    - Legacy/WS shape: count (int), yes_price/no_price (integer cents), or explicit price field
    """
    p = raw.payload
    # count_fp (real REST, string decimal) > count (legacy int/str) > contracts
    contracts = parse_decimal(p.get("count_fp", p.get("count", p.get("contracts"))), "count")
    validate_integral_count(contracts, "count")
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

    # Price extraction — three tiers, most-specific first:
    # 1. Explicit "price" field (legacy fixture format, already [0,1] or cent-converted below)
    # 2. yes_price_dollars / no_price_dollars (real REST shape, string dollars already in [0,1] — NO /100)
    # 3. yes_price / no_price (legacy integer cents — apply >1 -> /100 conversion)
    is_cents = False
    if p.get("price") is not None:
        price = parse_decimal(p["price"], "price")
        # Legacy explicit price may also be integer cents
        is_cents = price > 1
    elif yes_no == "yes" and p.get("yes_price_dollars") is not None:
        price = parse_decimal(p["yes_price_dollars"], "price")
    elif yes_no == "no" and p.get("no_price_dollars") is not None:
        price = parse_decimal(p["no_price_dollars"], "price")
    elif p.get("yes_price_dollars") is not None:
        price = parse_decimal(p["yes_price_dollars"], "price")
    elif p.get("no_price_dollars") is not None:
        price = parse_decimal(p["no_price_dollars"], "price")
    elif yes_no == "yes" and p.get("yes_price") is not None:
        price = parse_decimal(p["yes_price"], "price")
        is_cents = True
    elif yes_no == "no" and p.get("no_price") is not None:
        price = parse_decimal(p["no_price"], "price")
        is_cents = True
    else:
        raw_val = p.get("yes_price", p.get("no_price"))
        price = parse_decimal(raw_val, "price")
        is_cents = True
    if is_cents and price > 1:
        # Kalshi legacy: integer cents (0-100) -> decimal fraction
        price = price / Decimal("100")

    oi = parse_optional_decimal(p.get("open_interest"))
    return make_trade(
        raw=raw,
        venue_market_id=str(p.get("ticker", p.get("market_ticker", raw.venue_market_id or "unknown"))),
        venue_trade_id=str(p["trade_id"]) if p.get("trade_id") is not None else (str(p["id"]) if p.get("id") is not None else None),
        outcome_key=yes_no if yes_no in {"yes", "no"} else "unknown",
        price=price,
        contracts=contracts,
        directional_side=yes_no if yes_no in {"yes", "no"} else "unknown",
        aggressor_side=aggressor,
        side_confidence=confidence,
        open_interest_contracts=oi,
        warnings=() if yes_no in {"yes", "no"} else ("directional side unverified",),
    )
