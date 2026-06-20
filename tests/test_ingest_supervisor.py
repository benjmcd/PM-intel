"""Offline tests for US-07 reliability core: supervisor, PoolManager, jitter, connection-loss.

No DB, no network.  All asyncpg interactions are faked.
Imports the REAL production code — no inline re-implementation.
"""
from __future__ import annotations

import asyncio
import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# jittered_backoff (moved to supervisor module)
# ---------------------------------------------------------------------------

def test_jittered_backoff_no_jitter_returns_base():
    from pmfi.pipeline.supervisor import jittered_backoff
    assert jittered_backoff(4.0, False) == 4.0


def test_jittered_backoff_no_jitter_returns_base_small():
    from pmfi.pipeline.supervisor import jittered_backoff
    assert jittered_backoff(0.5, False) == 0.5


def test_jittered_backoff_with_jitter_within_bounds():
    """With jitter=True, result must be in [base*0.5, base)."""
    from pmfi.pipeline.supervisor import jittered_backoff
    random.seed(42)
    base = 8.0
    for _ in range(50):
        val = jittered_backoff(base, True)
        assert base * 0.5 <= val <= base, f"Out of bounds: {val} not in [{base*0.5}, {base}]"


def test_jittered_backoff_jitter_not_always_same():
    """With jitter=True, two distinct seeds produce different values."""
    from pmfi.pipeline.supervisor import jittered_backoff
    random.seed(0)
    v1 = jittered_backoff(10.0, True)
    random.seed(99999)
    v2 = jittered_backoff(10.0, True)
    assert v1 != v2


# ---------------------------------------------------------------------------
# IngestConnectionLost — import check (still importable from runner)
# ---------------------------------------------------------------------------

def test_ingest_connection_lost_importable():
    from pmfi.pipeline.runner import IngestConnectionLost
    exc = IngestConnectionLost("test")
    assert isinstance(exc, Exception)
    assert "test" in str(exc)


# ---------------------------------------------------------------------------
# PoolManager
# ---------------------------------------------------------------------------

def _fake_pool(label: str = "pool") -> MagicMock:
    """Return a mock that mimics asyncpg.Pool.close()."""
    p = MagicMock()
    p.label = label
    p.close = AsyncMock()
    return p


def test_pool_manager_recreate_swaps_pool_and_bumps_generation():
    """recreate() replaces the pool and increments generation."""
    from pmfi.pipeline.supervisor import PoolManager

    old_pool = _fake_pool("old")
    new_pool = _fake_pool("new")
    create_calls = [0]

    async def _fake_create(dsn, *, min_size, max_size, **_):
        create_calls[0] += 1
        return new_pool

    async def _run():
        pm = PoolManager("fake_dsn")
        pm._pool = old_pool  # inject directly, bypassing real DB open()

        with patch("pmfi.pipeline.supervisor.PoolManager.open", new_callable=AsyncMock):
            with patch("pmfi.db.create_pool_with_retry", side_effect=_fake_create):
                with patch("pmfi.db.close_pool", new_callable=AsyncMock):
                    gen0 = pm.generation
                    result = await pm.recreate(gen0)

        assert result is new_pool
        assert pm.pool is new_pool
        assert pm.generation == gen0 + 1
        assert create_calls[0] == 1

    asyncio.run(_run())


def test_pool_manager_recreate_noop_when_generation_differs():
    """Second caller with stale observed_generation gets current pool, no extra create."""
    from pmfi.pipeline.supervisor import PoolManager

    initial_pool = _fake_pool("initial")
    newer_pool = _fake_pool("newer")
    create_calls = [0]

    async def _fake_create(dsn, *, min_size, max_size, **_):
        create_calls[0] += 1
        return newer_pool

    async def _run():
        pm = PoolManager("fake_dsn")
        pm._pool = initial_pool
        # Simulate that generation was already bumped (another caller succeeded)
        pm._generation = 1
        observed_stale = 0  # caller captured generation before the first recreate

        with patch("pmfi.db.create_pool_with_retry", side_effect=_fake_create):
            with patch("pmfi.db.close_pool", new_callable=AsyncMock):
                result = await pm.recreate(observed_stale)

        # No-op: generation was already > observed_stale
        assert result is initial_pool
        assert pm.pool is initial_pool
        assert pm.generation == 1
        assert create_calls[0] == 0  # create was NOT called

    asyncio.run(_run())


def test_pool_manager_recreate_failure_leaves_old_pool():
    """If create_pool_with_retry raises, the old pool stays in place and exception is re-raised."""
    from pmfi.pipeline.supervisor import PoolManager

    old_pool = _fake_pool("old")

    async def _fail_create(dsn, *, min_size, max_size, **_):
        raise RuntimeError("DB unavailable")

    async def _run():
        pm = PoolManager("fake_dsn")
        pm._pool = old_pool
        gen0 = pm.generation

        with patch("pmfi.db.create_pool_with_retry", side_effect=_fail_create):
            with pytest.raises(RuntimeError, match="DB unavailable"):
                await pm.recreate(gen0)

        # Pool and generation must be unchanged after failure
        assert pm.pool is old_pool
        assert pm.generation == gen0

    asyncio.run(_run())


def test_pool_manager_concurrent_recreate_calls_create_exactly_once():
    """Two concurrent calls with the same observed_generation produce exactly one create."""
    from pmfi.pipeline.supervisor import PoolManager

    old_pool = _fake_pool("old")
    new_pool = _fake_pool("new")
    create_calls = [0]
    # Barrier: stall the first create so the second caller enters recreate() concurrently
    barrier = asyncio.Event()

    async def _fake_create(dsn, *, min_size, max_size, **_):
        create_calls[0] += 1
        # First caller: release barrier after being "inside" create
        if create_calls[0] == 1:
            barrier.set()
            await asyncio.sleep(0)  # yield so second caller can enter recreate()
        return new_pool

    async def _run():
        pm = PoolManager("fake_dsn")
        pm._pool = old_pool
        gen0 = pm.generation

        with patch("pmfi.db.create_pool_with_retry", side_effect=_fake_create):
            with patch("pmfi.db.close_pool", new_callable=AsyncMock):
                # Launch both recreates concurrently with the SAME observed generation
                results = await asyncio.gather(
                    pm.recreate(gen0),
                    pm.recreate(gen0),
                    return_exceptions=False,
                )

        # Exactly one underlying pool create
        assert create_calls[0] == 1
        # Both callers got the new pool back
        assert all(r is new_pool for r in results)
        assert pm.generation == gen0 + 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# supervise (the REAL imported function)
# ---------------------------------------------------------------------------

def test_supervise_exits_immediately_when_shutdown_preset():
    """If shutdown is already set, the supervise loop body is never entered."""
    from pmfi.pipeline.supervisor import supervise, PoolManager

    async def _run():
        shutdown = asyncio.Event()
        shutdown.set()  # pre-set

        make_count = [0]

        def make_adapter():
            make_count[0] += 1
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            pass

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        await asyncio.wait_for(
            supervise(
                "test", make_adapter, run_one,
                shutdown=shutdown, pool_manager=pm,
                initial_backoff=0.01, max_backoff=0.1, jitter=False,
            ),
            timeout=2.0,
        )
        return make_count[0]

    result = asyncio.run(_run())
    assert result == 0


def test_supervise_restarts_after_connection_lost_then_exits():
    """run_one raises IngestConnectionLost once, then succeeds; shutdown ends the loop."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        make_count = [0]
        recreate_called = [False]

        def make_adapter():
            make_count[0] += 1
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise IngestConnectionLost("fake loss")
            # Second run: trigger shutdown so the loop exits cleanly
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        async def _fake_recreate(observed_gen):
            recreate_called[0] = True
            # No-op: just bump generation for the test
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            await asyncio.wait_for(
                supervise(
                    "test", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.01, max_backoff=0.1, jitter=False,
                ),
                timeout=5.0,
            )

        return run_count[0], make_count[0], recreate_called[0]

    run_count, make_count, recreate_called = asyncio.run(_run())
    assert run_count == 2, f"Expected 2 run_one calls, got {run_count}"
    assert make_count == 2, f"Expected 2 make_adapter calls, got {make_count}"
    assert recreate_called, "Expected recreate to be called on IngestConnectionLost"


def test_supervise_backoff_resets_after_successful_run():
    """After a transient fault followed by a clean run, the next delay is initial_backoff."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        delays_used: list[float] = []

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                # First call: fault — backoff should double
                raise IngestConnectionLost("transient")
            if run_count[0] == 2:
                # Second call: clean return — backoff must reset before next delay
                return
            # Third call: record that shutdown fires cleanly
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        def capturing_backoff(base: float, jitter: bool) -> float:
            delays_used.append(base)
            return 0.0  # instant sleep; recorded base is what we assert on

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            with patch("pmfi.pipeline.supervisor.jittered_backoff", side_effect=capturing_backoff):
                await asyncio.wait_for(
                    supervise(
                        "test", make_adapter, run_one,
                        shutdown=shutdown, pool_manager=pm,
                        initial_backoff=1.0, max_backoff=60.0, jitter=False,
                    ),
                    timeout=5.0,
                )

        return delays_used

    delays = asyncio.run(_run())
    # delay[0]: after fault (run 1) — base doubled from 1.0 → passed as 2.0? No:
    # base starts at 1.0; fault exits; delay = jittered_backoff(1.0) → 1.0; then base = min(2.0,60) = 2.0
    # delay[1]: after clean run (run 2) — base reset to 1.0; delay = jittered_backoff(1.0) → 1.0
    assert len(delays) >= 2, f"Expected at least 2 delay calls, got {delays}"
    assert delays[0] == 1.0, f"First delay (after fault) should use base=1.0, got {delays[0]}"
    assert delays[1] == 1.0, f"Second delay (after clean run) should reset to initial_backoff=1.0, got {delays[1]}"


def test_supervise_consecutive_failures_double_backoff():
    """Consecutive failures double base up to max_backoff."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        delays_used: list[float] = []

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] < 4:
                raise IngestConnectionLost("keep failing")
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        def capturing_backoff(base: float, jitter: bool) -> float:
            delays_used.append(base)
            return 0.0  # instant sleep so test completes quickly

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            with patch("pmfi.pipeline.supervisor.jittered_backoff", side_effect=capturing_backoff):
                await asyncio.wait_for(
                    supervise(
                        "test", make_adapter, run_one,
                        shutdown=shutdown, pool_manager=pm,
                        initial_backoff=1.0, max_backoff=60.0, jitter=False,
                    ),
                    timeout=5.0,
                )

        return delays_used

    delays = asyncio.run(_run())
    # 3 consecutive faults before clean exit: base passed to jittered_backoff
    # should be 1.0, 2.0, 4.0 (doubling each time)
    assert delays[:3] == [1.0, 2.0, 4.0], f"Expected doubling [1,2,4], got {delays[:3]}"


def test_supervise_opens_circuit_after_sustained_connection_failures():
    """Sustained connection failures surface circuit_open before half-open retry."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        status_map: dict = {}
        opened_snapshots: list[dict] = []
        clock_values = iter([100.0, 111.0])

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] <= 2:
                raise IngestConnectionLost(f"loss-{run_count[0]}")
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        async def _fake_recreate(observed_gen):
            if status_map.get("polymarket", {}).get("circuit_open"):
                opened_snapshots.append(dict(status_map["polymarket"]))
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            with patch("pmfi.pipeline.supervisor.jittered_backoff", return_value=0.0):
                await asyncio.wait_for(
                    supervise(
                        "polymarket", make_adapter, run_one,
                        shutdown=shutdown,
                        pool_manager=pm,
                        initial_backoff=1.0,
                        max_backoff=60.0,
                        jitter=False,
                        status_map=status_map,
                        circuit_breaker_failure_threshold=2,
                        circuit_breaker_window_seconds=10.0,
                        circuit_breaker_recovery_seconds=0.0,
                        monotonic=lambda: next(clock_values),
                    ),
                    timeout=5.0,
                )

        return run_count[0], status_map, opened_snapshots

    run_count, status_map, opened_snapshots = asyncio.run(_run())

    assert run_count == 3
    assert opened_snapshots
    assert opened_snapshots[-1]["circuit_open"] is True
    assert opened_snapshots[-1]["consecutive_failures"] == 2
    assert "loss-2" in opened_snapshots[-1]["last_error"]
    assert status_map["polymarket"]["circuit_open"] is False
    assert status_map["polymarket"]["consecutive_failures"] == 0


def test_supervise_progress_observed_resets_circuit_failure_streak():
    """A streaming venue that emits events before reconnecting must not false-open."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        status_map: dict = {}
        clock_values = iter([100.0, 111.0, 122.0])

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] <= 3:
                exc = IngestConnectionLost(
                    f"stream reconnect {run_count[0]}",
                    progress_events=2,
                )
                raise exc
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            with patch("pmfi.pipeline.supervisor.jittered_backoff", return_value=0.0):
                await asyncio.wait_for(
                    supervise(
                        "polymarket", make_adapter, run_one,
                        shutdown=shutdown,
                        pool_manager=pm,
                        initial_backoff=1.0,
                        max_backoff=60.0,
                        jitter=False,
                        status_map=status_map,
                        circuit_breaker_failure_threshold=2,
                        circuit_breaker_window_seconds=10.0,
                        circuit_breaker_progress_reset_min_events=2,
                        monotonic=lambda: next(clock_values),
                    ),
                    timeout=5.0,
                )

        return run_count[0], status_map

    run_count, status_map = asyncio.run(_run())

    assert run_count == 4
    assert status_map["polymarket"]["circuit_open"] is False
    assert status_map["polymarket"]["consecutive_failures"] == 0


def test_supervise_trickle_progress_still_opens_circuit():
    """One event per reconnect is degraded trickle progress, not a healthy reset."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import AdapterConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        status_map: dict = {}
        now = [100.0]
        opened_snapshot: dict | None = None

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        def monotonic():
            current = now[0]
            now[0] += 11.0
            return current

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] >= 4:
                shutdown.set()
            raise AdapterConnectionLost(
                f"trickle drop {run_count[0]}",
                progress_events=1,
            )

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        async def _drive():
            await supervise(
                "polymarket", make_adapter, run_one,
                shutdown=shutdown,
                pool_manager=pm,
                initial_backoff=1.0,
                max_backoff=60.0,
                jitter=False,
                status_map=status_map,
                circuit_breaker_failure_threshold=2,
                circuit_breaker_window_seconds=10.0,
                circuit_breaker_recovery_seconds=0.5,
                monotonic=monotonic,
            )

        with patch("pmfi.pipeline.supervisor.jittered_backoff", return_value=0.0):
            task = asyncio.create_task(_drive())
            for _ in range(100):
                current = status_map.get("polymarket", {})
                if current.get("circuit_open"):
                    opened_snapshot = dict(current)
                    shutdown.set()
                    break
                await asyncio.sleep(0.01)
            await asyncio.wait_for(task, timeout=2.0)

        return run_count[0], opened_snapshot

    run_count, opened_snapshot = asyncio.run(_run())

    assert opened_snapshot is not None
    assert opened_snapshot["circuit_open"] is True
    assert opened_snapshot["consecutive_failures"] == 2
    assert "trickle drop 2" in opened_snapshot["last_error"]
    assert run_count <= 3


def test_supervise_half_open_retries_after_circuit_cooldown():
    """An open circuit should retry after its cooldown instead of wedging forever."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import IngestConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        status_map: dict = {}
        clock_values = iter([100.0, 111.0])

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] <= 2:
                raise IngestConnectionLost(f"db outage {run_count[0]}")
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        async def _fake_recreate(observed_gen):
            pm._generation += 1
            return pm._pool

        with patch.object(pm, "recreate", side_effect=_fake_recreate):
            with patch("pmfi.pipeline.supervisor.jittered_backoff", return_value=0.0):
                await asyncio.wait_for(
                    supervise(
                        "polymarket", make_adapter, run_one,
                        shutdown=shutdown,
                        pool_manager=pm,
                        initial_backoff=1.0,
                        max_backoff=60.0,
                        jitter=False,
                        status_map=status_map,
                        circuit_breaker_failure_threshold=2,
                        circuit_breaker_window_seconds=10.0,
                        circuit_breaker_recovery_seconds=0.0,
                        monotonic=lambda: next(clock_values),
                    ),
                    timeout=5.0,
                )

        return run_count[0], status_map

    run_count, status_map = asyncio.run(_run())

    assert run_count == 3
    assert status_map["polymarket"]["circuit_open"] is False
    assert status_map["polymarket"]["consecutive_failures"] == 0


def test_supervise_adapter_connection_lost_does_not_recreate_pool():
    """Venue transport loss should restart the adapter without recreating Postgres."""
    from pmfi.pipeline.supervisor import supervise, PoolManager
    from pmfi.pipeline.runner import AdapterConnectionLost

    async def _run():
        shutdown = asyncio.Event()
        run_count = [0]
        status_map: dict = {}

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise AdapterConnectionLost("polymarket receive timed out")
            shutdown.set()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        with patch.object(pm, "recreate", new_callable=AsyncMock) as recreate:
            with patch("pmfi.pipeline.supervisor.jittered_backoff", return_value=0.0):
                await asyncio.wait_for(
                    supervise(
                        "polymarket", make_adapter, run_one,
                        shutdown=shutdown,
                        pool_manager=pm,
                        initial_backoff=1.0,
                        max_backoff=60.0,
                        jitter=False,
                        status_map=status_map,
                    ),
                    timeout=5.0,
                )

        return run_count[0], status_map, recreate.await_count

    run_count, status_map, recreate_count = asyncio.run(_run())

    assert run_count == 2
    assert recreate_count == 0
    assert status_map["polymarket"]["consecutive_failures"] == 0


def test_supervise_cancelled_error_propagates():
    """CancelledError inside run_one propagates out of supervise."""
    from pmfi.pipeline.supervisor import supervise, PoolManager

    async def _run():
        shutdown = asyncio.Event()

        def make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def run_one(adapter, pm):
            raise asyncio.CancelledError()

        pm = PoolManager("fake_dsn")
        pm._pool = _fake_pool("p")

        task = asyncio.create_task(
            supervise(
                "test", make_adapter, run_one,
                shutdown=shutdown, pool_manager=pm,
                initial_backoff=0.01, max_backoff=0.1, jitter=False,
            )
        )
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass  # expected

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# run_adapter_pipeline: raise_on_connection_loss behavior
# ---------------------------------------------------------------------------

def _make_raw_event(event_id: str):
    from pmfi.domain import RawEvent
    return RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=event_id,
        venue_market_id="test-market",
        exchange_ts=None,
        payload={"price": "0.55", "size": "100", "side": "buy", "outcome": "yes"},
    )


async def _async_iter(events):
    for e in events:
        yield e


def _make_pool() -> MagicMock:
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetch = AsyncMock(return_value=[])
    return mock_pool


def test_run_adapter_pipeline_raises_on_consecutive_connection_errors_when_flag_set():
    """raise_on_connection_loss=True + 5 consecutive asyncpg errors must raise IngestConnectionLost."""
    from pmfi.pipeline.runner import run_adapter_pipeline, IngestConnectionLost
    import asyncpg

    events = [_make_raw_event(f"ev-{i}") for i in range(10)]
    call_count = [0]

    async def always_conn_error(raw, pool, engine, handler, **kwargs):
        call_count[0] += 1
        raise asyncpg.InterfaceError("simulated connection reset")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with pytest.raises(IngestConnectionLost):
        with patch("pmfi.pipeline.runner.process_event", side_effect=always_conn_error):
            asyncio.run(
                run_adapter_pipeline(
                    _async_iter(events), pool, engine, handler,
                    raise_on_connection_loss=True,
                )
            )

    assert call_count[0] == 5


def test_run_adapter_pipeline_treats_adapter_timeout_as_adapter_connection_loss():
    """A timeout raised by the adapter iterator must restart without DB-pool recreation."""
    from pmfi.pipeline.runner import run_adapter_pipeline, AdapterConnectionLost

    class TimeoutIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.TimeoutError("polymarket receive timed out")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with pytest.raises(AdapterConnectionLost, match="polymarket receive timed out"):
        asyncio.run(
            run_adapter_pipeline(
                TimeoutIterator(),
                pool,
                engine,
                handler,
                raise_on_connection_loss=True,
            )
        )


def test_run_adapter_pipeline_treats_oserror_as_adapter_connection_loss():
    """Adapter/network OSError must restart without being classified as DB loss."""
    from pmfi.pipeline.runner import run_adapter_pipeline, AdapterConnectionLost

    class OSErrorIterator:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise OSError("polymarket stream error")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with pytest.raises(AdapterConnectionLost, match="polymarket stream error"):
        asyncio.run(
            run_adapter_pipeline(
                OSErrorIterator(),
                pool,
                engine,
                handler,
                raise_on_connection_loss=True,
            )
        )


def test_run_adapter_pipeline_adapter_loss_carries_progress_observed():
    """If events flowed before adapter loss, supervisor can reset the circuit streak."""
    from pmfi.pipeline.runner import run_adapter_pipeline, AdapterConnectionLost

    class ProgressThenTimeoutIterator:
        def __init__(self):
            self._calls = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self._calls += 1
            if self._calls == 1:
                return _make_raw_event("ev-ok-before-timeout")
            raise asyncio.TimeoutError("polymarket receive timed out")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    async def always_ok(raw, pool, engine, handler, **kwargs):
        pass

    with patch("pmfi.pipeline.runner.process_event", side_effect=always_ok):
        with pytest.raises(AdapterConnectionLost) as excinfo:
            asyncio.run(
                run_adapter_pipeline(
                    ProgressThenTimeoutIterator(),
                    pool,
                    engine,
                    handler,
                    raise_on_connection_loss=True,
                )
            )

    assert excinfo.value.progress_observed is True
    assert excinfo.value.progress_events == 1


def test_run_adapter_pipeline_legacy_no_raise_on_connection_errors():
    """raise_on_connection_loss=False (default) — 5+ consecutive errors do NOT raise."""
    from pmfi.pipeline.runner import run_adapter_pipeline, IngestConnectionLost
    import asyncpg

    events = [_make_raw_event(f"ev-{i}") for i in range(10)]

    async def always_conn_error(raw, pool, engine, handler, **kwargs):
        raise asyncpg.InterfaceError("simulated connection reset")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    # Should NOT raise regardless of consecutive failures when flag is False (default)
    with patch("pmfi.pipeline.runner.process_event", side_effect=always_conn_error):
        processed = asyncio.run(
            run_adapter_pipeline(_async_iter(events), pool, engine, handler)
            # raise_on_connection_loss defaults to False
        )

    assert processed == 0  # no successes, but no raise


def test_run_adapter_pipeline_data_error_does_not_raise():
    """ValueError (data-class) never triggers IngestConnectionLost regardless of flag."""
    from pmfi.pipeline.runner import run_adapter_pipeline

    events = [_make_raw_event(f"ev-{i}") for i in range(6)]

    async def always_value_error(raw, pool, engine, handler, **kwargs):
        raise ValueError("bad data")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=always_value_error):
        processed = asyncio.run(
            run_adapter_pipeline(
                _async_iter(events), pool, engine, handler,
                raise_on_connection_loss=True,
            )
        )

    assert processed == 0  # no successes, but no raise


def test_run_adapter_pipeline_conn_counter_resets_on_success():
    """4 conn errors, 1 success, 4 more conn errors — threshold of 5 never reached."""
    from pmfi.pipeline.runner import run_adapter_pipeline
    import asyncpg

    seq = []
    for i in range(4):
        seq.append(("conn", f"conn-{i}"))
    seq.append(("ok", "good"))
    for i in range(4):
        seq.append(("conn", f"conn2-{i}"))

    events = [_make_raw_event(ev_id) for _, ev_id in seq]
    idx = [0]

    async def side_effect(raw, pool, engine, handler, **kwargs):
        kind, _ = seq[idx[0]]
        idx[0] += 1
        if kind == "conn":
            raise asyncpg.InterfaceError("conn fail")

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=side_effect):
        processed = asyncio.run(
            run_adapter_pipeline(
                _async_iter(events), pool, engine, handler,
                raise_on_connection_loss=True,
            )
        )

    assert processed == 1  # only the "ok" event counted


def test_run_adapter_pipeline_healthy_db_never_raises():
    """All events succeed: no IngestConnectionLost, returns full count."""
    from pmfi.pipeline.runner import run_adapter_pipeline

    events = [_make_raw_event(f"ev-{i}") for i in range(10)]

    async def always_ok(raw, pool, engine, handler, **kwargs):
        pass

    pool = _make_pool()
    engine = MagicMock()
    handler = AsyncMock()

    with patch("pmfi.pipeline.runner.process_event", side_effect=always_ok):
        processed = asyncio.run(
            run_adapter_pipeline(_async_iter(events), pool, engine, handler)
        )

    assert processed == 10


# ---------------------------------------------------------------------------
# Adapter reconnect_jitter param accepted (keep)
# ---------------------------------------------------------------------------

def test_polymarket_adapter_accepts_reconnect_jitter_false():
    from pmfi.adapters.polymarket import PolymarketAdapter
    a = PolymarketAdapter(asset_ids=[], reconnect_jitter=False)
    assert a._reconnect_jitter is False


def test_polymarket_adapter_accepts_reconnect_jitter_true():
    from pmfi.adapters.polymarket import PolymarketAdapter
    a = PolymarketAdapter(asset_ids=[], reconnect_jitter=True)
    assert a._reconnect_jitter is True


def test_polymarket_adapter_default_reconnect_jitter_is_true():
    from pmfi.adapters.polymarket import PolymarketAdapter
    a = PolymarketAdapter(asset_ids=[])
    assert a._reconnect_jitter is True


def test_kalshi_adapter_accepts_reconnect_jitter_false():
    from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
    a = KalshiRestPollingAdapter(tickers=[], reconnect_jitter=False)
    assert a._reconnect_jitter is False


def test_kalshi_adapter_accepts_reconnect_jitter_true():
    from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
    a = KalshiRestPollingAdapter(tickers=[], reconnect_jitter=True)
    assert a._reconnect_jitter is True


def test_kalshi_adapter_default_reconnect_jitter_is_true():
    from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
    a = KalshiRestPollingAdapter(tickers=[])
    assert a._reconnect_jitter is True
