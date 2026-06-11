from __future__ import annotations
import asyncio
import dataclasses
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, AsyncIterator
import asyncpg
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision


class IngestConnectionLost(Exception):
    """Raised by run_adapter_pipeline when consecutive DB connection failures exceed threshold.

    Signals the outer supervisor to close and recreate the pool, then restart.
    Per-event data errors (NormalizationError, ValueError, etc.) do NOT trigger this.
    """


from pmfi.pipeline.normalize import normalize_event
from pmfi.normalization import NormalizationError
from pmfi.pipeline.engine import AlertEngine
from pmfi.db.repos.markets import upsert_market
from pmfi.db.repos.raw_events import insert_raw_event
from pmfi.db.repos.trades import insert_trade
from pmfi.db.repos.alerts import insert_alert
from pmfi.db.repos.metrics import upsert_metric_window
from pmfi.db.repos.dead_letters import insert_dead_letter
from pmfi.orderbook import _extract_token_id, fetch_polymarket_book, parse_book_levels, compute_book_summary
from pmfi.db.repos.orderbook import insert_orderbook_snapshot

logger = logging.getLogger(__name__)

AlertCallback = Callable[[AlertDecision, str, str | None], Awaitable[None]]

# Keyed by (venue_code, market_id_str, rule_id, outcome_key_or_empty)
_SuppressionCache = dict[tuple[str, str, str, str], datetime]


def _outcome_is_missing(outcome: object) -> bool:
    """Return True when an outcome value is absent or semantically unknown."""
    if outcome is None:
        return True
    s = str(outcome).strip()
    return s == "" or s.lower() == "unknown"


def resolve_asset_outcome(
    raw: RawEvent,
    asset_id_map: dict | None,
) -> tuple[RawEvent, str | None]:
    """Resolve a Polymarket asset_id to venue_market_id + outcome_key.

    Applies the mapping only when ALL of:
      - asset_id_map is truthy
      - raw.venue_code == "polymarket"
      - raw.payload contains a non-empty "asset_id"
      - the existing outcome is missing or unknown

    Returns (possibly-updated RawEvent, missing_asset_id-or-None).
    missing_asset_id is set when asset_id is present but not found in the map
    AND the outcome is still unknown — caller should write a dead-letter.
    No-clobber: venue_market_id already set is preserved; only fills it when None.

    Binary tokens: outcome_key injected from map ("yes"/"no").
    Non-binary tokens: outcome left absent; normalizer yields outcome_key="unknown",
    flagged degraded downstream.
    """
    if not asset_id_map:
        return raw, None
    if raw.venue_code != "polymarket":
        return raw, None

    asset_id = raw.payload.get("asset_id")
    if not asset_id:
        return raw, None

    existing_outcome = raw.payload.get("outcome")
    if not _outcome_is_missing(existing_outcome):
        # Outcome is already valid — trust first-party venue data, do not re-map.
        return raw, None

    info = asset_id_map.get(str(asset_id))
    if info is None:
        # asset_id present but not in map AND outcome unknown → dead-letter candidate
        return raw, str(asset_id)

    is_binary = info.get("is_binary", True)
    patch: dict = {}

    if is_binary:
        # Binary token: inject the correct yes/no outcome_key from the map
        patch["outcome"] = info["outcome_key"]
    # Non-binary: leave outcome absent -> normalizer yields outcome_key="unknown", flagged degraded downstream.

    # No-clobber: only fill venue_market_id when it was None
    new_vmid = raw.venue_market_id or info["venue_market_id"]
    if raw.venue_market_id is None:
        patch["market"] = info["venue_market_id"]

    new_payload = {**raw.payload, **patch}
    raw = dataclasses.replace(raw, venue_market_id=new_vmid, payload=new_payload)

    return raw, None


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
    # Polymarket live events carry asset_id (token ID) not market (condition ID).
    # Guard: if the map is absent/empty and this Polymarket event has an asset_id
    # but no pre-resolved 'market' field, we cannot identify the market — emit a
    # dead_letter and skip storage rather than collapsing under 'unknown'.
    if (
        raw.venue_code == "polymarket"
        and not asset_id_map
        and raw.payload.get("asset_id")
        and not raw.payload.get("market")
        and raw.venue_market_id is None
    ):
        async with pool.acquire() as conn:
            raw_event_id, is_duplicate = await insert_raw_event(conn, raw)
            if is_duplicate:
                logger.debug("duplicate event skipped id=%s", raw_event_id)
                return
            _asset_id = raw.payload.get("asset_id")
            await insert_dead_letter(
                conn,
                venue_code=raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=raw.source_channel,
                failure_stage="normalization",
                error_class="asset_map_not_loaded",
                error_message=(
                    f"asset_id={_asset_id!r} cannot be resolved: asset_id_map not loaded; "
                    "run 'pmfi markets discover' then 'pmfi markets watch' before ingesting"
                ),
                payload=raw.payload,
            )
            logger.debug("asset_map_not_loaded dead_letter venue=%s asset_id=%s", raw.venue_code, _asset_id)
        return

    # Resolve to venue_market_id and inject outcome_key before normalization.
    raw, _missing_asset_id = resolve_asset_outcome(raw, asset_id_map)

    async with pool.acquire() as conn:
        raw_event_id, is_duplicate = await insert_raw_event(conn, raw)
        if is_duplicate:
            logger.debug("duplicate event skipped id=%s", raw_event_id)
            return
        logger.debug("raw_event stored id=%s venue=%s", raw_event_id, raw.venue_code)

        if _missing_asset_id:
            await insert_dead_letter(
                conn,
                venue_code=raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=raw.source_channel,
                failure_stage="normalization",
                error_class="missing_asset_mapping",
                error_message=f"asset_id={_missing_asset_id!r} not in local mapping; run 'pmfi markets discover' and 'pmfi markets watch'",
                payload=raw.payload,
            )
            logger.debug("missing_asset_mapping dead_letter venue=%s asset_id=%s", raw.venue_code, _missing_asset_id)
            return

        try:
            trade = normalize_event(raw)
        except NormalizationError as _norm_err:
            _err_msg = str(_norm_err)
            if "normalizer_exception" in _err_msg:
                _error_class = "normalizer_exception"
            elif any(k in _err_msg for k in ("price", "size", "count", "contracts")):
                _error_class = "invalid_price_or_size"
            elif any(k in _err_msg for k in ("timestamp", "decimal", "invalid")):
                _error_class = "payload_schema_mismatch"
            else:
                _error_class = "normalization_error"
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
            # Benign non-trade event (lifecycle, subscription ack, etc.) — skip silently
            logger.debug("non-trade event skipped venue=%s event_type=%s", raw.venue_code, raw.source_event_type)
            return

        # Non-binary token: trade carries valid price/contracts so we store it,
        # but emit a dead_letter so the operator can see it via `pmfi dead-letters`.
        if trade.outcome_key == "unknown" and raw.venue_code == "polymarket" and raw.payload.get("asset_id"):
            await insert_dead_letter(
                conn,
                venue_code=raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=raw.source_channel,
                failure_stage="normalization",
                error_class="multi_outcome_unsupported",
                error_message=(
                    f"asset_id={raw.payload.get('asset_id')!r} is a non-binary (multi-outcome) token; "
                    "outcome_key stored as 'unknown'. Per-market suppression may not work until "
                    "resolved. Run 'pmfi markets discover' for full outcome mapping."
                ),
                payload=raw.payload,
            )
            logger.debug("multi_outcome_unsupported dead_letter venue=%s asset_id=%s", raw.venue_code, raw.payload.get("asset_id"))

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

        if capture_orderbook and raw.venue_code == "polymarket":
            token_id = _extract_token_id(raw.payload)
            if token_id:
                try:
                    raw_book = await fetch_polymarket_book(token_id)
                    if raw_book is not None:
                        bids, asks = parse_book_levels(raw_book)
                        summary = compute_book_summary(bids, asks)
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
                        # Liquidity wall/vacuum assessment on the captured snapshot.
                        # Opt-in path; any error is caught by the surrounding handler.
                        from pmfi.pipeline.liquidity import assess_liquidity, build_liquidity_decision
                        _liq_cfg = getattr(engine, "_rules", {}).get("rules", {}).get("liquidity_wall_v1", {})
                        if bool(_liq_cfg.get("enabled", True)):
                            from decimal import Decimal as _D
                            _min_spread = _liq_cfg.get("min_spread")
                            _finding = assess_liquidity(
                                bids, asks,
                                min_wall_usd=_D(str(_liq_cfg.get("min_wall_usd", 25000))),
                                min_spread=_D(str(_min_spread)) if _min_spread is not None else None,
                                levels=int(_liq_cfg.get("levels", 3)),
                            )
                            if _finding is not None:
                                _liq_decision = build_liquidity_decision(_finding, outcome_key=trade.outcome_key)
                                _liq_ts = trade.exchange_ts or trade.received_at
                                _liq_alert_id = await insert_alert(
                                    conn, _liq_decision,
                                    event_ts=_liq_ts,
                                    title=f"liquidity_{_finding['kind']} on {trade.venue_market_id}",
                                    summary=f"{_liq_decision.severity}: {_finding['wall_side']} wall {_finding['wall_usd']} USD",
                                    venue_code=trade.venue_code, market_id=market_id,
                                    outcome_key=trade.outcome_key, raw_event_id=raw_event_id, trade_id=trade_id,
                                )
                                if _liq_alert_id:
                                    await alert_handler(_liq_decision, trade.venue_code, market_id)
                except Exception as ob_exc:
                    logger.debug("orderbook capture non-fatal: %s", ob_exc)

        decisions = engine.evaluate(trade)
        logger.debug("engine.evaluate: %d decision(s) for market=%s", len(decisions), trade.venue_market_id)

        # Use event-time for suppression so replay suppression behaves consistently
        # with live (a 5-min suppression window is meaningful in event-time).
        # In live ingest event_ts ≈ now(), so live behaviour is unchanged in practice.
        event_now = trade.exchange_ts or trade.received_at
        for decision in decisions:
            if not decision.emit_alert:
                continue

            if suppression is not None:
                key = (trade.venue_code, str(market_id), decision.rule_id, trade.outcome_key or "")
                last = suppression.get(key)
                if last is not None and (event_now - last).total_seconds() < suppression_window_seconds:
                    logger.debug(
                        "suppressed alert rule=%s market=%s outcome=%s (%.0fs since last fired, event-time)",
                        decision.rule_id, trade.venue_market_id, trade.outcome_key,
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
                outcome_key=trade.outcome_key,
                raw_event_id=raw_event_id,
                trade_id=trade_id,
            )
            if alert_id:
                logger.info("alert inserted id=%s rule=%s severity=%s", alert_id, decision.rule_id, decision.severity)
            try:
                await alert_handler(decision, trade.venue_code, market_id)
            except Exception as cb_exc:
                logger.warning("alert_handler error (non-fatal): %s", cb_exc)


_CONNECTION_ERROR_TYPES = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    ConnectionResetError,
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
    async for raw in adapter_events:
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
        except _CONNECTION_ERROR_TYPES as conn_exc:
            consecutive_conn_failures += 1
            logger.error(
                "Pipeline DB connection error (%d/%d consecutive): %s",
                consecutive_conn_failures, _CONNECTION_FAILURE_THRESHOLD, conn_exc,
            )
            failed += 1
            if raise_on_connection_loss and consecutive_conn_failures >= _CONNECTION_FAILURE_THRESHOLD:
                raise IngestConnectionLost(
                    f"DB connection lost after {consecutive_conn_failures} consecutive failures: {conn_exc}"
                ) from conn_exc
        except Exception as exc:
            logger.error("Pipeline error processing event: %s", exc)
            consecutive_conn_failures = 0  # data error, not a connection error
            failed += 1
    if failed:
        logger.warning("run_adapter_pipeline: %d event(s) failed during processing (see errors above)", failed)
    return processed
