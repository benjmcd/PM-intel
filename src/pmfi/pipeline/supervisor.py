"""Ingest supervisor: PoolManager with safe concurrent recreation, per-adapter restart loop.

This module is importable and testable independently of cli.py.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


class _TimedAcquire:
    def __init__(self, acquire_cm: Any, acquire_wait_stats: Any) -> None:
        self._acquire_cm = acquire_cm
        self._acquire_wait_stats = acquire_wait_stats

    async def __aenter__(self) -> Any:
        started = self._acquire_wait_stats.clock()
        conn = await self._acquire_cm.__aenter__()
        self._acquire_wait_stats.record_seconds(
            self._acquire_wait_stats.clock() - started
        )
        return conn

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return bool(await self._acquire_cm.__aexit__(exc_type, exc, tb))

    def __await__(self) -> Any:
        async def _await_acquire() -> Any:
            started = self._acquire_wait_stats.clock()
            conn = await self._acquire_cm
            self._acquire_wait_stats.record_seconds(
                self._acquire_wait_stats.clock() - started
            )
            return conn

        return _await_acquire().__await__()


class _TimedPoolProxy:
    def __init__(self, pool: Any, acquire_wait_stats: Any) -> None:
        self._pool = pool
        self._acquire_wait_stats = acquire_wait_stats

    def acquire(self, *args: Any, **kwargs: Any) -> _TimedAcquire:
        return _TimedAcquire(self._pool.acquire(*args, **kwargs), self._acquire_wait_stats)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)


# ---------------------------------------------------------------------------
# Jittered backoff
# ---------------------------------------------------------------------------

def jittered_backoff(base: float, jitter: bool) -> float:
    """Return a backoff delay.

    With jitter=True, the value is uniformly drawn from [base*0.5, base).
    With jitter=False, returns base unchanged.
    """
    if not jitter:
        return base
    return base * (0.5 + random.random() / 2)


# ---------------------------------------------------------------------------
# PoolManager
# ---------------------------------------------------------------------------

class PoolManager:
    """Holds a single asyncpg pool and supports safe concurrent recreation.

    Invariant: observers never see a closed/None pool between open() and close().
    Concurrent calls to recreate() with the same observed_generation produce
    exactly one underlying create call; subsequent callers get the already-new
    pool back immediately.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        acquire_wait_stats: Any = None,
        command_timeout: float | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._acquire_wait_stats = acquire_wait_stats
        self._command_timeout = command_timeout
        self._pool: Any = None  # asyncpg.Pool | None
        self._generation: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    def _wrap_pool(self, pool: Any) -> Any:
        if self._acquire_wait_stats is None:
            return pool
        return _TimedPoolProxy(pool, self._acquire_wait_stats)

    async def open(self) -> Any:
        """Create the initial pool and return it."""
        from pmfi.db import create_pool_with_retry
        raw_pool = await create_pool_with_retry(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=self._command_timeout,
        )
        self._pool = self._wrap_pool(raw_pool)
        return self._pool

    @property
    def pool(self) -> Any:
        """Current pool.  Never None after open() and before close()."""
        return self._pool

    @property
    def generation(self) -> int:
        """Monotonically-increasing integer, bumped on each successful recreate."""
        return self._generation

    async def recreate(self, observed_generation: int) -> Any:
        """Safely recreate the pool.

        If another caller already recreated since observed_generation was captured
        (i.e. self._generation != observed_generation), this is a NO-OP and the
        current (new) pool is returned immediately — no second create call.

        If creation of the new pool fails, the old pool is left in place and the
        exception is re-raised so the caller can back off and retry.

        On success: new pool is swapped in, generation is bumped, OLD pool is
        closed best-effort AFTER the swap (so observers never see a closed pool).
        """
        from pmfi.db import create_pool_with_retry, close_pool
        async with self._lock:
            if observed_generation != self._generation:
                # Another caller already recreated — return the current pool.
                return self._pool
            old_pool = self._pool
            # May raise — if so, leave old pool in place.
            raw_new_pool = await create_pool_with_retry(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                command_timeout=self._command_timeout,
            )
            new_pool = self._wrap_pool(raw_new_pool)
            self._pool = new_pool
            self._generation += 1
        # Close old pool OUTSIDE the lock (best-effort) so other callers are
        # unblocked immediately after the swap.
        if old_pool is not None:
            try:
                await close_pool(old_pool)
            except Exception as exc:
                logger.warning("PoolManager: error closing old pool after recreate: %s", exc)
        return new_pool

    async def close(self) -> None:
        """Close the pool once and set _pool to None."""
        from pmfi.db import close_pool
        async with self._lock:
            pool = self._pool
            self._pool = None
        if pool is not None:
            try:
                await close_pool(pool)
            except Exception as exc:
                logger.warning("PoolManager: error closing pool: %s", exc)


# ---------------------------------------------------------------------------
# supervise
# ---------------------------------------------------------------------------

# Connection-class exceptions that warrant a pool recreation + adapter restart.
# OSError and asyncio.TimeoutError are intentionally EXCLUDED: they can fire for
# reasons unrelated to DB connectivity (socket timeouts to venue APIs, DNS, etc.)
# and cause false pool teardowns of otherwise-healthy connections.
_CONN_EXCEPTIONS: tuple[type[BaseException], ...] = ()

def _connection_exception_types() -> tuple[type[BaseException], ...]:
    """Return the tuple of exception types that indicate a lost DB connection.

    Imported lazily so asyncpg is not required at module-import time.
    """
    global _CONN_EXCEPTIONS  # noqa: PLW0603
    if _CONN_EXCEPTIONS:
        return _CONN_EXCEPTIONS
    import asyncpg
    from pmfi.pipeline.runner import IngestConnectionLost
    _CONN_EXCEPTIONS = (
        IngestConnectionLost,
        asyncpg.PostgresConnectionError,
        asyncpg.InterfaceError,
        ConnectionResetError,
    )
    return _CONN_EXCEPTIONS


async def supervise(
    name: str,
    make_adapter: Callable[[], Any],
    run_one: Callable[[Any, "PoolManager"], Awaitable[None]],
    *,
    shutdown: asyncio.Event,
    pool_manager: "PoolManager",
    initial_backoff: float = 1.0,
    max_backoff: float = 60.0,
    jitter: bool = True,
    status_map: "dict | None" = None,
    circuit_breaker_failure_threshold: int = 0,
    circuit_breaker_window_seconds: float = 300.0,
    circuit_breaker_recovery_seconds: float = 60.0,
    circuit_breaker_progress_reset_min_events: int = 2,
    monotonic: Callable[[], float] | None = None,
) -> None:
    """Per-adapter supervised restart loop with jittered backoff.

    Runs until shutdown is set.  On connection-class errors, asks pool_manager
    to recreate the pool (idempotent across concurrent supervisors).  On
    CancelledError, re-raises immediately.  On other exceptions, logs and backs off.

    run_one(adapter, pool_manager) must dereference pool_manager.pool at call time.

    Optional *status_map*: if provided, a shared mutable dict keyed by venue name
    into which supervise writes per-venue health info after each failure::

        status_map[name] = {
            "consecutive_failures": int,
            "last_error": str | None,
            "circuit_open": bool,
            "failure_window_seconds": float,
        }

    A clean run or sufficiently productive stream reset consecutive_failures
    to 0 so routine live-stream reconnects do not accumulate into a false
    circuit-open. Tiny trickle progress below circuit_breaker_progress_reset_min_events
    remains eligible to open the circuit. When circuit_breaker_failure_threshold
    is >0, a sustained failure window opens the circuit, surfaces that state,
    waits circuit_breaker_recovery_seconds, then retries in half-open mode
    instead of wedging until daemon restart.
    """
    from pmfi.pipeline.runner import AdapterConnectionLost

    conn_exc_types = _connection_exception_types()
    base = initial_backoff
    consecutive_failures = 0
    first_failure_at: float | None = None
    trickle_reconnect_failures = 0
    first_trickle_failure_at: float | None = None
    clock = monotonic or time.monotonic

    def _write_status(
        *,
        last_error: str | None,
        circuit_open: bool = False,
        failure_window_seconds: float | None = None,
    ) -> None:
        if status_map is None:
            return
        payload = {
            "consecutive_failures": consecutive_failures,
            "trickle_reconnect_failures": trickle_reconnect_failures,
            "last_error": last_error,
            "circuit_open": circuit_open,
        }
        if failure_window_seconds is not None:
            payload["failure_window_seconds"] = max(0.0, failure_window_seconds)
        status_map[name] = payload

    def _record_failure(exc: BaseException) -> bool:
        nonlocal consecutive_failures, first_failure_at
        now = clock()
        consecutive_failures += 1
        if first_failure_at is None:
            first_failure_at = now
        elapsed = now - first_failure_at
        threshold = int(circuit_breaker_failure_threshold)
        circuit_open = (
            threshold > 0
            and consecutive_failures >= threshold
            and elapsed >= float(circuit_breaker_window_seconds)
        )
        _write_status(
            last_error=str(exc),
            circuit_open=circuit_open,
            failure_window_seconds=elapsed,
        )
        return circuit_open

    def _reset_trickle_streak() -> None:
        nonlocal trickle_reconnect_failures, first_trickle_failure_at
        trickle_reconnect_failures = 0
        first_trickle_failure_at = None

    def _reset_failure_streak(*, reset_trickle: bool = True) -> None:
        nonlocal consecutive_failures, first_failure_at, base
        consecutive_failures = 0
        first_failure_at = None
        base = initial_backoff
        if reset_trickle:
            _reset_trickle_streak()

    def _progress_events(exc: BaseException) -> int:
        raw_events = getattr(exc, "progress_events", None)
        if raw_events is None:
            return 1 if getattr(exc, "progress_observed", False) else 0
        return max(0, int(raw_events))

    def _reset_after_progress(
        exc: BaseException,
        *,
        reset_trickle: bool = True,
    ) -> None:
        min_events = max(1, int(circuit_breaker_progress_reset_min_events))
        if _progress_events(exc) >= min_events:
            _reset_failure_streak(reset_trickle=reset_trickle)

    def _record_trickle_reconnect(exc: BaseException) -> bool:
        nonlocal trickle_reconnect_failures, first_trickle_failure_at
        progress_events = _progress_events(exc)
        min_events = max(1, int(circuit_breaker_progress_reset_min_events))
        if progress_events <= 0:
            return False
        if progress_events > min_events:
            _reset_trickle_streak()
            return False
        now = clock()
        trickle_reconnect_failures += 1
        if first_trickle_failure_at is None:
            first_trickle_failure_at = now
        elapsed = now - first_trickle_failure_at
        threshold = int(circuit_breaker_failure_threshold)
        circuit_open = (
            threshold > 0
            and trickle_reconnect_failures >= threshold
            and elapsed >= float(circuit_breaker_window_seconds)
        )
        if circuit_open:
            _write_status(
                last_error=str(exc),
                circuit_open=True,
                failure_window_seconds=elapsed,
            )
        return circuit_open

    async def _wait_for_half_open(reason: str) -> bool:
        recovery_seconds = max(0.0, float(circuit_breaker_recovery_seconds))
        logger.error(
            "[ingest:%s] Circuit opened after sustained %s failures; "
            "half-open retry in %.1fs",
            name,
            reason,
            recovery_seconds,
        )
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=recovery_seconds)
        except asyncio.TimeoutError:
            pass
        if shutdown.is_set():
            return False
        _reset_failure_streak()
        _write_status(
            last_error=f"half-open retry after {reason} circuit cooldown",
            circuit_open=False,
            failure_window_seconds=0.0,
        )
        return True

    while not shutdown.is_set():
        observed_gen = pool_manager.generation
        adapter = make_adapter()
        _ran_clean = False
        circuit_opened_reason: str | None = None
        try:
            await adapter.connect()
            await run_one(adapter, pool_manager)
            _ran_clean = True
        except conn_exc_types as conn_exc:
            logger.warning("[ingest:%s] DB connection lost, recreating pool: %s", name, conn_exc)
            # DB-path progress is remediated by pool recreation below. The
            # trickle reconnect counter is intentionally scoped to adapter churn
            # where the venue stream keeps reconnecting but only makes floor
            # progress before dropping again.
            _reset_after_progress(conn_exc)
            circuit_open = _record_failure(conn_exc)
            try:
                await pool_manager.recreate(observed_gen)
            except Exception as recreate_exc:
                logger.error("[ingest:%s] Pool recreate failed (will retry): %s", name, recreate_exc)
            if circuit_open:
                circuit_opened_reason = "DB connection"
        except AdapterConnectionLost as adapter_exc:
            logger.warning(
                "[ingest:%s] Adapter connection lost, restarting without pool recreate: %s",
                name,
                adapter_exc,
            )
            adapter_progress = _progress_events(adapter_exc)
            min_progress = max(1, int(circuit_breaker_progress_reset_min_events))
            _reset_after_progress(
                adapter_exc,
                reset_trickle=adapter_progress > min_progress,
            )
            circuit_open = _record_failure(adapter_exc)
            trickle_open = _record_trickle_reconnect(adapter_exc)
            if circuit_open or trickle_open:
                circuit_opened_reason = "adapter"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ingest:%s] Adapter error: %s", name, exc)
            adapter_progress = _progress_events(exc)
            min_progress = max(1, int(circuit_breaker_progress_reset_min_events))
            _reset_after_progress(
                exc,
                reset_trickle=adapter_progress > min_progress,
            )
            circuit_open = _record_failure(exc)
            trickle_open = _record_trickle_reconnect(exc)
            if circuit_open or trickle_open:
                circuit_opened_reason = "adapter"
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass

        # Reset backoff after a clean run so only *consecutive* failures cause
        # exponential growth.  This also records clean terminal shutdowns.
        if _ran_clean:
            _reset_failure_streak()
            _write_status(last_error=None, circuit_open=False)

        if shutdown.is_set():
            break

        if circuit_opened_reason is not None:
            if not await _wait_for_half_open(circuit_opened_reason):
                break
            continue

        delay = jittered_backoff(base, jitter)
        logger.info("[ingest:%s] Restarting in %.1fs", name, delay)
        base = min(base * 2, max_backoff)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
