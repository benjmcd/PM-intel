from __future__ import annotations

from typing import TYPE_CHECKING

from pmfi.db.repos.baselines import fetch_all_baselines, upsert_baseline

if TYPE_CHECKING:
    import asyncpg


async def compute_market_baselines(
    pool: asyncpg.Pool,
    *,
    lookback_seconds: int = 7 * 86400,
    min_samples: int = 2,
) -> list[dict]:
    """Compute per-market trade-size percentiles from normalized_trades and upsert baselines."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                nt.market_id::text,
                nt.venue_code,
                m.venue_market_id,
                COUNT(*) AS sample_size,
                percentile_cont(0.50) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd)
                    AS p50_trade_usd,
                percentile_cont(0.95) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd)
                    AS p95_trade_usd,
                percentile_cont(0.99) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd)
                    AS p99_trade_usd,
                percentile_cont(0.995) WITHIN GROUP (ORDER BY nt.capital_at_risk_usd)
                    AS p995_trade_usd,
                NULL::numeric AS median_5m_flow_usd,
                NULL::numeric AS p99_5m_flow_usd
            FROM normalized_trades nt
            JOIN markets m ON m.market_id = nt.market_id
            WHERE nt.received_at >= now() - ($1 || ' seconds')::interval
              AND nt.capital_at_risk_usd IS NOT NULL
            GROUP BY nt.market_id, nt.venue_code, m.venue_market_id
            HAVING COUNT(*) >= $2
            """,
            str(lookback_seconds),
            min_samples,
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
                "min_samples": min_samples,
                "p99_trade_usd": float(r["p99_trade_usd"]) if r["p99_trade_usd"] is not None else None,
            })
        return results


async def load_baselines(pool: asyncpg.Pool) -> dict[str, dict]:
    async with pool.acquire() as conn:
        return await fetch_all_baselines(conn)
