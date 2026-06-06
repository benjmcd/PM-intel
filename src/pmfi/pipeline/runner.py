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
        trade = normalize_event(raw)
        if trade is None:
            return
        market_id = await upsert_market(
            conn,
            venue_code=trade.venue_code,
            venue_market_id=trade.venue_market_id,
            title=trade.venue_market_id,
        )
        await insert_trade(conn, trade, raw_event_id=raw_event_id, market_id=market_id)
        decisions = engine.evaluate(trade)
        for decision in decisions:
            title = f"{decision.rule_id} on {trade.venue_market_id}"
            summary = f"{decision.severity} alert: capital={trade.capital_at_risk_usd}"
            await insert_alert(
                conn, decision,
                title=title, summary=summary,
                venue_code=trade.venue_code,
                market_id=market_id,
                outcome_key=trade.outcome_key,
            )
            await alert_callback(decision, trade.venue_code, market_id)

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
