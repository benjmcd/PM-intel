from __future__ import annotations
from pmfi.domain import RawEvent, NormalizedTrade
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError

# Event types from the Polymarket market channel that represent actual trades.
# Empty string is included for backward compatibility with existing fixtures.
_POLYMARKET_TRADE_EVENT_TYPES = frozenset({"last_trade_price", "trade", ""})


def normalize_event(raw: RawEvent) -> NormalizedTrade | None:
    try:
        if raw.venue_code == "polymarket":
            if raw.source_event_type not in _POLYMARKET_TRADE_EVENT_TYPES:
                return None  # non-trade event; retained raw
            return normalize_polymarket_fixture(raw)
        elif raw.venue_code == "kalshi":
            return normalize_kalshi_fixture(raw)
        return None
    except NormalizationError:
        return None
    except Exception:
        return None
