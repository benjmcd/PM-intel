from __future__ import annotations
import json
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

    exchange_ts_key = trade.exchange_ts

    try:
        async with conn.transaction():
            if trade.venue_trade_id:
                claimed = await conn.fetchrow(
                    """INSERT INTO normalized_trade_dedupe_keys
                       (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
                        price, contracts, outcome_key)
                       VALUES ($1, $2, $3::uuid, $4,
                               COALESCE($4::timestamptz, '-infinity'::timestamptz),
                               $5, $6, $7)
                       ON CONFLICT (venue_code, venue_trade_id)
                       WHERE venue_trade_id IS NOT NULL
                       DO NOTHING
                       RETURNING dedupe_id""",
                    trade.venue_code, trade.venue_trade_id, market_id,
                    exchange_ts_key, trade.price, trade.contracts, trade.outcome_key,
                )
            else:
                claimed = await conn.fetchrow(
                    """INSERT INTO normalized_trade_dedupe_keys
                       (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
                        price, contracts, outcome_key)
                       VALUES ($1, NULL, $2::uuid, $3,
                               COALESCE($3::timestamptz, '-infinity'::timestamptz),
                               $4, $5, $6)
                       ON CONFLICT (venue_code, market_id, exchange_ts_key, price, contracts, outcome_key)
                       WHERE venue_trade_id IS NULL
                       DO NOTHING
                       RETURNING dedupe_id""",
                    trade.venue_code, market_id, exchange_ts_key,
                    trade.price, trade.contracts, trade.outcome_key,
                )
            if claimed is None:
                return None

            row = await conn.fetchrow(
                """INSERT INTO normalized_trades
                   (raw_event_id, raw_event_received_at, venue_code, venue_trade_id, market_id,
                    outcome_id, outcome_key, aggressor_side, directional_side, side_confidence,
                    price, contracts, capital_at_risk_usd, payout_notional_usd, fee_usd,
                    exchange_ts, received_at, normalization_version, warnings, source_payload)
                   VALUES ($1,$2,$3,$4,$5::uuid,
                           (SELECT outcome_id FROM market_outcomes
                            WHERE market_id=$5::uuid AND outcome_key=$6 LIMIT 1),
                           $6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::jsonb)
                   RETURNING trade_id""",
                raw_event_id, received_at if raw_event_id else None,
                trade.venue_code, trade.venue_trade_id, market_id,
                trade.outcome_key, trade.aggressor_side, trade.directional_side, trade.side_confidence,
                trade.price, trade.contracts,
                trade.capital_at_risk_usd, trade.payout_notional_usd, trade.fee_usd,
                trade.exchange_ts, received_at, "trade.v1",
                list(trade.warnings), json.dumps(trade.source_payload),
            )
            trade_id = row["trade_id"]
            await conn.execute(
                "UPDATE normalized_trade_dedupe_keys SET trade_id = $1 WHERE dedupe_id = $2",
                trade_id, claimed["dedupe_id"],
            )
    except asyncpg.exceptions.SerializationError:
        # Under repeatable-read/serializable snapshots, a concurrent unique-guard
        # conflict can surface as serialization failure instead of DO NOTHING.
        # The committed winner owns the identity, so preserve the duplicate contract.
        return None

    return str(trade_id)
