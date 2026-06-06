from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, AsyncIterator
import asyncpg
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision
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

# Keyed by (venue_code, market_id_str, rule_id)
_SuppressionCache = dict[tuple[str, str, str], datetime]


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
    # Resolve to venue_market_id and inject outcome_key before normalization.
    _missing_asset_id: str | None = None
    if asset_id_map and raw.venue_market_id is None:
        import dataclasses as _dc
        _asset_id = raw.payload.get("asset_id")
        if _asset_id:
            _info = asset_id_map.get(str(_asset_id))
            if _info:
                new_payload = {**raw.payload, "outcome": _info["outcome_key"]}
                raw = _dc.replace(raw, venue_market_id=_info["venue_market_id"], payload=new_payload)
            else:
                _missing_asset_id = str(_asset_id)

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

        market_id = await upsert_market(
            conn,
            venue_code=trade.venue_code,
            venue_market_id=trade.venue_market_id,
            title=trade.venue_market_id,
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
                except Exception as ob_exc:
                    logger.debug("orderbook capture non-fatal: %s", ob_exc)

        decisions = engine.evaluate(trade)
        logger.debug("engine.evaluate: %d decision(s) for market=%s", len(decisions), trade.venue_market_id)

        now = datetime.now(timezone.utc)
        for decision in decisions:
            if not decision.emit_alert:
                continue

            if suppression is not None:
                key = (trade.venue_code, str(market_id), decision.rule_id)
                last = suppression.get(key)
                if last is not None and (now - last).total_seconds() < suppression_window_seconds:
                    logger.debug(
                        "suppressed alert rule=%s market=%s (%.0fs since last fired)",
                        decision.rule_id, trade.venue_market_id,
                        (now - last).total_seconds(),
                    )
                    continue
                suppression[key] = now

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
            )
            if alert_id:
                logger.info("alert inserted id=%s rule=%s severity=%s", alert_id, decision.rule_id, decision.severity)
            try:
                await alert_handler(decision, trade.venue_code, market_id)
            except Exception as cb_exc:
                logger.warning("alert_handler error (non-fatal): %s", cb_exc)


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
) -> int:
    suppression: _SuppressionCache = {}
    processed = 0
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
            if max_events and processed >= max_events:
                break
        except Exception as exc:
            logger.error("Pipeline error processing event: %s", exc)
    return processed
