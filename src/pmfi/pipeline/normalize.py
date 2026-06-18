from __future__ import annotations
from pmfi.domain import RawEvent, NormalizedTrade
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError

# Event types from the Polymarket market channel that represent actual trades.
# Empty string is included for backward compatibility with existing fixtures.
_POLYMARKET_TRADE_EVENT_TYPES = frozenset({"last_trade_price", "trade", ""})


def normalize_event(raw: RawEvent) -> NormalizedTrade | None:
    """Return NormalizedTrade, or None for benign non-trade events.

    Raises NormalizationError for actual normalization failures so callers can
    write structured dead letters with actionable reason codes.
    """
    if raw.venue_code == "polymarket":
        if raw.source_event_type not in _POLYMARKET_TRADE_EVENT_TYPES:
            return None  # benign lifecycle event; no dead letter needed
        try:
            return normalize_polymarket_fixture(raw)
        except NormalizationError:
            raise
        except Exception as exc:
            raise NormalizationError(f"normalizer_exception: {exc}") from exc
    elif raw.venue_code == "kalshi":
        try:
            return normalize_kalshi_fixture(raw)
        except NormalizationError:
            raise
        except Exception as exc:
            raise NormalizationError(f"normalizer_exception: {exc}") from exc
    raise NormalizationError(f"unsupported venue: {raw.venue_code}")
