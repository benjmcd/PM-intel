from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from pmfi.domain import RawEvent
from pmfi.adapters.base import FixtureAdapter, VenueAdapter
from pmfi.adapters.kalshi import _parse_exchange_ts as kalshi_parse_ts
from pmfi.adapters.polymarket import _parse_exchange_ts as poly_parse_ts
from pmfi.fixtures import load_raw_event
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"

def _load_fixtures() -> list[RawEvent]:
    return [load_raw_event(p) for p in sorted(FIXTURE_DIR.glob("*.json"))]

# --- Kalshi _parse_exchange_ts ---

def test_kalshi_ts_iso_string():
    payload = {"created_time": "2024-06-01T12:00:00Z", "trade_id": "abc"}
    result = kalshi_parse_ts(payload)
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 2024 and result.month == 6 and result.day == 1
    assert result.hour == 12

def test_kalshi_ts_milliseconds_epoch():
    # 1_700_000_000_000 ms = 2023-11-14T22:13:20Z
    payload = {"ts": 1_700_000_000_000}
    result = kalshi_parse_ts(payload)
    assert result is not None
    assert result.year == 2023

def test_kalshi_ts_seconds_epoch():
    payload = {"timestamp": 1_700_000_000}
    result = kalshi_parse_ts(payload)
    assert result is not None
    assert result.year == 2023

def test_kalshi_ts_none_when_missing():
    assert kalshi_parse_ts({}) is None
    assert kalshi_parse_ts({"trade_id": "x"}) is None

def test_kalshi_ts_ignores_malformed():
    assert kalshi_parse_ts({"created_time": "not-a-date"}) is None

def test_kalshi_ts_naive_iso_gets_utc():
    payload = {"created_time": "2024-01-15T10:30:00"}
    result = kalshi_parse_ts(payload)
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

# --- Polymarket _parse_exchange_ts ---

def test_poly_ts_milliseconds():
    # Use a timestamp within the live-guard window (recent epoch in ms).
    # 1_748_000_000_000 ms = approx 2025-05-23, within 30d of today (2026-06-09).
    from datetime import timedelta
    recent_ms = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000)
    payload = {"timestamp": recent_ms}
    result = poly_parse_ts(payload)
    assert result is not None and result.tzinfo is not None

def test_poly_ts_none_when_missing():
    assert poly_parse_ts({}) is None


# --- FixtureAdapter ---

def test_fixture_adapter_yields_events():
    events = _load_fixtures()
    adapter = FixtureAdapter(events)

    async def _run():
        results = []
        async for ev in adapter.events():
            results.append(ev)
        return results

    result = asyncio.run(_run())
    assert len(result) == len(events)
    for ev in result:
        assert isinstance(ev, RawEvent)

def test_fixture_adapter_is_context_manager():
    events = _load_fixtures()

    async def _run():
        async with FixtureAdapter(events) as adapter:
            results = []
            async for ev in adapter.events():
                results.append(ev)
        return results

    result = asyncio.run(_run())
    assert len(result) >= 1
