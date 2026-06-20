from __future__ import annotations
from pmfi.domain import RawEvent, NormalizedTrade
from pmfi.normalization import NormalizationError
from pmfi.venue_registry import get_venue


def normalize_event(raw: RawEvent) -> NormalizedTrade | None:
    """Return NormalizedTrade, or None for benign non-trade events.

    Raises NormalizationError for actual normalization failures so callers can
    write structured dead letters with actionable reason codes.
    """
    venue = get_venue(raw.venue_code)
    if venue is None:
        return None  # unsupported venue; no dead letter
    try:
        return venue.normalizer(raw)
    except NormalizationError:
        raise
    except Exception as exc:
        raise NormalizationError(f"normalizer_exception: {exc}") from exc
