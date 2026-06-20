from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from pmfi.domain import RawEvent
from pmfi.normalization import make_trade
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.normalize import normalize_event
from pmfi.pipeline.runner import process_event
from pmfi.venue_registry import VenueDefinition, get_venue, register_venue, unregister_venue


class _StubVenueAdapter:
    venue_code = "stub"

    def __init__(self, events: list[RawEvent]) -> None:
        self._events = events

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def events(self) -> AsyncIterator[RawEvent]:
        for event in self._events:
            yield event

    async def __aenter__(self) -> "_StubVenueAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.disconnect()


def _stub_raw_event() -> RawEvent:
    return RawEvent(
        venue_code="stub",  # type: ignore[arg-type]
        source_channel="fixture",
        source_event_type="trade",
        source_event_id="stub-trade-1",
        venue_market_id="stub-market-1",
        payload={
            "market": "stub-market-1",
            "outcome": "yes",
            "price": "0.50",
            "contracts": "100000",
            "trade_id": "stub-trade-1",
        },
    )


def _normalize_stub(raw: RawEvent):
    return make_trade(
        raw=raw,
        venue_market_id=str(raw.payload["market"]),
        venue_trade_id=str(raw.payload["trade_id"]),
        outcome_key=str(raw.payload["outcome"]),
        price=Decimal(str(raw.payload["price"])),
        contracts=Decimal(str(raw.payload["contracts"])),
        directional_side="yes",
        aggressor_side="buy",
        side_confidence="medium",
    )


def _make_pool(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def test_stub_venue_registry_flows_normalize_process_event_alert():
    raw = _stub_raw_event()
    register_venue(
        VenueDefinition(
            venue_code="stub",
            adapter_factory=lambda: _StubVenueAdapter([raw]),
            normalizer=_normalize_stub,
        ),
        replace=True,
    )
    try:
        venue = get_venue("stub")
        assert venue is not None
        assert venue.adapter_factory is not None
        adapter = venue.adapter_factory()
        events = asyncio.run(_collect_events(adapter))
        assert events == [raw]

        trade = normalize_event(events[0])
        assert trade is not None
        assert trade.venue_code == "stub"
        assert trade.venue_market_id == "stub-market-1"

        conn = AsyncMock()
        pool = _make_pool(conn)
        handler = AsyncMock()
        engine = AlertEngine(
            rules_config={
                "rules": {
                    "large_trade_absolute_v1": {"enabled": True},
                    "market_relative_large_trade_v1": {"enabled": False},
                    "open_interest_shock_v1": {"enabled": False},
                    "directional_cluster_v1": {"enabled": False},
                    "momentum_v1": {"enabled": False},
                    "volume_spike_v1": {"enabled": False},
                }
            }
        )

        with (
            patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-stub-1", False))),
            patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="market-stub-1")),
            patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-stub-1")),
            patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
            patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="alert-stub-1")) as insert_alert,
        ):
            asyncio.run(process_event(events[0], pool, engine, handler, suppression=None))

        assert insert_alert.await_count == 1
        handler.assert_awaited_once()
    finally:
        unregister_venue("stub")


async def _collect_events(adapter: _StubVenueAdapter) -> list[RawEvent]:
    async with adapter:
        return [event async for event in adapter.events()]
