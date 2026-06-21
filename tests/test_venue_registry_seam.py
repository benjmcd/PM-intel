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


def test_process_event_routes_polymarket_orderbook_capture_through_registry():
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="last_trade_price",
        source_event_id="poly-book-trade-1",
        venue_market_id="condition-book-1",
        payload={
            "asset_id": "token-book-1",
            "market": "condition-book-1",
            "outcome": "yes",
            "price": "0.50",
            "size": "100",
            "side": "BUY",
            "trade_id": "poly-book-trade-1",
        },
    )
    raw_book = {
        "bids": [{"price": "0.49", "size": "10"}],
        "asks": [{"price": "0.51", "size": "20"}],
    }
    main_conn = AsyncMock()
    orderbook_conn = AsyncMock()
    main_acquire = MagicMock()
    main_acquire.__aenter__ = AsyncMock(return_value=main_conn)
    main_acquire.__aexit__ = AsyncMock(return_value=False)
    orderbook_acquire = MagicMock()
    orderbook_acquire.__aenter__ = AsyncMock(return_value=orderbook_conn)
    orderbook_acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.side_effect = [main_acquire, orderbook_acquire]
    engine = AlertEngine(
        rules_config={
            "rules": {
                "large_trade_absolute_v1": {"enabled": False},
                "market_relative_large_trade_v1": {"enabled": False},
                "open_interest_shock_v1": {"enabled": False},
                "directional_cluster_v1": {"enabled": False},
                "momentum_v1": {"enabled": False},
                "volume_spike_v1": {"enabled": False},
            }
        }
    )

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-poly-1", False))),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="market-poly-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="trade-poly-1")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.orderbook.fetch_polymarket_book", new=AsyncMock(return_value=raw_book)) as fetch_book,
        patch("pmfi.db.repos.orderbook.insert_orderbook_snapshot", new=AsyncMock()) as insert_snapshot,
    ):
        asyncio.run(
            process_event(
                raw,
                pool,
                engine,
                AsyncMock(),
                capture_orderbook=True,
                suppression=None,
            )
        )

    fetch_book.assert_awaited_once_with("token-book-1")
    insert_snapshot.assert_awaited_once()
    args, kwargs = insert_snapshot.await_args
    assert pool.acquire.call_count == 2
    assert args == (orderbook_conn,)
    assert kwargs["venue_code"] == "polymarket"
    assert kwargs["market_id"] == "market-poly-1"
    assert kwargs["raw_event_id"] == "raw-poly-1"
    assert kwargs["payload"] == raw_book
    assert kwargs["is_reconstructed"] is True
    assert kwargs["best_bid"] == Decimal("0.49")
    assert kwargs["best_ask"] == Decimal("0.51")
    assert kwargs["spread"] == Decimal("0.02")
    assert kwargs["bids"] == [{"price": Decimal("0.49"), "size": Decimal("10")}]
    assert kwargs["asks"] == [{"price": Decimal("0.51"), "size": Decimal("20")}]


async def _collect_events(adapter: _StubVenueAdapter) -> list[RawEvent]:
    async with adapter:
        return [event async for event in adapter.events()]
