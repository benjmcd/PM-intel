from __future__ import annotations
import asyncio
import logging
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

logger = logging.getLogger(__name__)

AlertCallback = Callable[[AlertDecision, str, str | None], Awaitable[None]]

async def process_event(
    raw: RawEvent,
    pool: asyncpg.Pool,
    engine: AlertEngine,
    alert_callback: AlertCallback,
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
        await insert_trade(conn, trade, raw_event_id=raw_event_id, market_id=market_id)
        await upsert_metric_window(conn, trade, market_id=market_id, window_seconds=300)

        decisions = engine.evaluate(trade)
        logger.debug("engine.evaluate: %d decision(s) for market=%s", len(decisions), trade.venue_market_id)

        for decision in decisions:
            if not decision.emit_alert:
                continue
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
                await alert_callback(decision, trade.venue_code, market_id)
            except Exception as cb_exc:
                logger.warning("alert_callback error (non-fatal): %s", cb_exc)

async def run_adapter_pipeline(
    adapter_events: AsyncIterator[RawEvent],
    pool: asyncpg.Pool,
    engine: AlertEngine,
    alert_callback: AlertCallback,
    *,
    max_events: int | None = None,
) -> int:
    processed = 0
    async for raw in adapter_events:
        try:
            await process_event(raw, pool, engine, alert_callback)
            processed += 1
            if max_events and processed >= max_events:
                break
        except Exception as exc:
            logger.error("Pipeline error processing event: %s", exc)
    return processed
