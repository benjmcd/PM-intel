from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable, AsyncIterator
import asyncpg
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision
from pmfi.pipeline.normalize import normalize_event
from pmfi.pipeline.engine import AlertEngine
from pmfi.db.repos.markets import upsert_market
from pmfi.db.repos.raw_events import insert_raw_event
from pmfi.db.repos.trades import insert_trade
from pmfi.db.repos.alerts import insert_alert
from pmfi.db.repos.metrics import upsert_metric_window
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
) -> None:
    async with pool.acquire() as conn:
        raw_event_id = await insert_raw_event(conn, raw)
        logger.debug("raw_event stored id=%s venue=%s", raw_event_id, raw.venue_code)

        trade = normalize_event(raw)
        if trade is None:
            logger.debug("normalization returned None for venue=%s market=%s", raw.venue_code, raw.venue_market_id)
            return

        market_id = await upsert_market(
            conn,
            venue_code=trade.venue_code,
            venue_market_id=trade.venue_market_id,
            title=trade.venue_market_id,
        )
        trade_id = await insert_trade(conn, trade, raw_event_id=raw_event_id, market_id=market_id)
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
            alert_id = await insert_alert(
                conn, decision,
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
            )
            processed += 1
            if max_events and processed >= max_events:
                break
        except Exception as exc:
            logger.error("Pipeline error processing event: %s", exc)
    return processed
