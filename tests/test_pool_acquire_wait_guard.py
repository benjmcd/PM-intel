from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _Acquire:
    def __init__(self, clock: _Clock, wait_seconds: float) -> None:
        self._clock = clock
        self._wait_seconds = wait_seconds

    async def __aenter__(self) -> object:
        self._clock.advance(self._wait_seconds)
        return object()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _Pool:
    def __init__(self, clock: _Clock, wait_seconds: float) -> None:
        self._clock = clock
        self._wait_seconds = wait_seconds
        self.close = AsyncMock()

    def acquire(self) -> _Acquire:
        return _Acquire(self._clock, self._wait_seconds)


def test_pool_manager_records_acquire_wait_and_guard_degrades() -> None:
    from pmfi.operational_health import (
        OperationalHealthState,
        PoolAcquireWaitGuard,
        PoolAcquireWaitStats,
    )
    from pmfi.pipeline.supervisor import PoolManager

    clock = _Clock()
    raw_pool = _Pool(clock, wait_seconds=0.150)
    stats = PoolAcquireWaitStats(clock=clock)

    async def run() -> dict:
        pm = PoolManager("fake_dsn", acquire_wait_stats=stats)
        with patch("pmfi.db.create_pool_with_retry", return_value=raw_pool):
            await pm.open()
        async with pm.pool.acquire():
            pass
        state = OperationalHealthState()
        guard = PoolAcquireWaitGuard(threshold_ms=100, stats=stats)
        return await guard.evaluate(pm.pool, state)

    snapshot = asyncio.run(run())

    assert snapshot["status"] == "DEGRADED"
    assert snapshot["intake_allowed"] is True
    assert snapshot["reasons"][0]["reason"] == "pool_acquire_wait_p95_high"
    assert snapshot["reasons"][0]["observed"]["p95_ms"] == 150.0


def test_pool_acquire_wait_guard_stays_ok_under_threshold() -> None:
    from pmfi.operational_health import (
        OperationalHealthState,
        PoolAcquireWaitGuard,
        PoolAcquireWaitStats,
    )
    from pmfi.pipeline.supervisor import PoolManager

    clock = _Clock()
    raw_pool = _Pool(clock, wait_seconds=0.020)
    stats = PoolAcquireWaitStats(clock=clock)

    async def run() -> dict:
        pm = PoolManager("fake_dsn", acquire_wait_stats=stats)
        with patch("pmfi.db.create_pool_with_retry", return_value=raw_pool):
            await pm.open()
        async with pm.pool.acquire():
            pass
        state = OperationalHealthState()
        guard = PoolAcquireWaitGuard(threshold_ms=100, stats=stats)
        return await guard.evaluate(pm.pool, state)

    snapshot = asyncio.run(run())

    assert snapshot["status"] == "OK"
    assert snapshot["reasons"] == []


def test_pool_acquire_wait_stats_reports_rolling_p95() -> None:
    from pmfi.operational_health import PoolAcquireWaitStats

    stats = PoolAcquireWaitStats(max_samples=4)
    for wait_seconds in (0.010, 0.020, 0.030, 0.200, 0.040):
        stats.record_seconds(wait_seconds)

    snapshot = stats.snapshot()

    assert snapshot["sample_count"] == 4
    assert snapshot["p95_ms"] == 200.0
    assert snapshot["max_ms"] == 200.0
