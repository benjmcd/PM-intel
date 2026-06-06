"""Orderbook snapshot persistence."""
from __future__ import annotations
import json
from decimal import Decimal
from typing import Any
import asyncpg


async def insert_orderbook_snapshot(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    market_id: str,
    raw_event_id: int | None = None,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
    spread: Decimal | None = None,
    top_depth_usd: Decimal | None = None,
    bids: list[dict] | None = None,
    asks: list[dict] | None = None,
    is_reconstructed: bool = True,
    payload: dict[str, Any] | None = None,
) -> str:
    """Insert an orderbook snapshot and return the snapshot_id::text."""
    payload_json = json.dumps(payload or {})
    row = await conn.fetchrow(
        """INSERT INTO orderbook_snapshots
               (venue_code, market_id, source, is_reconstructed, best_bid, best_ask,
                spread, top_depth_usd, raw_event_id, payload)
           VALUES ($1, $2, 'rest_poll', $3, $4, $5, $6, $7, $8, $9::jsonb)
           RETURNING orderbook_snapshot_id::text""",
        venue_code, market_id, is_reconstructed,
        float(best_bid) if best_bid is not None else None,
        float(best_ask) if best_ask is not None else None,
        float(spread) if spread is not None else None,
        float(top_depth_usd) if top_depth_usd is not None else None,
        raw_event_id,
        payload_json,
    )
    snapshot_id = str(row["orderbook_snapshot_id"])

    if bids or asks:
        await _insert_levels(conn, snapshot_id=snapshot_id, market_id=market_id,
                             outcome_key="yes", bids=bids or [], asks=asks or [])

    return snapshot_id


async def _insert_levels(
    conn: asyncpg.Connection,
    *,
    snapshot_id: str,
    market_id: str,
    outcome_key: str,
    bids: list[dict],
    asks: list[dict],
) -> None:
    captured_at_row = await conn.fetchrow(
        "SELECT captured_at FROM orderbook_snapshots WHERE orderbook_snapshot_id=$1::uuid",
        snapshot_id,
    )
    if not captured_at_row:
        return
    captured_at = captured_at_row["captured_at"]

    for idx, level in enumerate(bids[:10]):  # cap at 10 levels
        await conn.execute(
            """INSERT INTO orderbook_levels
                   (orderbook_snapshot_id, captured_at, market_id, outcome_key, side, price, contracts, level_index)
               VALUES ($1::uuid, $2, $3::uuid, $4, 'bid', $5, $6, $7)
               ON CONFLICT DO NOTHING""",
            snapshot_id, captured_at, market_id, outcome_key,
            float(level["price"]), float(level["size"]), idx,
        )
    for idx, level in enumerate(asks[:10]):
        await conn.execute(
            """INSERT INTO orderbook_levels
                   (orderbook_snapshot_id, captured_at, market_id, outcome_key, side, price, contracts, level_index)
               VALUES ($1::uuid, $2, $3::uuid, $4, 'ask', $5, $6, $7)
               ON CONFLICT DO NOTHING""",
            snapshot_id, captured_at, market_id, outcome_key,
            float(level["price"]), float(level["size"]), idx,
        )
