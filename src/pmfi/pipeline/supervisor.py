"""Ingest supervisor: PoolManager with safe concurrent recreation, per-adapter restart loop.

This module is importable and testable independently of cli.py.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any = None  # asyncpg.Pool | None
        self._generation: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def open(self) -> Any:
        """Create the initial pool and return it."""
        from pmfi.db import create_pool_with_retry
        self._pool = await create_pool_with_retry(
            self._dsn, min_size=self._min_size, max_size=self._max_size
        )
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
            new_pool = await create_pool_with_retry(
                self._dsn, min_size=self._min_size, max_size=self._max_size
            )
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
) -> None:
    """Per-adapter supervised restart loop with jittered backoff.

    Runs until shutdown is set.  On connection-class errors, asks pool_manager
    to recreate the pool (idempotent across concurrent supervisors).  On
    CancelledError, re-raises immediately.  On other exceptions, logs and backs off.

    run_one(adapter, pool_manager) must dereference pool_manager.pool at call time.
    """
    conn_exc_types = _connection_exception_types()
    base = initial_backoff

    while not shutdown.is_set():
        observed_gen = pool_manager.generation
        adapter = make_adapter()
        try:
            await adapter.connect()
            await run_one(adapter, pool_manager)
        except conn_exc_types as conn_exc:
            print(f"[ingest:{name}] DB connection lost, recreating pool: {conn_exc}")
            try:
                await pool_manager.recreate(observed_gen)
            except Exception as recreate_exc:
                print(f"[ingest:{name}] Pool recreate failed (will retry): {recreate_exc}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[ingest:{name}] Adapter error: {exc}")
        finally:
            try:
                await adapter.disconnect()
            except Exception:
                pass

        if shutdown.is_set():
            break

        delay = jittered_backoff(base, jitter)
        print(f"[ingest:{name}] Restarting in {delay:.1f}s")
        base = min(base * 2, max_backoff)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
