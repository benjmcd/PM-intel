from __future__ import annotations
import asyncpg
from pmfi.domain import NormalizedTrade
from datetime import datetime, timezone
from decimal import Decimal

async def upsert_metric_window(
    conn: asyncpg.Connection,
    trade: NormalizedTrade,
    *,
    market_id: str,
    window_seconds: int = 300,
) -> None:
    now = trade.received_at
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    window_start = datetime(
        now.year, now.month, now.day,
        now.hour, (now.minute // (window_seconds // 60)) * (window_seconds // 60),
        tzinfo=timezone.utc,
    ) if window_seconds >= 60 else now.replace(second=(now.second // window_seconds) * window_seconds, microsecond=0)
    await conn.execute(
        """INSERT INTO metric_windows
           (market_id, venue_code, outcome_key, window_start, window_seconds,
            trade_count, gross_capital_at_risk_usd, payout_notional_usd, metric_version)
           VALUES ($1::uuid,$2,$3,$4,$5,1,$6,$7,'metrics.v1')
           ON CONFLICT DO NOTHING""",
        market_id, trade.venue_code, trade.outcome_key, window_start, window_seconds,
        float(trade.capital_at_risk_usd), float(trade.payout_notional_usd),
    )
