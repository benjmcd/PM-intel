"""Gap 2: supervise() generic-exception path.

Confirms that a ValueError (non-connection exception) from run_one causes:
- adapter restart (make_adapter called >= 2 times)
- pool NOT recreated (no IngestConnectionLost)
- status_map records consecutive_failures and last_error
- backoff doubles on consecutive generic failures, resets after a clean run
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_manager():
    from pmfi.pipeline.supervisor import PoolManager
    pm = PoolManager("fake_dsn")
    pm._pool = MagicMock()
    pm._pool.close = AsyncMock()
    return pm


def _make_adapter():
    a = MagicMock()
    a.connect = AsyncMock()
    a.disconnect = AsyncMock()
    return a


class TestSuperviseGenericException:
    """Generic (non-connection) exception from run_one."""

    def test_adapter_restarts_after_generic_exception(self):
        """ValueError from run_one causes make_adapter to be called again (>= 2)."""
        from pmfi.pipeline.supervisor import supervise

        shutdown = asyncio.Event()
        make_count = [0]
        run_count = [0]

        def make_adapter():
            make_count[0] += 1
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise ValueError("bad data from adapter")
            shutdown.set()

        pm = _make_pool_manager()

        async def _drive():
            await asyncio.wait_for(
                supervise(
                    "test", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.0, max_backoff=0.0, jitter=False,
                ),
                timeout=5.0,
            )

        asyncio.run(_drive())
        assert make_count[0] >= 2, f"Expected >= 2 make_adapter calls, got {make_count[0]}"

    def test_pool_NOT_recreated_on_generic_exception(self):
        """ValueError must NOT trigger pool_manager.recreate — that is only for conn errors."""
        from pmfi.pipeline.supervisor import supervise

        shutdown = asyncio.Event()
        run_count = [0]
        recreate_calls = [0]

        def make_adapter():
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise ValueError("generic error, not a DB issue")
            shutdown.set()

        pm = _make_pool_manager()

        async def _fake_recreate(observed_gen):
            recreate_calls[0] += 1
            pm._generation += 1
            return pm._pool

        async def _drive():
            with patch.object(pm, "recreate", side_effect=_fake_recreate):
                await asyncio.wait_for(
                    supervise(
                        "test", make_adapter, run_one,
                        shutdown=shutdown, pool_manager=pm,
                        initial_backoff=0.0, max_backoff=0.0, jitter=False,
                    ),
                    timeout=5.0,
                )

        asyncio.run(_drive())
        assert recreate_calls[0] == 0, (
            f"recreate must NOT be called for ValueError, got {recreate_calls[0]} call(s)"
        )

    def test_status_map_records_consecutive_failures_and_last_error(self):
        """status_map[name] contains consecutive_failures and last_error after generic exc."""
        from pmfi.pipeline.supervisor import supervise

        status_map: dict = {}
        shutdown = asyncio.Event()
        run_count = [0]
        captured: list[dict] = []

        def make_adapter():
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise ValueError("oops value error")
            # Capture status before clean-run reset
            captured.append(dict(status_map.get("test", {})))
            shutdown.set()

        pm = _make_pool_manager()

        async def _drive():
            await asyncio.wait_for(
                supervise(
                    "test", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.0, max_backoff=0.0, jitter=False,
                    status_map=status_map,
                ),
                timeout=5.0,
            )

        asyncio.run(_drive())
        assert captured, "second run_one never reached"
        s = captured[0]
        assert s["consecutive_failures"] == 1, f"Expected 1, got {s['consecutive_failures']}"
        assert "oops value error" in s["last_error"]

    def test_status_map_reset_after_clean_run(self):
        """After a clean run that does NOT immediately set shutdown, consecutive_failures resets.

        The supervise() reset path (lines 227-234) fires only when shutdown is NOT set
        at the top of the restart block (line 222-223).  If run_one itself calls
        shutdown.set() and returns cleanly, the loop breaks before the reset — the
        failure record from a prior iteration remains.

        This test validates the reset by letting the clean run return WITHOUT setting
        shutdown, then setting it on the NEXT call so the reset fires between runs.
        """
        from pmfi.pipeline.supervisor import supervise

        status_map: dict = {}
        shutdown = asyncio.Event()
        run_count = [0]

        def make_adapter():
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise ValueError("transient generic error")
            if run_count[0] == 2:
                # Clean run — return without setting shutdown so the reset fires
                return
            # Third call: now shut down
            shutdown.set()

        pm = _make_pool_manager()

        async def _drive():
            await asyncio.wait_for(
                supervise(
                    "test", make_adapter, run_one,
                    shutdown=shutdown, pool_manager=pm,
                    initial_backoff=0.0, max_backoff=0.0, jitter=False,
                    status_map=status_map,
                ),
                timeout=5.0,
            )

        asyncio.run(_drive())
        # After the clean run (run 2) returned without setting shutdown, the reset
        # fires before the next loop iteration — consecutive_failures must be 0.
        final = status_map.get("test", {})
        assert final["consecutive_failures"] == 0
        assert final["last_error"] is None

    def test_backoff_doubles_on_consecutive_generic_failures(self):
        """Two consecutive ValueError failures double the backoff base."""
        from pmfi.pipeline.supervisor import supervise

        shutdown = asyncio.Event()
        run_count = [0]
        delays_used: list[float] = []

        def make_adapter():
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] < 3:
                raise ValueError(f"generic fail {run_count[0]}")
            shutdown.set()

        pm = _make_pool_manager()

        def capturing_backoff(base: float, jitter: bool) -> float:
            delays_used.append(base)
            return 0.0  # instant so test runs fast

        async def _drive():
            with patch("pmfi.pipeline.supervisor.jittered_backoff", side_effect=capturing_backoff):
                await asyncio.wait_for(
                    supervise(
                        "test", make_adapter, run_one,
                        shutdown=shutdown, pool_manager=pm,
                        initial_backoff=1.0, max_backoff=60.0, jitter=False,
                    ),
                    timeout=5.0,
                )

        asyncio.run(_drive())
        # 2 failures before clean exit → delays [1.0, 2.0]
        assert len(delays_used) >= 2, f"Expected >= 2 delay calls, got {delays_used}"
        assert delays_used[0] == 1.0
        assert delays_used[1] == 2.0

    def test_backoff_resets_after_clean_run_following_generic_failure(self):
        """After fault + clean run, next delay uses initial_backoff again."""
        from pmfi.pipeline.supervisor import supervise

        shutdown = asyncio.Event()
        run_count = [0]
        delays_used: list[float] = []

        def make_adapter():
            return _make_adapter()

        async def run_one(adapter, pm):
            run_count[0] += 1
            if run_count[0] == 1:
                raise ValueError("transient")
            if run_count[0] == 2:
                return  # clean run
            shutdown.set()

        pm = _make_pool_manager()

        def capturing_backoff(base: float, jitter: bool) -> float:
            delays_used.append(base)
            return 0.0

        async def _drive():
            with patch("pmfi.pipeline.supervisor.jittered_backoff", side_effect=capturing_backoff):
                await asyncio.wait_for(
                    supervise(
                        "test", make_adapter, run_one,
                        shutdown=shutdown, pool_manager=pm,
                        initial_backoff=1.0, max_backoff=60.0, jitter=False,
                    ),
                    timeout=5.0,
                )

        asyncio.run(_drive())
        assert len(delays_used) >= 2
        assert delays_used[0] == 1.0, f"After fault: expected 1.0, got {delays_used[0]}"
        assert delays_used[1] == 1.0, f"After clean run: expected reset to 1.0, got {delays_used[1]}"
