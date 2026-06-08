from __future__ import annotations
import asyncpg


async def upsert_baseline(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    venue_code: str,
    scope: str,
    lookback_seconds: int,
    sample_size: int,
    p50_trade_usd: float | None,
    p95_trade_usd: float | None,
    p99_trade_usd: float | None,
    p995_trade_usd: float | None,
    median_5m_flow_usd: float | None,
    p99_5m_flow_usd: float | None,
    baseline_payload: dict | None = None,
) -> str:
    import json
    payload = json.dumps(baseline_payload or {})
    row = await conn.fetchrow(
        """
        INSERT INTO market_baselines (
            market_id, venue_code, scope, lookback_seconds, sample_size,
            p50_trade_usd, p95_trade_usd, p99_trade_usd, p995_trade_usd,
            median_5m_flow_usd, p99_5m_flow_usd, baseline_payload
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
        ON CONFLICT (market_id, venue_code, scope) DO UPDATE SET
            lookback_seconds=EXCLUDED.lookback_seconds,
            sample_size=EXCLUDED.sample_size,
            p50_trade_usd=EXCLUDED.p50_trade_usd,
            p95_trade_usd=EXCLUDED.p95_trade_usd,
            p99_trade_usd=EXCLUDED.p99_trade_usd,
            p995_trade_usd=EXCLUDED.p995_trade_usd,
            median_5m_flow_usd=EXCLUDED.median_5m_flow_usd,
            p99_5m_flow_usd=EXCLUDED.p99_5m_flow_usd,
            baseline_payload=EXCLUDED.baseline_payload,
            computed_at=now()
        RETURNING baseline_id
        """,
        market_id, venue_code, scope, lookback_seconds, sample_size,
        p50_trade_usd, p95_trade_usd, p99_trade_usd, p995_trade_usd,
        median_5m_flow_usd, p99_5m_flow_usd, payload,
    )
    return str(row["baseline_id"])


async def fetch_all_baselines(conn: asyncpg.Connection) -> dict[str, dict]:
    rows = await conn.fetch(
        """
        SELECT b.market_id, b.venue_code, m.venue_market_id,
               b.p50_trade_usd, b.p95_trade_usd, b.p99_trade_usd,
               b.p995_trade_usd, b.median_5m_flow_usd, b.p99_5m_flow_usd,
               b.sample_size, b.computed_at
        FROM market_baselines b
        JOIN markets m ON m.market_id = b.market_id
        WHERE b.scope = 'market'
          AND b.computed_at >= now() - (b.lookback_seconds * 2 || ' seconds')::interval
        """
    )
    result: dict[str, dict] = {}
    for r in rows:
        key = f"{r['venue_code']}:{r['venue_market_id']}"
        result[key] = dict(r)
    return result
