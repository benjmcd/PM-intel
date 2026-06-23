from __future__ import annotations

import asyncio
from types import SimpleNamespace

from pmfi.domain import RawEvent
from pmfi.venue_registry import (
    VenueAdapterParamsContext,
    VenueDefinition,
    register_venue,
    unregister_venue,
)


class _StubAdapter:
    venue_code = "stub"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()

    async def events(self):
        if False:
            yield RawEvent(
                venue_code="stub",
                source_channel="stub",
                source_event_type="trade",
                source_event_id="stub-1",
                venue_market_id="STUB-1",
                payload={},
            )


def test_dispatch_wires_registered_stub_venue_without_cli_branch() -> None:
    from pmfi.pipeline.venue_dispatch import (
        build_venue_ingest_tasks,
        resolve_venue_subscription_targets,
    )

    built_kwargs: list[dict] = []

    def adapter_factory(**kwargs):
        built_kwargs.append(dict(kwargs))
        return _StubAdapter(**kwargs)

    def subscription_resolver(watched, asset_id_map):
        return [
            str(row["venue_market_id"])
            for row in watched
            if row["venue_code"] == "stub"
        ]

    def adapter_params(context: VenueAdapterParamsContext):
        return {
            "markets": list(context.subscription_targets),
            "timeout_seconds": context.cfg.ingestion.live_api_timeout_seconds,
            "fixture_flag": context.options["fixture_flag"],
        }

    register_venue(
        VenueDefinition(
            venue_code="stub",
            normalizer=lambda raw: None,
            adapter_factory=adapter_factory,
            adapter_params=adapter_params,
            subscription_resolver=subscription_resolver,
            empty_subscription_message="Stub venue enabled but no markets are watched.",
        )
    )
    try:
        watched = [
            {
                "market_id": "stub-market",
                "venue_code": "stub",
                "venue_market_id": "STUB-1",
                "title": "Stub market",
            }
        ]
        targets, messages = resolve_venue_subscription_targets(["stub"], watched, {})
        assert targets == {"stub": ["STUB-1"]}
        assert messages == []

        cfg = SimpleNamespace(
            ingestion=SimpleNamespace(live_api_timeout_seconds=3),
            alerts=SimpleNamespace(suppression_window_seconds=11),
        )
        pool_manager = SimpleNamespace(pool=object())
        specs = build_venue_ingest_tasks(
            live_venues=["stub"],
            subscription_targets_by_venue=targets,
            cfg=cfg,
            pool_getter=lambda: pool_manager.pool,
            counted_events_for=lambda venue: (lambda source: source),
            operational_state=object(),
            intake_guards=[],
            shutdown=asyncio.Event(),
            engine=object(),
            alert_handler=lambda decision, venue_code, market_id: None,
            suppression_window_seconds=cfg.alerts.suppression_window_seconds,
            capture_orderbook_enabled=True,
            asset_id_map={},
            rules_reloader=None,
            venue_options_by_venue={"stub": {"fixture_flag": True}},
        )

        assert [spec.venue_code for spec in specs] == ["stub"]
        assert specs[0].subscription_count == 1
        adapter = specs[0].make_adapter()
        assert isinstance(adapter, _StubAdapter)
        assert built_kwargs == [
            {
                "markets": ["STUB-1"],
                "timeout_seconds": 3,
                "fixture_flag": True,
            }
        ]
    finally:
        unregister_venue("stub")
