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
    event_ts = trade.exchange_ts or trade.received_at
    if event_ts.tzinfo is None:
        event_ts = event_ts.replace(tzinfo=timezone.utc)
    window_start = datetime(
        event_ts.year, event_ts.month, event_ts.day,
        event_ts.hour, (event_ts.minute // (window_seconds // 60)) * (window_seconds // 60),
        tzinfo=timezone.utc,
    ) if window_seconds >= 60 else event_ts.replace(second=(event_ts.second // window_seconds) * window_seconds, microsecond=0)
    cap: Decimal = trade.capital_at_risk_usd
    payout: Decimal = trade.payout_notional_usd
    await conn.execute(
        """INSERT INTO metric_windows
           (market_id, venue_code, outcome_key, window_start, window_seconds,
            trade_count, gross_capital_at_risk_usd, max_trade_capital_at_risk_usd,
            payout_notional_usd, metric_version)
           VALUES ($1::uuid,$2,$3,$4,$5,1,$6,$6,$7,'metrics.v1')
           ON CONFLICT (market_id, outcome_key, window_start, window_seconds)
           DO UPDATE SET
             trade_count = metric_windows.trade_count + 1,
             gross_capital_at_risk_usd = metric_windows.gross_capital_at_risk_usd
               + EXCLUDED.gross_capital_at_risk_usd,
             max_trade_capital_at_risk_usd = GREATEST(
               metric_windows.max_trade_capital_at_risk_usd,
               EXCLUDED.max_trade_capital_at_risk_usd
             ),
             payout_notional_usd = metric_windows.payout_notional_usd
               + EXCLUDED.payout_notional_usd""",
        market_id, trade.venue_code, trade.outcome_key, window_start, window_seconds,
        cap, payout,
    )
