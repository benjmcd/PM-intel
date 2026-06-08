"""Offline unit test: compute_baselines returns market_id in each entry.

No DB required — uses an AsyncMock to simulate conn.fetch returning known rows.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pmfi.db.repos.metrics import compute_baselines


def _fake_row(venue_code: str, market_id: str, venue_market_id: str, p99: float, p995: float, n: int):
    """Return a mapping-like object matching the columns returned by the SQL query."""
    return {
        "venue_code": venue_code,
        "market_id": market_id,
        "venue_market_id": venue_market_id,
        "p99": p99,
        "p995": p995,
        "sample_size": n,
    }


def test_compute_baselines_returns_market_id():
    """compute_baselines must include market_id in every returned dict entry."""
    fake_market_id = "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb"
    fake_rows = [
        _fake_row("kalshi", fake_market_id, "KX-TEST-001", 500.0, 750.0, 20),
    ]

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fake_rows)

    result = asyncio.run(compute_baselines(conn, window_days=7, min_samples=5))

    assert len(result) == 1
    key = "kalshi:KX-TEST-001"
    assert key in result, f"Expected key {key!r} in result, got {list(result.keys())}"

    entry = result[key]
    assert "market_id" in entry, "market_id missing from compute_baselines entry"
    assert entry["market_id"] == fake_market_id
    assert abs(entry["p99_trade_usd"] - 500.0) < 0.001
    assert abs(entry["p995_trade_usd"] - 750.0) < 0.001
    assert entry["sample_size"] == 20


def test_compute_baselines_multiple_markets():
    """market_id is present for every returned entry when multiple markets exist."""
    rows = [
        _fake_row("polymarket", "uuid-poly-1", "PM-MKT-A", 100.0, 150.0, 30),
        _fake_row("kalshi", "uuid-kals-2", "KX-MKT-B", 200.0, 300.0, 15),
    ]

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    result = asyncio.run(compute_baselines(conn, window_days=30, min_samples=10))

    assert len(result) == 2
    for key, entry in result.items():
        assert "market_id" in entry, f"market_id missing from entry for key {key!r}"
        assert entry["market_id"], f"market_id is empty for key {key!r}"
