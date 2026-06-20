from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, AsyncIterator
import asyncpg
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision


class IngestConnectionLost(Exception):
    """Raised by run_adapter_pipeline when a DB connection failure requires restart.

    Signals the outer supervisor to close and recreate the pool when needed, then restart.
    Per-event data errors (NormalizationError, ValueError, etc.) do NOT trigger this.
    """

    def __init__(
        self,
        message: str = "",
        *,
        progress_observed: bool = False,
        progress_events: int = 0,
    ) -> None:
        super().__init__(message)
        self.progress_events = max(0, int(progress_events))
        self.progress_observed = bool(progress_observed or self.progress_events > 0)


class AdapterConnectionLost(Exception):
    """Raised when the venue stream itself disconnects or goes silent."""

    def __init__(
        self,
        message: str = "",
        *,
        progress_observed: bool = False,
        progress_events: int = 0,
    ) -> None:
        super().__init__(message)
        self.progress_events = max(0, int(progress_events))
        self.progress_observed = bool(progress_observed or self.progress_events > 0)


from pmfi.pipeline.normalize import normalize_event
from pmfi.normalization import NormalizationError
from pmfi.pipeline.engine import AlertEngine
from pmfi.db.repos.markets import upsert_market
from pmfi.db.repos.raw_events import insert_raw_event
from pmfi.db.repos.trades import insert_trade
from pmfi.db.repos.alerts import insert_alert
from pmfi.db.repos.metrics import upsert_metric_window
from pmfi.db.repos.dead_letters import insert_dead_letter
from pmfi.venue_registry import (
    DeadLetterRequest,
    VenuePreprocessContext,
    VenuePreprocessResult,
    get_venue,
    is_trade_event_type,
    resolve_polymarket_asset_outcome as resolve_asset_outcome,
)

logger = logging.getLogger(__name__)

AlertCallback = Callable[[AlertDecision, str, str | None], Awaitable[None]]

# Keyed by (venue_code, market_id_str, rule_id, outcome_key_or_empty)
_SuppressionCache = dict[tuple[str, str, str, str], datetime]


_DOMINANT_SIDE_ALERT_RULES = {"directional_cluster_v1", "momentum_v1"}


def _alert_outcome_key(decision: AlertDecision, trade: NormalizedTrade) -> str:
    """Return the outcome side the alert should be persisted and suppressed under."""
    evidence = decision.evidence or {}
    if decision.rule_id in _DOMINANT_SIDE_ALERT_RULES:
        dominant_side = str(evidence.get("dominant_side") or "").strip().lower()
        if dominant_side in {"yes", "no"}:
            return dominant_side

    evidence_outcome = str(evidence.get("outcome_key") or "").strip().lower()
    if evidence_outcome in {"yes", "no", "unknown"}:
        return evidence_outcome
    return trade.outcome_key


async def _write_dead_letter_request(
    conn: object,
    raw: RawEvent,
    raw_event_id: object,
    request: DeadLetterRequest,
) -> None:
    await insert_dead_letter(
        conn,
        venue_code=raw.venue_code,
        raw_event_id=raw_event_id,
        source_channel=raw.source_channel,
        failure_stage=request.failure_stage,
        error_class=request.error_class,
        error_message=request.error_message,
        payload=request.payload if request.payload is not None else raw.payload,
    )


def normalization_error_class(error_message: str) -> str:
    if "normalizer_exception" in error_message:
        return "normalizer_exception"
    if any(k in error_message for k in ("count", "contracts")):
        return "invalid_count"
    if any(k in error_message for k in ("price", "size")):
        return "invalid_price_or_size"
    if any(k in error_message for k in ("timestamp", "decimal", "invalid")):
        return "payload_schema_mismatch"
    return "normalization_error"


async def process_event(
    raw: RawEvent,
    pool: asyncpg.Pool,
    engine: AlertEngine,
    alert_handler: AlertCallback,
    *,
    suppression: _SuppressionCache | None = None,
    suppression_window_seconds: int = 300,
    capture_orderbook: bool = False,
    asset_id_map: dict | None = None,
) -> None:
    venue = get_venue(raw.venue_code)
    if venue and venue.preprocessor:
        preprocessed = venue.preprocessor(
            raw,
            VenuePreprocessContext(asset_id_map=asset_id_map),
        )
        raw = preprocessed.raw
    else:
        preprocessed = VenuePreprocessResult(raw=raw)

    async with pool.acquire() as conn:
        raw_event_id, is_duplicate = await insert_raw_event(conn, raw)
        if is_duplicate:
            logger.debug("duplicate event skipped id=%s", raw_event_id)
            return
        logger.debug("raw_event stored id=%s venue=%s", raw_event_id, raw.venue_code)

        for request in preprocessed.dead_letters:
            await _write_dead_letter_request(conn, raw, raw_event_id, request)
            logger.debug(
                "%s dead_letter venue=%s",
                request.error_class,
                raw.venue_code,
            )
        if preprocessed.halt:
            return

        try:
            trade = normalize_event(raw)
        except NormalizationError as _norm_err:
            _err_msg = str(_norm_err)
            _error_class = normalization_error_class(_err_msg)
            await insert_dead_letter(
                conn,
                venue_code=raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=raw.source_channel,
                failure_stage="normalization",
                error_class=_error_class,
                error_message=_err_msg,
                payload=raw.payload,
            )
            logger.debug("dead_letter written error_class=%s venue=%s", _error_class, raw.venue_code)
            return

        if trade is None:
            if is_trade_event_type(raw):
                await insert_dead_letter(
                    conn,
                    venue_code=raw.venue_code,
                    raw_event_id=raw_event_id,
                    source_channel=raw.source_channel,
                    failure_stage="normalization",
                    error_class="trade_normalization_failed",
                    error_message=(
                        "trade-type raw event produced no normalized_trade; "
                        "venue normalizer returned None"
                    ),
                    payload=raw.payload,
                )
                logger.debug(
                    "dead_letter written for trade-type None venue=%s event_type=%s",
                    raw.venue_code,
                    raw.source_event_type,
                )
                return
            # Benign non-trade event (lifecycle, subscription ack, etc.); skip silently.
            logger.debug("non-trade event skipped venue=%s event_type=%s", raw.venue_code, raw.source_event_type)
            return

        if venue and venue.post_normalize:
            for request in venue.post_normalize(raw, trade):
                await _write_dead_letter_request(conn, raw, raw_event_id, request)
                logger.debug(
                    "%s dead_letter venue=%s",
                    request.error_class,
                    raw.venue_code,
                )

        # Pass title=None so upsert_market will not overwrite an existing human-readable
        # title (set by market discovery) with the raw venue_market_id string.
        market_id = await upsert_market(
            conn,
            venue_code=trade.venue_code,
            venue_market_id=trade.venue_market_id,
            title=None,
        )
        trade_id = await insert_trade(conn, trade, raw_event_id=raw_event_id, market_id=market_id)
        if trade_id is None:
            logger.debug("duplicate trade skipped venue=%s venue_trade_id=%s", trade.venue_code, trade.venue_trade_id)
            return
        await upsert_metric_window(conn, trade, market_id=market_id, window_seconds=300)

        if capture_orderbook and venue and venue.orderbook_capture:
            try:
                await venue.orderbook_capture(conn, raw, raw_event_id, market_id)
            except Exception as ob_exc:
                logger.debug("orderbook capture non-fatal: %s", ob_exc)

        decisions = engine.evaluate(trade)
        logger.debug("engine.evaluate: %d decision(s) for market=%s", len(decisions), trade.venue_market_id)

        # Use event-time for suppression so replay suppression behaves consistently
        # with live (a 5-min suppression window is meaningful in event-time).
        # In live ingest event_ts is effectively now(), so live behaviour is unchanged in practice.
        event_now = trade.exchange_ts or trade.received_at
        for decision in decisions:
            if not decision.emit_alert:
                continue

            alert_outcome_key = _alert_outcome_key(decision, trade)

            if suppression is not None:
                key = (trade.venue_code, str(market_id), decision.rule_id, alert_outcome_key or "")
                last = suppression.get(key)
                if last is not None and (event_now - last).total_seconds() < suppression_window_seconds:
                    logger.debug(
                        "suppressed alert rule=%s market=%s outcome=%s (%.0fs since last fired, event-time)",
                        decision.rule_id, trade.venue_market_id, alert_outcome_key,
                        (event_now - last).total_seconds(),
                    )
                    continue
                suppression[key] = event_now

            title = f"{decision.rule_id} on {trade.venue_market_id}"
            summary = f"{decision.severity} alert: capital={trade.capital_at_risk_usd}"
            _event_ts = trade.exchange_ts or trade.received_at
            alert_id = await insert_alert(
                conn, decision,
                event_ts=_event_ts,
                title=title, summary=summary,
                venue_code=trade.venue_code,
                market_id=market_id,
                outcome_key=alert_outcome_key,
                raw_event_id=raw_event_id,
                trade_id=trade_id,
            )
            if alert_id:
                logger.info("alert inserted id=%s rule=%s severity=%s", alert_id, decision.rule_id, decision.severity)
                try:
                    await alert_handler(decision, trade.venue_code, market_id)
                except Exception as cb_exc:
                    logger.warning("alert_handler error (non-fatal): %s", cb_exc)


_DB_CONNECTION_ERROR_TYPES = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    ConnectionResetError,
)
_ADAPTER_CONNECTION_ERROR_TYPES = (
    OSError,
    asyncio.TimeoutError,
)
_CONNECTION_FAILURE_THRESHOLD = 5


async def run_adapter_pipeline(
    adapter_events: AsyncIterator[RawEvent],
    pool: asyncpg.Pool,
    engine: AlertEngine,
    alert_handler: AlertCallback,
    *,
    max_events: int | None = None,
    suppression_window_seconds: int = 300,
    capture_orderbook: bool = False,
    asset_id_map: dict | None = None,
    raise_on_connection_loss: bool = False,
) -> int:
    suppression: _SuppressionCache = {}
    # Seed suppression cache from DB to survive restarts
    try:
        async with pool.acquire() as _seed_conn:
            from pmfi.db.repos.alerts import load_suppression_cache
            suppression = await load_suppression_cache(
                _seed_conn, window_seconds=suppression_window_seconds
            )
            if suppression:
                logger.info("suppression cache seeded: %d entry(ies) from DB", len(suppression))
    except Exception as _seed_exc:
        logger.warning("suppression cache seed failed (continuing with empty cache): %s", _seed_exc)
    processed = 0
    failed = 0
    consecutive_conn_failures = 0
    iterator = adapter_events.__aiter__()
    while True:
        try:
            raw = await iterator.__anext__()
        except StopAsyncIteration:
            break
        except _ADAPTER_CONNECTION_ERROR_TYPES as conn_exc:
            failed += 1
            logger.error("Pipeline adapter connection error: %s", conn_exc)
            if raise_on_connection_loss:
                raise AdapterConnectionLost(
                    f"Adapter connection lost while reading events: {conn_exc}",
                    progress_events=processed,
                ) from conn_exc
            break
        try:
            await process_event(
                raw, pool, engine, alert_handler,
                suppression=suppression,
                suppression_window_seconds=suppression_window_seconds,
                capture_orderbook=capture_orderbook,
                asset_id_map=asset_id_map,
            )
            processed += 1
            consecutive_conn_failures = 0  # reset on success
            if max_events and processed >= max_events:
                break
        except _DB_CONNECTION_ERROR_TYPES as conn_exc:
            consecutive_conn_failures += 1
            logger.error(
                "Pipeline DB connection error (%d/%d consecutive): %s",
                consecutive_conn_failures, _CONNECTION_FAILURE_THRESHOLD, conn_exc,
            )
            failed += 1
            if raise_on_connection_loss and consecutive_conn_failures >= _CONNECTION_FAILURE_THRESHOLD:
                raise IngestConnectionLost(
                    f"DB connection lost after {consecutive_conn_failures} consecutive failures: {conn_exc}",
                    progress_events=processed,
                ) from conn_exc
        except Exception as exc:
            logger.error("Pipeline error processing event: %s", exc)
            consecutive_conn_failures = 0  # data error, not a connection error
            failed += 1
    if failed:
        logger.warning("run_adapter_pipeline: %d event(s) failed during processing (see errors above)", failed)
    return processed
