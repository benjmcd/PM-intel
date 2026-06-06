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
) -> str:
    received_at = trade.received_at
    if received_at.tzinfo is None:
        from datetime import timezone
        received_at = received_at.replace(tzinfo=timezone.utc)
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
        # Pass Decimal directly — asyncpg >= 0.29 maps Python Decimal to Postgres
        # numeric without precision loss. float() conversion removed (was P0.5 bug).
        # P1 TODO: add a unique constraint on (venue_code, venue_trade_id) and an
        # ON CONFLICT DO NOTHING / RETURNING clause to deduplicate live duplicate events.
        trade.price, trade.contracts,
        trade.capital_at_risk_usd, trade.payout_notional_usd,
        trade.exchange_ts, received_at, "trade.v1",
        list(trade.warnings), json.dumps(trade.source_payload),
    )
    return str(row["trade_id"])
