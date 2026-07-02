from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        database=SimpleNamespace(
            url="postgresql://fake/db",
            pool_min_size=1,
            pool_max_size=1,
        ),
        features=SimpleNamespace(
            enable_polymarket_live=False,
            enable_kalshi_live=False,
            enable_orderbook_reconstruction=False,
        ),
        alerts=SimpleNamespace(
            default_delivery="stdout",
            suppression_window_seconds=30,
        ),
        ingestion=SimpleNamespace(
            reconnect_initial_backoff=1,
            reconnect_max_backoff=60,
            reconnect_jitter=False,
            live_api_timeout_seconds=1,
            raw_retention_days=90,
            kalshi_poll_interval_seconds=5,
            kalshi_trade_poll_limit=200,
            kalshi_trade_poll_max_pages=1,
        ),
        baselines=SimpleNamespace(
            recompute_interval_minutes=1440,
            recompute_enabled=False,
            window_days=30,
            min_samples=10,
        ),
    )


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        venue=["polymarket", "kalshi"],
        dry_run=False,
        max_seconds=0,
        log_file=None,
    )


def test_cmd_ingest_single_active_conflict_fails_before_pool_open(capsys):
    from pmfi.cli import cmd_ingest

    events: list[str] = []

    class _Lock:
        def __init__(self, dsn: str):
            events.append(f"lock.init:{dsn}")

        async def acquire(self) -> bool:
            events.append("lock.acquire")
            return False

        async def close(self) -> None:
            events.append("lock.close")

    class _PoolManager:
        def __init__(self, *args, **kwargs):
            events.append("pool.init")

        async def open(self):
            events.append("pool.open")

    with (
        patch("pmfi.config.load_config", return_value=_cfg()),
        patch("pmfi.db.advisory_lock.SingleActiveIngestLock", _Lock),
        patch("pmfi.pipeline.supervisor.PoolManager", _PoolManager),
    ):
        rc = cmd_ingest(_args())

    assert rc == 1
    assert "another PMFI ingest daemon holds the single-active lock" in capsys.readouterr().out
    assert "lock.acquire" in events
    assert "pool.init" not in events
    assert "pool.open" not in events


def test_cmd_ingest_single_active_releases_after_preflight_exit(capsys):
    from pmfi.cli import cmd_ingest

    events: list[str] = []

    class _Lock:
        def __init__(self, dsn: str):
            events.append(f"lock.init:{dsn}")

        async def acquire(self) -> bool:
            events.append("lock.acquire")
            return True

        async def reacquire(self) -> bool:
            events.append("lock.reacquire")
            return True

        async def close(self) -> None:
            events.append("lock.close")

    class _Acquire:
        async def __aenter__(self):
            events.append("pool.acquire.enter")
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            events.append("pool.acquire.exit")
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    class _PoolManager:
        def __init__(self, *args, **kwargs):
            self.on_recreate = kwargs.get("on_recreate")
            self.pool = _Pool()
            events.append("pool.init")

        async def open(self):
            events.append("pool.open")
            assert callable(self.on_recreate)
            await self.on_recreate()

        async def close(self):
            events.append("pool.close")

    with (
        patch("pmfi.config.load_config", return_value=_cfg()),
        patch("pmfi.db.advisory_lock.SingleActiveIngestLock", _Lock),
        patch("pmfi.pipeline.supervisor.PoolManager", _PoolManager),
        patch("pmfi.db.migrations.startup_maintenance", new=AsyncMock()),
        patch("pmfi.baseline.load_baselines", new=AsyncMock(return_value={})),
        patch("pmfi.db.repos.markets.fetch_watched_markets", new=AsyncMock(return_value=[])),
        patch("pmfi.markets.load_asset_id_mapping", new=AsyncMock(return_value={})),
    ):
        rc = cmd_ingest(_args())

    assert rc == 1
    assert events.index("lock.acquire") < events.index("pool.open")
    assert "lock.reacquire" in events
    assert events.index("pool.close") < events.index("lock.close")
    assert "[ingest] No watched markets." in capsys.readouterr().out
