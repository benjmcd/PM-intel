from __future__ import annotations

import asyncio
from decimal import Decimal


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _FetchConn:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.args = ()

    async def fetch(self, sql, *args):
        self.sql = sql
        self.args = args
        return self.rows


def test_compute_market_baselines_uses_normalized_trades_and_upserts(monkeypatch):
    from pmfi.baseline import compute_market_baselines

    row = {
        "market_id": "11111111-1111-1111-1111-111111111111",
        "venue_code": "kalshi",
        "venue_market_id": "KXEXAMPLE-26JUN03",
        "sample_size": 2,
        "p50_trade_usd": Decimal("10000"),
        "p95_trade_usd": Decimal("25000"),
        "p99_trade_usd": Decimal("26000"),
        "p995_trade_usd": Decimal("26500"),
        "median_5m_flow_usd": None,
        "p99_5m_flow_usd": None,
    }
    conn = _FetchConn([row])
    captured = []

    async def upsert_baseline(_conn, **kwargs):
        captured.append(kwargs)
        return "baseline-id"

    monkeypatch.setattr("pmfi.baseline.upsert_baseline", upsert_baseline)

    results = asyncio.run(
        compute_market_baselines(
            _Pool(conn),
            lookback_seconds=86400,
            min_samples=2,
        )
    )

    assert "FROM normalized_trades nt" in conn.sql
    assert "metric_windows" not in conn.sql
    assert conn.args == ("86400", 2)
    assert captured[0]["market_id"] == row["market_id"]
    assert captured[0]["sample_size"] == 2
    assert captured[0]["p99_trade_usd"] == 26000.0
    assert captured[0]["baseline_payload"] == {"venue_market_id": "KXEXAMPLE-26JUN03"}
    assert results == [
        {
            "baseline_id": "baseline-id",
            "market_id": row["market_id"],
            "venue_code": "kalshi",
            "venue_market_id": "KXEXAMPLE-26JUN03",
            "sample_size": 2,
            "min_samples": 2,
            "p99_trade_usd": 26000.0,
        }
    ]


def test_upsert_baseline_uses_market_scope_conflict_constraint():
    from pmfi.db.repos.baselines import upsert_baseline

    class Conn:
        def __init__(self):
            self.sql = ""

        async def fetchrow(self, sql, *_args):
            self.sql = sql
            return {"baseline_id": "baseline-id"}

    conn = Conn()
    baseline_id = asyncio.run(
        upsert_baseline(
            conn,
            market_id="11111111-1111-1111-1111-111111111111",
            venue_code="kalshi",
            scope="market",
            lookback_seconds=86400,
            sample_size=2,
            p50_trade_usd=10000.0,
            p95_trade_usd=25000.0,
            p99_trade_usd=26000.0,
            p995_trade_usd=26500.0,
            median_5m_flow_usd=None,
            p99_5m_flow_usd=None,
            baseline_payload={"venue_market_id": "KXEXAMPLE-26JUN03"},
        )
    )

    assert baseline_id == "baseline-id"
    assert "ON CONFLICT ON CONSTRAINT market_baselines_market_scope_unique" in conn.sql
    assert "DO UPDATE SET" in conn.sql
    assert "RETURNING baseline_id" in conn.sql
