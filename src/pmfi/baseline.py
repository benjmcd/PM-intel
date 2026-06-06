from __future__ import annotations
import asyncpg
from pmfi.db.repos.baselines import upsert_baseline, fetch_all_baselines


async def compute_market_baselines(
    pool: asyncpg.Pool,
    *,
    lookback_seconds: int = 7 * 86400,
) -> list[dict]:
    """Compute per-market trade-size percentiles from metric_windows and upsert into market_baselines."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                mw.market_id::text,
                mw.venue_code,
                m.venue_market_id,
                COUNT(*) AS sample_size,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY mw.max_trade_capital_at_risk_usd)
                    AS p50_trade_usd,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY mw.max_trade_capital_at_risk_usd)
                    AS p95_trade_usd,
                percentile_cont(0.99) WITHIN GROUP (ORDER BY mw.max_trade_capital_at_risk_usd)
                    AS p99_trade_usd,
                percentile_cont(0.995) WITHIN GROUP (ORDER BY mw.max_trade_capital_at_risk_usd)
                    AS p995_trade_usd,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY mw.gross_capital_at_risk_usd)
                    AS median_5m_flow_usd,
                percentile_cont(0.99) WITHIN GROUP (ORDER BY mw.gross_capital_at_risk_usd)
                    AS p99_5m_flow_usd
            FROM metric_windows mw
            JOIN markets m ON m.market_id = mw.market_id
            WHERE mw.window_start >= now() - ($1 || ' seconds')::interval
              AND mw.max_trade_capital_at_risk_usd IS NOT NULL
            GROUP BY mw.market_id, mw.venue_code, m.venue_market_id
            HAVING COUNT(*) >= 2
            """,
            str(lookback_seconds),
        )

        results = []
        for r in rows:
            bid = await upsert_baseline(
                conn,
                market_id=r["market_id"],
                venue_code=r["venue_code"],
                scope="market",
                lookback_seconds=lookback_seconds,
                sample_size=int(r["sample_size"]),
                p50_trade_usd=float(r["p50_trade_usd"]) if r["p50_trade_usd"] is not None else None,
                p95_trade_usd=float(r["p95_trade_usd"]) if r["p95_trade_usd"] is not None else None,
                p99_trade_usd=float(r["p99_trade_usd"]) if r["p99_trade_usd"] is not None else None,
                p995_trade_usd=float(r["p995_trade_usd"]) if r["p995_trade_usd"] is not None else None,
                median_5m_flow_usd=float(r["median_5m_flow_usd"]) if r["median_5m_flow_usd"] is not None else None,
                p99_5m_flow_usd=float(r["p99_5m_flow_usd"]) if r["p99_5m_flow_usd"] is not None else None,
                baseline_payload={"venue_market_id": r["venue_market_id"]},
            )
            results.append({
                "baseline_id": bid,
                "market_id": r["market_id"],
                "venue_code": r["venue_code"],
                "venue_market_id": r["venue_market_id"],
                "sample_size": int(r["sample_size"]),
                "p99_trade_usd": float(r["p99_trade_usd"]) if r["p99_trade_usd"] is not None else None,
            })
        return results


async def load_baselines(pool: asyncpg.Pool) -> dict[str, dict]:
    async with pool.acquire() as conn:
        return await fetch_all_baselines(conn)
