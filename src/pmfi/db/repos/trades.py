from __future__ import annotations
import json
from decimal import Decimal
import asyncpg
from pmfi.domain import NormalizedTrade

async def insert_trade(
    conn: asyncpg.Connection,
    trade: NormalizedTrade,
    *,
    raw_event_id: int | None,
    market_id: str,
) -> str | None:
    """Insert a normalized trade. Returns trade_id str, or None if already persisted.

    Deduplicates by venue_trade_id when set (covers WS reconnect re-sends and
    different raw payload shapes for the same trade). The caller must skip
    downstream metric/alert processing when None is returned.
    """
    received_at = trade.received_at
    if received_at.tzinfo is None:
        from datetime import timezone
        received_at = received_at.replace(tzinfo=timezone.utc)

    if trade.venue_trade_id:
        existing = await conn.fetchval(
            "SELECT trade_id FROM normalized_trades "
            "WHERE venue_code = $1 AND venue_trade_id = $2 LIMIT 1",
            trade.venue_code, trade.venue_trade_id,
        )
        if existing is not None:
            return None
    else:
        # No venue-assigned trade ID: dedup via a deterministic content fingerprint
        # of (venue_code, market_id, exchange_ts, price, contracts, outcome_key).
        # This makes replay / reconnect of null-id trades idempotent so that
        # metric_windows are not double-counted.
        existing_null = await conn.fetchval(
            """SELECT trade_id FROM normalized_trades
               WHERE venue_code = $1
                 AND market_id = $2::uuid
                 AND exchange_ts IS NOT DISTINCT FROM $3
                 AND price = $4
                 AND contracts = $5
                 AND outcome_key = $6
                 AND venue_trade_id IS NULL
               LIMIT 1""",
            trade.venue_code, market_id,
            trade.exchange_ts, trade.price, trade.contracts, trade.outcome_key,
        )
        if existing_null is not None:
            return None

    row = await conn.fetchrow(
        """INSERT INTO normalized_trades
           (raw_event_id, raw_event_received_at, venue_code, venue_trade_id, market_id,
            outcome_key, aggressor_side, directional_side, side_confidence,
            price, contracts, capital_at_risk_usd, payout_notional_usd,
            exchange_ts, received_at, normalization_version, warnings, source_payload)
           VALUES ($1,$2,$3,$4,$5::uuid,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18::jsonb)
           RETURNING trade_id::text""",
        raw_event_id, received_at if raw_event_id else None,
        trade.venue_code, trade.venue_trade_id, market_id,
        trade.outcome_key, trade.aggressor_side, trade.directional_side, trade.side_confidence,
        trade.price, trade.contracts,
        trade.capital_at_risk_usd, trade.payout_notional_usd,
        trade.exchange_ts, received_at, "trade.v1",
        list(trade.warnings), json.dumps(trade.source_payload),
    )
    return str(row["trade_id"])
