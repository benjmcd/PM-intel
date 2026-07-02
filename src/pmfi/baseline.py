from __future__ import annotations
import asyncpg
from pmfi.db.repos.baselines import fetch_all_baselines
from pmfi.db.repos.baselines import upsert_baseline
from pmfi.db.repos.metrics import compute_baselines


async def compute_market_baselines(
    pool: asyncpg.Pool,
    *,
    lookback_seconds: int = 7 * 86400,
) -> list[dict]:
    """DEPRECATED — diagnostic read-only path. Do NOT use as the canonical baseline writer.

    Computes per-market trade-size percentiles from metric_windows aggregates
    (max_trade_capital_at_risk_usd per 5-min window) and returns the results as a
    list of dicts. This function NO LONGER writes to market_baselines.

    Use compute_and_store_baselines() instead — it reads from normalized_trades
    (per-trade level) and is the sole canonical writer to market_baselines.
    """
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

        return [
            {
                "market_id": r["market_id"],
                "venue_code": r["venue_code"],
                "venue_market_id": r["venue_market_id"],
                "sample_size": int(r["sample_size"]),
                "p99_trade_usd": float(r["p99_trade_usd"]) if r["p99_trade_usd"] is not None else None,
            }
            for r in rows
        ]


async def compute_and_store_baselines(
    pool: asyncpg.Pool,
    *,
    window_days: int = 30,
    min_samples: int = 10,
) -> dict:
    """Compute per-trade baselines from normalized_trades and UPSERT them into
    market_baselines (the canonical store read by ingest/live/replay/monitor)."""
    async with pool.acquire() as conn:
        entries = await compute_baselines(conn, window_days=window_days, min_samples=min_samples)
        lookback_seconds = window_days * 86400
        active_market_ids: list[str] = []
        for key, entry in entries.items():
            market_id = entry.get("market_id")
            if not market_id:
                continue
            active_market_ids.append(str(market_id))
            venue_code = key.split(":", 1)[0]
            venue_market_id = key.split(":", 1)[1] if ":" in key else key
            await upsert_baseline(
                conn,
                market_id=market_id,
                venue_code=venue_code,
                scope="market",
                lookback_seconds=lookback_seconds,
                sample_size=entry["sample_size"],
                p50_trade_usd=None,
                p95_trade_usd=None,
                p99_trade_usd=entry["p99_trade_usd"],
                p995_trade_usd=entry["p995_trade_usd"],
                median_5m_flow_usd=None,
                p99_5m_flow_usd=None,
                baseline_payload={"venue_market_id": venue_market_id},
            )
        if active_market_ids:
            await conn.execute(
                """
                DELETE FROM market_baselines
                WHERE scope = 'market'
                  AND lookback_seconds = $1
                  AND NOT (market_id = ANY($2::uuid[]))
                """,
                lookback_seconds,
                active_market_ids,
            )
        else:
            await conn.execute(
                """
                DELETE FROM market_baselines
                WHERE scope = 'market'
                  AND lookback_seconds = $1
                """,
                lookback_seconds,
            )
    return entries


async def load_baselines(pool: asyncpg.Pool) -> dict[str, dict]:
    async with pool.acquire() as conn:
        return await fetch_all_baselines(conn)
