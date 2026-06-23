"""Venue registry for normalization and venue-specific ingest seams."""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from pmfi.adapters.base import VenueAdapter
from pmfi.domain import NormalizedTrade, RawEvent
from pmfi.normalization import normalize_kalshi_fixture, normalize_polymarket_fixture

logger = logging.getLogger(__name__)

Normalizer = Callable[[RawEvent], NormalizedTrade | None]
AdapterFactory = Callable[..., VenueAdapter]
DiscoveryHandler = Callable[..., Any]
Preprocessor = Callable[[RawEvent, "VenuePreprocessContext"], "VenuePreprocessResult"]
PostNormalizeHook = Callable[[RawEvent, NormalizedTrade], tuple["DeadLetterRequest", ...]]
OrderbookCapture = Callable[[Any, RawEvent, object, object], Awaitable[None]]
SubscriptionResolver = Callable[
    [Sequence[Mapping[str, Any]], Mapping[str, Mapping[str, Any]]],
    list[str],
]
AdapterParams = Callable[["VenueAdapterParamsContext"], Mapping[str, Any]]
RuntimeOptions = Callable[["VenueRuntimeOptionsContext"], Mapping[str, Any]]
ConnectionRecorderFactory = Callable[[Callable[[], Any]], Any]
CursorRecorderFactory = Callable[[Any], Callable[[RawEvent], Awaitable[None]]]
CursorLoader = Callable[[Any, Sequence[str]], Awaitable[Mapping[str, str]]]


@dataclasses.dataclass(frozen=True)
class DeadLetterRequest:
    failure_stage: str
    error_class: str
    error_message: str
    payload: Mapping[str, Any] | None = None


@dataclasses.dataclass(frozen=True)
class VenuePreprocessContext:
    asset_id_map: Mapping[str, Mapping[str, Any]] | None = None


@dataclasses.dataclass(frozen=True)
class VenuePreprocessResult:
    raw: RawEvent
    dead_letters: tuple[DeadLetterRequest, ...] = ()
    halt: bool = False


@dataclasses.dataclass(frozen=True)
class VenueAdapterParamsContext:
    cfg: Any
    subscription_targets: tuple[str, ...]
    options: Mapping[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class VenueRuntimeOptionsContext:
    cfg: Any
    args: Any


@dataclasses.dataclass(frozen=True)
class VenueDefinition:
    venue_code: str
    normalizer: Normalizer
    adapter_factory: AdapterFactory | None = None
    adapter_params: AdapterParams | None = None
    runtime_options: RuntimeOptions | None = None
    subscription_resolver: SubscriptionResolver | None = None
    empty_subscription_message: str | None = None
    enable_flag: str | None = None
    connection_recorder_factory: ConnectionRecorderFactory | None = None
    cursor_loader: CursorLoader | None = None
    cursor_recorder_factory: CursorRecorderFactory | None = None
    captures_orderbook: bool = False
    resolves_asset_ids: bool = False
    subscription_count_label: str = "subscriptions"
    preprocessor: Preprocessor | None = None
    post_normalize: PostNormalizeHook | None = None
    orderbook_capture: OrderbookCapture | None = None
    discovery: DiscoveryHandler | None = None
    trade_event_types: frozenset[str] = frozenset()


_REGISTRY: dict[str, VenueDefinition] = {}


def register_venue(definition: VenueDefinition, *, replace: bool = False) -> None:
    if not definition.venue_code:
        raise ValueError("venue_code is required")
    if definition.venue_code in _REGISTRY and not replace:
        raise ValueError(f"venue already registered: {definition.venue_code}")
    _REGISTRY[definition.venue_code] = definition


def unregister_venue(venue_code: str) -> None:
    _REGISTRY.pop(venue_code, None)


def get_venue(venue_code: str) -> VenueDefinition | None:
    return _REGISTRY.get(venue_code)


def registered_venues() -> tuple[str, ...]:
    return tuple(_REGISTRY)


_POLYMARKET_TRADE_EVENT_TYPES = frozenset({"last_trade_price", "trade", ""})
_KALSHI_TRADE_EVENT_TYPES = frozenset({"trade"})


def is_trade_event_type(raw: RawEvent) -> bool:
    venue = get_venue(raw.venue_code)
    if venue is None:
        return False
    return raw.source_event_type in venue.trade_event_types


def normalize_polymarket_event(raw: RawEvent) -> NormalizedTrade | None:
    if raw.source_event_type not in _POLYMARKET_TRADE_EVENT_TYPES:
        return None
    return normalize_polymarket_fixture(raw)


def normalize_kalshi_event(raw: RawEvent) -> NormalizedTrade | None:
    return normalize_kalshi_fixture(raw)


def _outcome_is_missing(outcome: object) -> bool:
    if outcome is None:
        return True
    s = str(outcome).strip()
    return s == "" or s.lower() == "unknown"


def resolve_polymarket_asset_outcome(
    raw: RawEvent,
    asset_id_map: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[RawEvent, str | None]:
    if not asset_id_map:
        return raw, None
    if raw.venue_code != "polymarket":
        return raw, None

    asset_id = raw.payload.get("asset_id")
    if not asset_id:
        return raw, None

    existing_outcome = raw.payload.get("outcome")
    if not _outcome_is_missing(existing_outcome):
        return raw, None

    info = asset_id_map.get(str(asset_id))
    if info is None:
        return raw, str(asset_id)

    is_binary = info.get("is_binary", True)
    patch: dict[str, Any] = {}

    if is_binary:
        patch["outcome"] = info["outcome_key"]

    new_vmid = raw.venue_market_id or info["venue_market_id"]
    if raw.venue_market_id is None:
        patch["market"] = info["venue_market_id"]

    new_payload = {**raw.payload, **patch}
    return dataclasses.replace(raw, venue_market_id=new_vmid, payload=new_payload), None


def preprocess_polymarket_event(
    raw: RawEvent,
    context: VenuePreprocessContext,
) -> VenuePreprocessResult:
    if (
        not context.asset_id_map
        and raw.payload.get("asset_id")
        and not raw.payload.get("market")
        and raw.venue_market_id is None
    ):
        asset_id = raw.payload.get("asset_id")
        return VenuePreprocessResult(
            raw=raw,
            dead_letters=(
                DeadLetterRequest(
                    failure_stage="normalization",
                    error_class="asset_map_not_loaded",
                    error_message=(
                        f"asset_id={asset_id!r} cannot be resolved: asset_id_map not loaded; "
                        "run 'pmfi markets discover' then 'pmfi markets watch' before ingesting"
                    ),
                ),
            ),
            halt=True,
        )

    resolved, missing_asset_id = resolve_polymarket_asset_outcome(raw, context.asset_id_map)
    if missing_asset_id:
        return VenuePreprocessResult(
            raw=resolved,
            dead_letters=(
                DeadLetterRequest(
                    failure_stage="normalization",
                    error_class="missing_asset_mapping",
                    error_message=(
                        f"asset_id={missing_asset_id!r} not in local mapping; "
                        "run 'pmfi markets discover' and 'pmfi markets watch'"
                    ),
                ),
            ),
            halt=True,
        )
    return VenuePreprocessResult(raw=resolved)


def polymarket_post_normalize_dead_letters(
    raw: RawEvent,
    trade: NormalizedTrade,
) -> tuple[DeadLetterRequest, ...]:
    if trade.outcome_key != "unknown" or not raw.payload.get("asset_id"):
        return ()
    asset_id = raw.payload.get("asset_id")
    return (
        DeadLetterRequest(
            failure_stage="normalization",
            error_class="multi_outcome_unsupported",
            error_message=(
                f"asset_id={asset_id!r} is a non-binary (multi-outcome) token; "
                "outcome_key stored as 'unknown'. Per-market suppression may not work until "
                "resolved. Run 'pmfi markets discover' for full outcome mapping."
            ),
        ),
    )


async def capture_polymarket_orderbook(
    pool: Any,
    raw: RawEvent,
    raw_event_id: object,
    market_id: object,
) -> None:
    from pmfi.db.repos.orderbook import insert_orderbook_snapshot
    from pmfi.orderbook import (
        _extract_token_id,
        compute_book_summary,
        fetch_polymarket_book,
        parse_book_levels,
    )

    token_id = _extract_token_id(raw.payload)
    if not token_id:
        return
    try:
        raw_book = await fetch_polymarket_book(token_id)
        if raw_book is None:
            return
        bids, asks = parse_book_levels(raw_book)
        summary = compute_book_summary(bids, asks)
        async with pool.acquire() as conn:
            await insert_orderbook_snapshot(
                conn,
                venue_code=raw.venue_code,
                market_id=market_id,
                raw_event_id=raw_event_id,
                bids=bids,
                asks=asks,
                is_reconstructed=True,
                payload=raw_book,
                **summary,
            )
    except Exception as ob_exc:
        logger.debug("orderbook capture non-fatal: %s", ob_exc)


def _polymarket_adapter_factory(**kwargs: Any) -> VenueAdapter:
    from pmfi.adapters.polymarket import PolymarketAdapter

    return PolymarketAdapter(**kwargs)


def _kalshi_adapter_factory(**kwargs: Any) -> VenueAdapter:
    from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter

    return KalshiRestPollingAdapter(**kwargs)


def _polymarket_subscription_targets(
    watched: Sequence[Mapping[str, Any]],
    asset_id_map: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    watched_poly_market_ids = {
        row["market_id"]
        for row in watched
        if row.get("venue_code") == "polymarket"
    }
    return [
        token_id
        for token_id, info in asset_id_map.items()
        if info["venue_code"] == "polymarket" and info["market_id"] in watched_poly_market_ids
    ]


def _kalshi_subscription_targets(
    watched: Sequence[Mapping[str, Any]],
    asset_id_map: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    return [
        str(row["venue_market_id"])
        for row in watched
        if row.get("venue_code") == "kalshi"
    ]


def _polymarket_adapter_params(context: VenueAdapterParamsContext) -> Mapping[str, Any]:
    return {
        "asset_ids": list(context.subscription_targets),
        "timeout_seconds": context.cfg.ingestion.live_api_timeout_seconds,
        "initial_backoff": context.cfg.ingestion.reconnect_initial_backoff,
        "max_backoff": context.cfg.ingestion.reconnect_max_backoff,
        "reconnect_jitter": context.cfg.ingestion.reconnect_jitter,
        "subscription_timeout_seconds": (
            context.cfg.ingestion.polymarket_subscription_timeout_seconds
        ),
        "receive_timeout_seconds": context.cfg.ingestion.polymarket_receive_timeout_seconds,
    }


def _kalshi_adapter_params(context: VenueAdapterParamsContext) -> Mapping[str, Any]:
    return {
        "tickers": list(context.subscription_targets),
        "poll_interval_seconds": context.options["poll_interval_seconds"],
        "limit": context.options["limit"],
        "max_pages": context.options["max_pages"],
        "all_market_poll": context.options["all_market_poll"],
        "timeout_seconds": context.cfg.ingestion.live_api_timeout_seconds,
        "initial_backoff": context.cfg.ingestion.reconnect_initial_backoff,
        "max_backoff": context.cfg.ingestion.reconnect_max_backoff,
        "reconnect_jitter": context.cfg.ingestion.reconnect_jitter,
    }


def _kalshi_runtime_options(context: VenueRuntimeOptionsContext) -> Mapping[str, Any]:
    poll_interval_seconds = getattr(context.args, "kalshi_poll_interval_seconds", None)
    if poll_interval_seconds is None:
        poll_interval_seconds = context.cfg.ingestion.kalshi_poll_interval_seconds
    trade_poll_limit = getattr(context.args, "kalshi_trade_poll_limit", None)
    if trade_poll_limit is None:
        trade_poll_limit = context.cfg.ingestion.kalshi_trade_poll_limit
    trade_poll_max_pages = getattr(context.args, "kalshi_trade_poll_max_pages", None)
    if trade_poll_max_pages is None:
        trade_poll_max_pages = context.cfg.ingestion.kalshi_trade_poll_max_pages
    return {
        "all_market_poll": getattr(context.args, "kalshi_all_market_poll", False),
        "poll_interval_seconds": poll_interval_seconds,
        "limit": trade_poll_limit,
        "max_pages": trade_poll_max_pages,
    }


def _polymarket_connection_recorder_factory(pool_getter: Callable[[], Any]) -> Any:
    from pmfi.pipeline.connection_tracking import PooledIngestionConnectionRecorder

    return PooledIngestionConnectionRecorder(pool_getter)


def _kalshi_cursor_recorder_factory(pool: Any) -> Callable[[RawEvent], Awaitable[None]]:
    from pmfi.db.repos.feed_cursors import record_kalshi_rest_trade_cursor

    return lambda raw: record_kalshi_rest_trade_cursor(pool, raw)


async def _load_kalshi_rest_cursors(conn: Any, targets: Sequence[str]) -> Mapping[str, str]:
    from pmfi.db.repos.feed_cursors import load_kalshi_rest_trade_cursors

    return await load_kalshi_rest_trade_cursors(conn, list(targets))


register_venue(
    VenueDefinition(
        venue_code="polymarket",
        adapter_factory=_polymarket_adapter_factory,
        adapter_params=_polymarket_adapter_params,
        subscription_resolver=_polymarket_subscription_targets,
        empty_subscription_message=(
            "Polymarket enabled but no token IDs resolved for watched markets; "
            "skipping it. Run 'pmfi markets discover --venue polymarket' then "
            "'pmfi markets watch <market_id>'."
        ),
        enable_flag="enable_polymarket_live",
        connection_recorder_factory=_polymarket_connection_recorder_factory,
        captures_orderbook=True,
        resolves_asset_ids=True,
        subscription_count_label="poly_tokens",
        normalizer=normalize_polymarket_event,
        preprocessor=preprocess_polymarket_event,
        post_normalize=polymarket_post_normalize_dead_letters,
        orderbook_capture=capture_polymarket_orderbook,
        trade_event_types=_POLYMARKET_TRADE_EVENT_TYPES,
    )
)
register_venue(
    VenueDefinition(
        venue_code="kalshi",
        adapter_factory=_kalshi_adapter_factory,
        adapter_params=_kalshi_adapter_params,
        runtime_options=_kalshi_runtime_options,
        subscription_resolver=_kalshi_subscription_targets,
        empty_subscription_message=(
            "Kalshi enabled but no tickers among watched markets; skipping it. "
            "Run 'pmfi markets discover --venue kalshi' then "
            "'pmfi markets watch <market_id> --venue kalshi'."
        ),
        enable_flag="enable_kalshi_live",
        cursor_loader=_load_kalshi_rest_cursors,
        cursor_recorder_factory=_kalshi_cursor_recorder_factory,
        subscription_count_label="kalshi_tickers",
        normalizer=normalize_kalshi_event,
        trade_event_types=_KALSHI_TRADE_EVENT_TYPES,
    )
)
