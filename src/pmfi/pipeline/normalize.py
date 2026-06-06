from __future__ import annotations
from pmfi.domain import RawEvent, NormalizedTrade
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError

def normalize_event(raw: RawEvent) -> NormalizedTrade | None:
    try:
        if raw.venue_code == "polymarket":
            return normalize_polymarket_fixture(raw)
        elif raw.venue_code == "kalshi":
            return normalize_kalshi_fixture(raw)
        return None
    except NormalizationError:
        return None
    except Exception:
        return None
