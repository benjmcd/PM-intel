from __future__ import annotations
import asyncio
from pmfi.domain import RawEvent
from pmfi.adapters.base import FixtureAdapter, VenueAdapter
from pmfi.fixtures import load_raw_event
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"

def _load_fixtures() -> list[RawEvent]:
    return [load_raw_event(p) for p in sorted(FIXTURE_DIR.glob("*.json"))]

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
