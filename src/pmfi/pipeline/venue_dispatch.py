from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from pmfi.adapters.base import VenueAdapter
from pmfi.domain import RawEvent
from pmfi.operational_health import guarded_source
from pmfi.pipeline.runner import run_adapter_pipeline
from pmfi.venue_registry import VenueAdapterParamsContext, get_venue

AlertSink = Callable[[Any, str, str | None], Awaitable[None]]
RulesReloader = Callable[[], None]
EventWrapper = Callable[[Any], Any]


@dataclasses.dataclass(frozen=True)
class VenueIngestTask:
    venue_code: str
    make_adapter: Callable[[], VenueAdapter]
    run_adapter: Callable[[VenueAdapter, Any], Awaitable[None]]
    subscription_count: int
    subscription_count_label: str


def resolve_venue_subscription_targets(
    live_venues: Sequence[str],
    watched: Sequence[Mapping[str, Any]],
    asset_id_map: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    targets_by_venue: dict[str, list[str]] = {}
    messages: list[str] = []
    for venue_code in live_venues:
        definition = get_venue(venue_code)
        if definition is None or definition.adapter_factory is None:
            messages.append(
                f"{venue_code} enabled but no registered ingest adapter is available; skipping it."
            )
            continue
        resolver = definition.subscription_resolver
        targets = list(resolver(watched, asset_id_map)) if resolver is not None else []
        if not targets:
            messages.append(
                definition.empty_subscription_message
                or f"{venue_code} enabled but no watched subscriptions resolved; skipping it."
            )
            continue
        targets_by_venue[venue_code] = targets
    return targets_by_venue, messages


def build_venue_ingest_tasks(
    *,
    live_venues: Sequence[str],
    subscription_targets_by_venue: Mapping[str, Sequence[str]],
    cfg: Any,
    pool_getter: Callable[[], Any],
    counted_events_for: Callable[[str], EventWrapper],
    operational_state: Any,
    intake_guards: Sequence[Any],
    shutdown: Any,
    engine: Any,
    alert_handler: AlertSink,
    suppression_window_seconds: int,
    capture_orderbook_enabled: bool,
    asset_id_map: Mapping[str, Mapping[str, Any]] | None,
    rules_reloader: RulesReloader | None,
    venue_options_by_venue: Mapping[str, Mapping[str, Any]] | None = None,
    run_pipeline: Callable[..., Awaitable[int]] = run_adapter_pipeline,
) -> tuple[VenueIngestTask, ...]:
    tasks: list[VenueIngestTask] = []
    options_by_venue = venue_options_by_venue or {}
    for venue_code in live_venues:
        definition = get_venue(venue_code)
        if definition is None or definition.adapter_factory is None:
            continue
        targets = subscription_targets_by_venue.get(venue_code, ())
        options = options_by_venue.get(venue_code, {})
        connection_recorder = (
            definition.connection_recorder_factory(pool_getter)
            if definition.connection_recorder_factory is not None
            else None
        )

        def make_adapter(
            *,
            _definition=definition,
            _targets=targets,
            _options=options,
            _connection_recorder=connection_recorder,
        ) -> VenueAdapter:
            params: dict[str, Any] = {}
            if _definition.adapter_params is not None:
                context = VenueAdapterParamsContext(
                    cfg=cfg,
                    subscription_targets=tuple(_targets),
                    options=_options,
                )
                params.update(dict(_definition.adapter_params(context)))
            if _connection_recorder is not None:
                params["connection_recorder"] = _connection_recorder
            return _definition.adapter_factory(**params)

        async def run_adapter(
            adapter: VenueAdapter,
            pool_manager: Any,
            *,
            _definition=definition,
            _venue_code=venue_code,
            _targets=targets,
        ) -> None:
            if _definition.cursor_loader is not None:
                async with pool_manager.pool.acquire() as cursor_conn:
                    cursors = await _definition.cursor_loader(cursor_conn, tuple(_targets))
                seed_cursors = getattr(adapter, "seed_cursors", None)
                if cursors and callable(seed_cursors):
                    seed_cursors(dict(cursors))
            source = counted_events_for(_venue_code)(adapter.events())
            guarded_events = guarded_source(
                source,
                state=operational_state,
                intake_guards=intake_guards,
                shutdown=shutdown,
            )
            cursor_recorder = (
                _definition.cursor_recorder_factory(pool_manager.pool)
                if _definition.cursor_recorder_factory is not None
                else None
            )
            await run_pipeline(
                guarded_events,
                pool_manager.pool,
                engine,
                alert_handler,
                suppression_window_seconds=suppression_window_seconds,
                capture_orderbook=bool(
                    capture_orderbook_enabled and _definition.captures_orderbook
                ),
                asset_id_map=asset_id_map if _definition.resolves_asset_ids else None,
                raise_on_connection_loss=True,
                rules_reloader=rules_reloader,
                cursor_recorder=cursor_recorder,
            )

        tasks.append(
            VenueIngestTask(
                venue_code=venue_code,
                make_adapter=make_adapter,
                run_adapter=run_adapter,
                subscription_count=len(targets),
                subscription_count_label=definition.subscription_count_label,
            )
        )
    return tuple(tasks)
