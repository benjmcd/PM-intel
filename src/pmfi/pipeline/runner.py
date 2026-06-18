from __future__ import annotations
import asyncio
import dataclasses
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


@dataclasses.dataclass
class EventOutcome:
    raw_inserted: bool = False
    raw_duplicate: bool = False
    normalized_trade_inserted: bool = False
    duplicate_trade: bool = False
    non_trade_skipped: bool = False
    dead_letter_inserted: bool = False
    alerts_inserted: int = 0
    alerts_delivered: int = 0
    alerts_suppressed: int = 0


@dataclasses.dataclass
class PipelineStats:
    raw_events_seen: int = 0
    raw_events_inserted: int = 0
    raw_event_duplicates: int = 0
    normalized_trades_inserted: int = 0
    duplicate_trades: int = 0
    non_trade_skips: int = 0
    dead_letters_inserted: int = 0
    alerts_inserted: int = 0
    alerts_delivered: int = 0
    alerts_suppressed: int = 0
    processing_errors: int = 0

    def record(self, outcome: EventOutcome) -> None:
        if outcome.raw_inserted:
            self.raw_events_inserted += 1
        if outcome.raw_duplicate:
            self.raw_event_duplicates += 1
        if outcome.normalized_trade_inserted:
            self.normalized_trades_inserted += 1
        if outcome.duplicate_trade:
            self.duplicate_trades += 1
        if outcome.non_trade_skipped:
            self.non_trade_skips += 1
        if outcome.dead_letter_inserted:
            self.dead_letters_inserted += 1
        self.alerts_inserted += outcome.alerts_inserted
        self.alerts_delivered += outcome.alerts_delivered
        self.alerts_suppressed += outcome.alerts_suppressed


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _with_lineage_evidence(
    decision: AlertDecision,
    *,
    raw: RawEvent,
    raw_event_id: int | str,
    trade: NormalizedTrade,
    trade_id: str,
    market_id: str,
) -> AlertDecision:
    """Attach traceability fields before alert persistence/delivery."""
    evidence = dict(decision.evidence)
    evidence["lineage"] = {
        "raw_event_id": str(raw_event_id),
        "raw_event_received_at": _iso_or_none(raw.received_at),
        "source_channel": raw.source_channel,
        "source_event_type": raw.source_event_type,
        "source_event_id": raw.source_event_id,
        "venue_trade_id": trade.venue_trade_id,
        "market_id": str(market_id),
        "trade_id": str(trade_id),
        "trade_received_at": _iso_or_none(trade.received_at),
        "exchange_ts": _iso_or_none(trade.exchange_ts),
        "normalization_version": "trade.v1",
    }
    return dataclasses.replace(decision, evidence=evidence)


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
) -> EventOutcome:
    outcome = EventOutcome()
    orderbook_snapshot: dict | None = None
    # Polymarket live events carry asset_id (token ID) not market (condition ID).
    # Resolve to venue_market_id and inject outcome_key before normalization.
    original_raw = raw
    normalization_raw = raw
    _missing_asset_id: str | None = None
    if asset_id_map is not None and raw.venue_market_id is None:
        _asset_id = raw.payload.get("asset_id")
        if _asset_id:
            _info = asset_id_map.get(str(_asset_id))
            if _info:
                new_payload = {**raw.payload, "outcome": _info["outcome_key"], "market": _info["venue_market_id"]}
                normalization_raw = dataclasses.replace(
                    raw,
                    venue_market_id=_info["venue_market_id"],
                    payload=new_payload,
                )
            else:
                _missing_asset_id = str(_asset_id)

    async with pool.acquire() as conn:
        raw_event_id, is_duplicate = await insert_raw_event(conn, original_raw)
        if is_duplicate:
            outcome.raw_duplicate = True
            logger.debug("duplicate event skipped id=%s", raw_event_id)
            return outcome
        outcome.raw_inserted = True
        logger.debug("raw_event stored id=%s venue=%s", raw_event_id, original_raw.venue_code)

        if _missing_asset_id:
            await insert_dead_letter(
                conn,
                venue_code=original_raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=original_raw.source_channel,
                failure_stage="normalization",
                error_class="missing_asset_mapping",
                error_message=f"asset_id={_missing_asset_id!r} not in local mapping; run 'pmfi markets discover' and 'pmfi markets watch'",
                payload=original_raw.payload,
            )
            outcome.dead_letter_inserted = True
            logger.debug("missing_asset_mapping dead_letter venue=%s asset_id=%s", original_raw.venue_code, _missing_asset_id)
            return outcome

        try:
            trade = normalize_event(normalization_raw)
        except NormalizationError as _norm_err:
            _err_msg = str(_norm_err)
            if "unsupported venue:" in _err_msg:
                _error_class = "unsupported_venue"
            elif "normalizer_exception" in _err_msg:
                _error_class = "normalizer_exception"
            elif any(k in _err_msg for k in ("price", "size", "count", "contracts")):
                _error_class = "invalid_price_or_size"
            elif any(k in _err_msg for k in ("timestamp", "decimal", "invalid")):
                _error_class = "payload_schema_mismatch"
            else:
                _error_class = "normalization_error"
            await insert_dead_letter(
                conn,
                venue_code=original_raw.venue_code,
                raw_event_id=raw_event_id,
                source_channel=original_raw.source_channel,
                failure_stage="normalization",
                error_class=_error_class,
                error_message=_err_msg,
                payload=original_raw.payload,
            )
            outcome.dead_letter_inserted = True
            logger.debug("dead_letter written error_class=%s venue=%s", _error_class, original_raw.venue_code)
            return outcome

        if trade is None:
            # Benign non-trade event (lifecycle, subscription ack, etc.) — skip silently
            outcome.non_trade_skipped = True
            logger.debug("non-trade event skipped venue=%s event_type=%s", original_raw.venue_code, original_raw.source_event_type)
            return outcome
        if normalization_raw is not original_raw:
            trade = dataclasses.replace(trade, source_payload=original_raw.payload)

        market_id = await upsert_market(
            conn,
            venue_code=trade.venue_code,
            venue_market_id=trade.venue_market_id,
            title=trade.venue_market_id,
        )
        trade_id = await insert_trade(conn, trade, raw_event_id=raw_event_id, market_id=market_id)
        if trade_id is None:
            outcome.duplicate_trade = True
            logger.debug("duplicate trade skipped venue=%s venue_trade_id=%s", trade.venue_code, trade.venue_trade_id)
            return outcome
        outcome.normalized_trade_inserted = True
        await upsert_metric_window(conn, trade, market_id=market_id, window_seconds=300)

    if capture_orderbook and raw.venue_code == "polymarket":
        token_id = _extract_token_id(raw.payload)
        if token_id:
            try:
                raw_book = await fetch_polymarket_book(token_id)
                if raw_book is not None:
                    bids, asks = parse_book_levels(raw_book)
                    orderbook_snapshot = {
                        "bids": bids,
                        "asks": asks,
                        "payload": raw_book,
                        **compute_book_summary(bids, asks),
                    }
            except Exception as ob_exc:
                logger.debug("orderbook capture non-fatal: %s", ob_exc)

    decisions = engine.evaluate(trade)
    logger.debug("engine.evaluate: %d decision(s) for market=%s", len(decisions), trade.venue_market_id)

    if orderbook_snapshot is None and not any(decision.emit_alert for decision in decisions):
        return outcome

    async with pool.acquire() as conn:
        if orderbook_snapshot is not None:
            try:
                await insert_orderbook_snapshot(
                    conn,
                    venue_code=raw.venue_code,
                    market_id=market_id,
                    raw_event_id=raw_event_id,
                    bids=orderbook_snapshot["bids"],
                    asks=orderbook_snapshot["asks"],
                    is_reconstructed=True,
                    payload=orderbook_snapshot["payload"],
                    best_bid=orderbook_snapshot["best_bid"],
                    best_ask=orderbook_snapshot["best_ask"],
                    spread=orderbook_snapshot["spread"],
                    top_depth_usd=orderbook_snapshot["top_depth_usd"],
                )
            except Exception as ob_exc:
                logger.debug("orderbook capture non-fatal: %s", ob_exc)
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
                    outcome.alerts_suppressed += 1
                    continue
                suppression[key] = now

            decision_with_lineage = _with_lineage_evidence(
                decision,
                raw=raw,
                raw_event_id=raw_event_id,
                trade=trade,
                trade_id=trade_id,
                market_id=market_id,
            )
            title = f"{decision.rule_id} on {trade.venue_market_id}"
            summary = f"{decision.severity} alert: capital={trade.capital_at_risk_usd}"
            _event_ts = trade.exchange_ts or trade.received_at
            alert_id = await insert_alert(
                conn, decision_with_lineage,
                event_ts=_event_ts,
                title=title, summary=summary,
                venue_code=trade.venue_code,
                market_id=market_id,
                outcome_key=trade.outcome_key,
            )
            if alert_id:
                outcome.alerts_inserted += 1
                logger.info("alert inserted id=%s rule=%s severity=%s", alert_id, decision.rule_id, decision.severity)
            try:
                await alert_handler(decision_with_lineage, trade.venue_code, market_id)
                outcome.alerts_delivered += 1
            except Exception as cb_exc:
                logger.warning("alert_handler error (non-fatal): %s", cb_exc)
    return outcome


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
    stats: PipelineStats | None = None,
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
    async for raw in adapter_events:
        if stats is not None:
            stats.raw_events_seen += 1
        try:
            outcome = await process_event(
                raw, pool, engine, alert_handler,
                suppression=suppression,
                suppression_window_seconds=suppression_window_seconds,
                capture_orderbook=capture_orderbook,
                asset_id_map=asset_id_map,
            )
            if stats is not None:
                stats.record(outcome)
            processed += 1
            if max_events and processed >= max_events:
                break
        except Exception as exc:
            if stats is not None:
                stats.processing_errors += 1
            logger.error("Pipeline error processing event: %s", exc)
    return processed
