from __future__ import annotations

import asyncio
import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pmfi.domain import RawEvent
from pmfi.venue_registry import (
    VenueAdapterParamsContext,
    VenueDefinition,
    register_venue,
    resolve_polymarket_asset_outcome,
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


def _register_stub_venue(adapter_factory, *, adapter_params=None) -> None:
    def subscription_resolver(watched, asset_id_map):
        return [
            str(row["venue_market_id"])
            for row in watched
            if row["venue_code"] == "stub"
        ]

    register_venue(
        VenueDefinition(
            venue_code="stub",
            normalizer=lambda raw: None,
            adapter_factory=adapter_factory,
            adapter_params=adapter_params,
            subscription_resolver=subscription_resolver,
            empty_subscription_message="Stub venue enabled but no markets are watched.",
            enable_flag="enable_stub_live",
        )
    )


def test_polymarket_asset_map_fills_market_when_outcome_is_already_present() -> None:
    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws",
        source_event_type="last_trade_price",
        source_event_id="asset-known-outcome",
        venue_market_id=None,
        payload={
            "asset_id": "token-yes",
            "outcome": "yes",
            "price": "0.50",
            "size": "10",
        },
    )
    resolved, missing = resolve_polymarket_asset_outcome(
        raw,
        {
            "token-yes": {
                "venue_market_id": "condition-123",
                "outcome_key": "yes",
                "is_binary": True,
            }
        },
    )

    assert missing is None
    assert resolved.payload["outcome"] == "yes"
    assert resolved.payload["market"] == "condition-123"
    assert resolved.venue_market_id == "condition-123"


def test_enabled_live_venues_preserves_builtin_order() -> None:
    from pmfi.pipeline.venue_dispatch import enabled_live_venues

    cfg = SimpleNamespace(
        features=SimpleNamespace(
            enable_polymarket_live=True,
            enable_kalshi_live=True,
        )
    )

    assert enabled_live_venues(cfg) == ["polymarket", "kalshi"]


def test_dispatch_wires_registered_stub_venue_without_cli_branch() -> None:
    from pmfi.pipeline.venue_dispatch import (
        build_venue_ingest_tasks,
        resolve_venue_subscription_targets,
    )

    built_kwargs: list[dict] = []

    def adapter_factory(**kwargs):
        built_kwargs.append(dict(kwargs))
        return _StubAdapter(**kwargs)

    def adapter_params(context: VenueAdapterParamsContext):
        return {
            "markets": list(context.subscription_targets),
            "timeout_seconds": context.cfg.ingestion.live_api_timeout_seconds,
            "fixture_flag": context.options["fixture_flag"],
        }

    _register_stub_venue(adapter_factory, adapter_params=adapter_params)
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


def test_refresh_subscriptions_includes_registered_stub_venue() -> None:
    from pmfi.commands._shared import _refresh_subscriptions

    _register_stub_venue(lambda **kwargs: _StubAdapter(**kwargs))
    try:
        watched = [
            {
                "market_id": "stub-market",
                "venue_code": "stub",
                "venue_market_id": "STUB-1",
                "title": "Stub market",
            }
        ]
        asset_id_map: dict = {}
        pool = SimpleNamespace(acquire=lambda: _AcquireContext(object()))

        async def _run():
            with (
                patch("pmfi.db.repos.markets.fetch_watched_markets", new=AsyncMock(return_value=watched)),
                patch("pmfi.markets.load_asset_id_mapping", new=AsyncMock(return_value={})),
            ):
                return await _refresh_subscriptions(pool, asset_id_map)

        targets_by_venue = asyncio.run(_run())
        assert targets_by_venue["stub"] == ["STUB-1"]
    finally:
        unregister_venue("stub")


def test_dry_run_uses_registered_stub_venue_without_cli_branch(capsys) -> None:
    from pmfi.cli import cmd_ingest

    built_kwargs: list[dict] = []

    class _DryStubAdapter(_StubAdapter):
        async def events(self):
            yield SimpleNamespace(payload={"stub": True})

    def adapter_factory(**kwargs):
        built_kwargs.append(dict(kwargs))
        return _DryStubAdapter(**kwargs)

    def adapter_params(context: VenueAdapterParamsContext):
        return {
            "markets": list(context.subscription_targets),
            "timeout_seconds": context.cfg.ingestion.live_api_timeout_seconds,
        }

    _register_stub_venue(adapter_factory, adapter_params=adapter_params)
    try:
        cfg = SimpleNamespace(
            database=SimpleNamespace(url="postgresql://fake/db"),
            features=SimpleNamespace(
                enable_polymarket_live=False,
                enable_kalshi_live=False,
                enable_stub_live=True,
            ),
            ingestion=SimpleNamespace(
                reconnect_initial_backoff=1,
                reconnect_max_backoff=60,
                reconnect_jitter=False,
                live_api_timeout_seconds=7,
                kalshi_poll_interval_seconds=5,
                kalshi_trade_poll_limit=200,
                kalshi_trade_poll_max_pages=1,
            ),
            alerts=SimpleNamespace(suppression_window_seconds=30),
        )
        watched = [
            {
                "market_id": "stub-market",
                "venue_code": "stub",
                "venue_market_id": "STUB-1",
                "title": "Stub market",
            }
        ]
        args = argparse.Namespace(
            venue=[],
            dry_run=True,
            max_events=1,
            max_seconds=0,
            log_file=None,
            kalshi_all_market_poll=False,
            kalshi_poll_interval_seconds=None,
            kalshi_trade_poll_limit=None,
            kalshi_trade_poll_max_pages=None,
        )

        with (
            patch("pmfi.config.load_config", return_value=cfg),
            patch(
                "pmfi.db.create_pool",
                new=AsyncMock(return_value=SimpleNamespace(acquire=lambda: _AcquireContext(object()))),
            ),
            patch("pmfi.db.close_pool", new=AsyncMock()),
            patch("pmfi.db.repos.markets.fetch_watched_markets", new=AsyncMock(return_value=watched)),
            patch("pmfi.markets.load_asset_id_mapping", new=AsyncMock(return_value={})),
            patch(
                "pmfi.pipeline.normalize.normalize_event",
                return_value=SimpleNamespace(
                    venue_market_id="STUB-1",
                    price=1,
                    directional_side="yes",
                ),
            ),
        ):
            rc = cmd_ingest(args)

        assert rc == 0
        assert built_kwargs == [{"markets": ["STUB-1"], "timeout_seconds": 7}]
        output = capsys.readouterr().out
        assert "[dry:stub]" in output
        assert "started 1 adapter(s)" in output
    finally:
        unregister_venue("stub")


class _AcquireContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False
