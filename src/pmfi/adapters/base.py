from __future__ import annotations
from typing import AsyncIterator, Protocol, runtime_checkable
from pmfi.domain import RawEvent, VenueCode

@runtime_checkable
class VenueAdapter(Protocol):
    venue_code: VenueCode

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def events(self) -> AsyncIterator[RawEvent]: ...

    async def __aenter__(self) -> "VenueAdapter": ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...


class FixtureAdapter:
    """Replays fixture files as a VenueAdapter for testing/dev."""
    def __init__(self, events_list: list[RawEvent]):
        self.venue_code = events_list[0].venue_code if events_list else "polymarket"
        self._events = events_list

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def events(self) -> AsyncIterator[RawEvent]:
        for event in self._events:
            yield event

    async def __aenter__(self) -> "FixtureAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
