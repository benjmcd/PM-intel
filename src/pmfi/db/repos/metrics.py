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


async def compute_baselines(
    conn,
    *,
    window_days: int = 30,
    min_samples: int = 10,
) -> dict:
    """Compute p99/p995 capital_at_risk_usd baselines per market from recent trades.

    Returns baseline dict keyed by '{venue_code}:{venue_market_id}'.
    Only includes markets with at least min_samples trades.
    """
    # Queries normalized_trades (per-trade level) for fidelity.
    # Preferred over baseline.compute_market_baselines() which uses window aggregates.
    rows = await conn.fetch(
        """
        SELECT
            nt.venue_code,
            m.venue_market_id,
            PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd) AS p99,
            PERCENTILE_CONT(0.995) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd) AS p995,
            COUNT(*) AS sample_size
        FROM normalized_trades nt
        JOIN markets m ON m.market_id = nt.market_id
        WHERE nt.received_at >= NOW() - ($1 || ' days')::interval
        GROUP BY nt.venue_code, m.venue_market_id
        HAVING COUNT(*) >= $2
        """,
        str(window_days),
        min_samples,
    )
    return {
        f"{row['venue_code']}:{row['venue_market_id']}": {
            "p99_trade_usd": float(row["p99"]),
            "p995_trade_usd": float(row["p995"]),
            "sample_size": int(row["sample_size"]),
        }
        for row in rows
    }
