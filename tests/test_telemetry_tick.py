"""Offline integration-style tests for _telemetry_tick (Gap 1).

Drives the real _telemetry_tick coroutine for 2+ simulated cycles, asserting:
- heartbeat written every cycle (with venues payload + recompute fields)
- baselines reloaded on the 10-cycle boundary (engine.update_baselines called)
- recompute invoked when enabled and cycle matches (and NOT when disabled)
- subscription refresh on its boundary
- partition maintenance on its boundary
- an exception from any inner helper does NOT propagate out of a tick

No DB, no network. All helpers are injected mocks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pmfi.commands.daemon import _telemetry_tick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_now():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _base_kwargs(tmp_path: Path, *, cycle: int = 1) -> dict:
    """Return a fully-specified kwargs dict for _telemetry_tick with safe mocks."""
    recompute_state = {
        "last_recompute_at": None,
        "last_recompute_ok": None,
        "last_recompute_error": None,
    }
    return dict(
        cycle=cycle,
        events_total=5,
        alerts_total=1,
        delta=3,
        interval=60,
        hb_path=tmp_path / "hb.json",
        write_heartbeat=MagicMock(),
        started_at=_make_now(),
        build_venues_payload=MagicMock(return_value={"polymarket": {"events_total": 5}}),
        recompute_state=recompute_state,
        recompute_enabled=True,
        recompute_cycles=10,
        safe_recompute_baselines=AsyncMock(return_value=(2, None)),
        pool=MagicMock(),
        window_days=30,
        min_samples=10,
        baseline_refresh_cycles=10,
        load_baselines=AsyncMock(return_value={"mkt:a": {}, "mkt:b": {}}),
        engine=MagicMock(),
        map_refresh_cycles=10,
        refresh_subscriptions=AsyncMock(return_value=(["tok-1"], ["KX-BTC"])),
        asset_id_map={"tok-1": {}},
        current_poly_ids=["tok-1"],
        current_kalshi_tickers=["KX-BTC"],
        partition_maint_cycles=1440,
        ensure_partitions=AsyncMock(),
        find_old_partitions=AsyncMock(return_value=[]),
        raw_retention_days=90,
        now_utc=_make_now,
    )


# ---------------------------------------------------------------------------
# Core: heartbeat written on every cycle
# ---------------------------------------------------------------------------

class TestTelemetryTickHeartbeat:
    def test_heartbeat_written_on_cycle_1(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        asyncio.run(_telemetry_tick(**kw))
        kw["write_heartbeat"].assert_called_once()

    def test_heartbeat_written_on_cycle_2(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=2)
        asyncio.run(_telemetry_tick(**kw))
        kw["write_heartbeat"].assert_called_once()

    def test_heartbeat_called_with_venues_payload(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        asyncio.run(_telemetry_tick(**kw))
        _, call_kw = kw["write_heartbeat"].call_args
        assert "venues" in call_kw
        assert call_kw["venues"] == {"polymarket": {"events_total": 5}}

    def test_heartbeat_called_with_recompute_fields(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        # Pre-populate recompute_state to verify fields flow through
        kw["recompute_state"]["last_recompute_at"] = "2026-01-01T00:00:00+00:00"
        kw["recompute_state"]["last_recompute_ok"] = True
        kw["recompute_state"]["last_recompute_error"] = None
        asyncio.run(_telemetry_tick(**kw))
        _, call_kw = kw["write_heartbeat"].call_args
        assert call_kw["last_recompute_at"] == "2026-01-01T00:00:00+00:00"
        assert call_kw["last_recompute_ok"] is True
        assert call_kw["last_recompute_error"] is None

    def test_heartbeat_called_with_events_and_alerts(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["events_total"] = 42
        kw["alerts_total"] = 7
        asyncio.run(_telemetry_tick(**kw))
        _, call_kw = kw["write_heartbeat"].call_args
        assert call_kw["events_total"] == 42
        assert call_kw["alerts_total"] == 7

    def test_two_cycles_write_heartbeat_twice(self, tmp_path):
        """Simulate two consecutive cycles; heartbeat called once per cycle."""
        for cycle in (1, 2):
            kw = _base_kwargs(tmp_path, cycle=cycle)
            asyncio.run(_telemetry_tick(**kw))
            kw["write_heartbeat"].assert_called_once()


# ---------------------------------------------------------------------------
# Baseline reload: engine.update_baselines called on the 10-cycle boundary
# ---------------------------------------------------------------------------

class TestTelemetryTickBaselineRefresh:
    def test_engine_updated_on_cycle_10(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        fresh = {"mkt:a": {}, "mkt:b": {}}
        kw["load_baselines"] = AsyncMock(return_value=fresh)
        asyncio.run(_telemetry_tick(**kw))
        kw["engine"].update_baselines.assert_called_once_with(fresh)

    def test_engine_updated_on_cycle_20(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=20)
        asyncio.run(_telemetry_tick(**kw))
        kw["engine"].update_baselines.assert_called_once()

    def test_engine_NOT_updated_on_cycle_5(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=5)
        asyncio.run(_telemetry_tick(**kw))
        kw["engine"].update_baselines.assert_not_called()

    def test_engine_NOT_updated_on_cycle_1(self, tmp_path):
        # cycle 1 is a maintenance cycle but baseline_refresh uses %, not _is_maintenance_cycle
        kw = _base_kwargs(tmp_path, cycle=1)
        asyncio.run(_telemetry_tick(**kw))
        kw["engine"].update_baselines.assert_not_called()

    def test_load_baselines_failure_is_non_fatal(self, tmp_path):
        """load_baselines raising must not propagate out of tick."""
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["load_baselines"] = AsyncMock(side_effect=RuntimeError("db down"))
        # Must complete without raising
        asyncio.run(_telemetry_tick(**kw))
        # Engine should not have been updated
        kw["engine"].update_baselines.assert_not_called()


# ---------------------------------------------------------------------------
# Recompute: invoked when enabled + cycle matches; NOT when disabled
# ---------------------------------------------------------------------------

class TestTelemetryTickRecompute:
    def test_recompute_called_on_cycle_1_when_enabled(self, tmp_path):
        # _is_maintenance_cycle fires on cycle 1 AND every recompute_cycles
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["recompute_enabled"] = True
        asyncio.run(_telemetry_tick(**kw))
        kw["safe_recompute_baselines"].assert_awaited_once()

    def test_recompute_called_on_exact_cycle_boundary(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["recompute_enabled"] = True
        asyncio.run(_telemetry_tick(**kw))
        kw["safe_recompute_baselines"].assert_awaited_once()

    def test_recompute_NOT_called_when_disabled(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["recompute_enabled"] = False
        asyncio.run(_telemetry_tick(**kw))
        kw["safe_recompute_baselines"].assert_not_called()

    def test_recompute_NOT_called_when_disabled_on_boundary(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["recompute_enabled"] = False
        asyncio.run(_telemetry_tick(**kw))
        kw["safe_recompute_baselines"].assert_not_called()

    def test_recompute_NOT_called_on_non_boundary_cycle(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=7)  # not 1, not multiple of 10
        kw["recompute_enabled"] = True
        asyncio.run(_telemetry_tick(**kw))
        kw["safe_recompute_baselines"].assert_not_called()

    def test_recompute_state_updated_on_success(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["recompute_enabled"] = True
        kw["safe_recompute_baselines"] = AsyncMock(return_value=(3, None))
        asyncio.run(_telemetry_tick(**kw))
        rs = kw["recompute_state"]
        assert rs["last_recompute_ok"] is True
        assert rs["last_recompute_error"] is None
        assert rs["last_recompute_at"] is not None

    def test_recompute_state_updated_on_failure(self, tmp_path):
        """safe_recompute_baselines returns (None, err_str) on failure; state recorded."""
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["recompute_enabled"] = True
        kw["safe_recompute_baselines"] = AsyncMock(return_value=(None, "db gone"))
        asyncio.run(_telemetry_tick(**kw))
        rs = kw["recompute_state"]
        assert rs["last_recompute_ok"] is False
        assert rs["last_recompute_error"] == "db gone"


# ---------------------------------------------------------------------------
# Subscription refresh on its boundary
# ---------------------------------------------------------------------------

class TestTelemetryTickSubscriptionRefresh:
    def test_refresh_called_on_cycle_10(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        asyncio.run(_telemetry_tick(**kw))
        kw["refresh_subscriptions"].assert_awaited_once()

    def test_refresh_NOT_called_on_cycle_5(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=5)
        asyncio.run(_telemetry_tick(**kw))
        kw["refresh_subscriptions"].assert_not_called()

    def test_refresh_updates_mutable_lists(self, tmp_path):
        """current_poly_ids and current_kalshi_tickers lists updated in place."""
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["current_poly_ids"] = ["old-tok"]
        kw["current_kalshi_tickers"] = ["OLD-KX"]
        kw["refresh_subscriptions"] = AsyncMock(return_value=(["new-tok-1", "new-tok-2"], ["NEW-KX"]))
        asyncio.run(_telemetry_tick(**kw))
        assert kw["current_poly_ids"] == ["new-tok-1", "new-tok-2"]
        assert kw["current_kalshi_tickers"] == ["NEW-KX"]

    def test_refresh_failure_is_non_fatal(self, tmp_path):
        """refresh_subscriptions raising must not propagate out of tick."""
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["refresh_subscriptions"] = AsyncMock(side_effect=RuntimeError("net error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise


# ---------------------------------------------------------------------------
# Partition maintenance on its boundary
# ---------------------------------------------------------------------------

class TestTelemetryTickPartitionMaintenance:
    def test_ensure_partitions_called_on_cycle_1(self, tmp_path):
        # _is_maintenance_cycle fires on cycle 1
        kw = _base_kwargs(tmp_path, cycle=1)
        asyncio.run(_telemetry_tick(**kw))
        kw["ensure_partitions"].assert_awaited_once()

    def test_ensure_partitions_NOT_called_on_cycle_2(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=2)  # not 1, not multiple of 1440
        asyncio.run(_telemetry_tick(**kw))
        kw["ensure_partitions"].assert_not_called()

    def test_ensure_partitions_called_on_1440_boundary(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1440)
        asyncio.run(_telemetry_tick(**kw))
        kw["ensure_partitions"].assert_awaited_once()

    def test_partition_failure_is_non_fatal(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["ensure_partitions"] = AsyncMock(side_effect=RuntimeError("partition error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_old_partitions_warning_logged(self, tmp_path, caplog):
        import logging
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["find_old_partitions"] = AsyncMock(return_value=["raw_events_2024_01", "raw_events_2024_02"])
        with caplog.at_level(logging.WARNING, logger="pmfi.commands.daemon"):
            asyncio.run(_telemetry_tick(**kw))
        assert any("WARNING" in r.message for r in caplog.records)

    def test_no_warning_when_no_old_partitions(self, tmp_path, caplog):
        import logging
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["find_old_partitions"] = AsyncMock(return_value=[])
        with caplog.at_level(logging.WARNING, logger="pmfi.commands.daemon"):
            asyncio.run(_telemetry_tick(**kw))
        partition_warnings = [r for r in caplog.records if "partition" in r.message.lower() and "WARNING" in r.message]
        assert len(partition_warnings) == 0

    def test_find_old_partitions_failure_is_non_fatal(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["find_old_partitions"] = AsyncMock(side_effect=RuntimeError("query error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise


# ---------------------------------------------------------------------------
# Non-fatal contract: exceptions from any helper must NOT propagate
# ---------------------------------------------------------------------------

class TestTelemetryTickNonFatal:
    def test_write_heartbeat_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["write_heartbeat"] = MagicMock(side_effect=OSError("disk full"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_recompute_raising_directly_does_not_propagate(self, tmp_path):
        """safe_recompute_baselines itself raising (not returning error tuple) is still non-fatal."""
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["recompute_enabled"] = True
        # _safe_recompute_baselines is designed to return (n, err) and never raise,
        # but the tick guards it anyway: a helper bug must not kill the daemon.
        kw["safe_recompute_baselines"] = AsyncMock(side_effect=RuntimeError("unexpected raise"))
        recompute_state = kw["recompute_state"]
        asyncio.run(_telemetry_tick(**kw))  # must not raise
        assert recompute_state["last_recompute_ok"] is False
        assert "unexpected raise" in recompute_state["last_recompute_error"]

    def test_load_baselines_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["load_baselines"] = AsyncMock(side_effect=RuntimeError("db error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_refresh_subscriptions_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["refresh_subscriptions"] = AsyncMock(side_effect=RuntimeError("net error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_ensure_partitions_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["ensure_partitions"] = AsyncMock(side_effect=RuntimeError("maint error"))
        asyncio.run(_telemetry_tick(**kw))  # must not raise


class TestTelemetryTickMonitorFlags:
    def test_cross_venue_flag_flows_to_run_monitors(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=1)
        kw["cross_venue_enabled"] = True
        mock_monitors = AsyncMock()
        kw["run_monitors"] = mock_monitors

        asyncio.run(_telemetry_tick(**kw))

        mock_monitors.assert_awaited_once()
        assert mock_monitors.await_args.kwargs["cross_venue_enabled"] is True


class TestTelemetryTickOrderbookPolling:
    def test_orderbook_poll_flag_flows_to_poller(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["orderbook_poll_enabled"] = True
        kw["orderbook_poll_cycles"] = 10
        kw["current_poly_ids"] = ["tok-1"]
        kw["asset_id_map"] = {"tok-1": {"venue_code": "polymarket", "market_id": "mkt-1"}}
        kw["alert_handler"] = AsyncMock()
        poller = AsyncMock(return_value=type("PollResult", (), {
            "attempted": 1,
            "fetched": 1,
            "snapshots": 1,
            "alerts": 0,
            "skipped": 0,
        })())
        kw["poll_orderbooks"] = poller

        asyncio.run(_telemetry_tick(**kw))

        poller.assert_awaited_once()
        assert poller.await_args.kwargs["token_ids"] == ("tok-1",)
        assert poller.await_args.kwargs["asset_id_map"] is kw["asset_id_map"]
        assert poller.await_args.kwargs["alert_handler"] is kw["alert_handler"]

    def test_orderbook_poll_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["orderbook_poll_enabled"] = True
        kw["orderbook_poll_cycles"] = 10
        kw["poll_orderbooks"] = AsyncMock(side_effect=RuntimeError("book poll down"))

        asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_orderbook_poll_skips_when_not_poll_cycle(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=2)
        kw["orderbook_poll_enabled"] = True
        kw["orderbook_poll_cycles"] = 10
        poller = AsyncMock()
        kw["poll_orderbooks"] = poller

        asyncio.run(_telemetry_tick(**kw))

        poller.assert_not_called()

    def test_kalshi_orderbook_poll_flag_flows_to_poller(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["kalshi_orderbook_poll_enabled"] = True
        kw["orderbook_poll_cycles"] = 10
        kw["current_kalshi_tickers"] = ["KX-TEST"]
        kw["refresh_subscriptions"] = AsyncMock(return_value=(["tok-1"], ["KX-TEST"]))
        kw["alert_handler"] = AsyncMock()
        poller = AsyncMock(return_value=type("PollResult", (), {
            "attempted": 1,
            "fetched": 1,
            "snapshots": 2,
            "alerts": 0,
            "skipped": 0,
        })())
        kw["poll_kalshi_orderbooks"] = poller

        asyncio.run(_telemetry_tick(**kw))

        poller.assert_awaited_once()
        assert poller.await_args.kwargs["tickers"] == ("KX-TEST",)
        assert poller.await_args.kwargs["alert_handler"] is kw["alert_handler"]

    def test_kalshi_orderbook_poll_exception_does_not_propagate(self, tmp_path):
        kw = _base_kwargs(tmp_path, cycle=10)
        kw["kalshi_orderbook_poll_enabled"] = True
        kw["poll_kalshi_orderbooks"] = AsyncMock(side_effect=RuntimeError("kalshi book down"))

        asyncio.run(_telemetry_tick(**kw))  # must not raise


# ---------------------------------------------------------------------------
# Multi-cycle integration: run 2+ consecutive cycles end-to-end
# ---------------------------------------------------------------------------

class TestTelemetryTickMultiCycle:
    """Simulate the while loop running _telemetry_tick for cycles 1, 2, 10."""

    def _run_cycles(self, tmp_path: Path, cycles: list[int]) -> dict:
        """Run _telemetry_tick for each cycle number; share state objects across calls."""
        recompute_state = {
            "last_recompute_at": None,
            "last_recompute_ok": None,
            "last_recompute_error": None,
        }
        write_hb = MagicMock()
        load_bl = AsyncMock(return_value={"mkt:x": {}})
        engine = MagicMock()
        safe_rc = AsyncMock(return_value=(1, None))
        refresh_subs = AsyncMock(return_value=(["tok-1"], []))
        ensure_part = AsyncMock()
        find_old = AsyncMock(return_value=[])
        current_poly = ["tok-0"]
        current_kalshi: list = []

        async def _run_all():
            for c in cycles:
                await _telemetry_tick(
                    cycle=c,
                    events_total=c * 10,
                    alerts_total=c,
                    delta=10,
                    interval=60,
                    hb_path=tmp_path / "hb.json",
                    write_heartbeat=write_hb,
                    started_at=_make_now(),
                    build_venues_payload=lambda: {},
                    recompute_state=recompute_state,
                    recompute_enabled=True,
                    recompute_cycles=10,
                    safe_recompute_baselines=safe_rc,
                    pool=MagicMock(),
                    window_days=30,
                    min_samples=10,
                    baseline_refresh_cycles=10,
                    load_baselines=load_bl,
                    engine=engine,
                    map_refresh_cycles=10,
                    refresh_subscriptions=refresh_subs,
                    asset_id_map={},
                    current_poly_ids=current_poly,
                    current_kalshi_tickers=current_kalshi,
                    partition_maint_cycles=1440,
                    ensure_partitions=ensure_part,
                    find_old_partitions=find_old,
                    raw_retention_days=90,
                    now_utc=_make_now,
                )

        asyncio.run(_run_all())
        return {
            "write_hb": write_hb,
            "load_bl": load_bl,
            "engine": engine,
            "safe_rc": safe_rc,
            "refresh_subs": refresh_subs,
            "ensure_part": ensure_part,
            "recompute_state": recompute_state,
            "current_poly": current_poly,
        }

    def test_heartbeat_written_for_every_cycle(self, tmp_path):
        result = self._run_cycles(tmp_path, [1, 2, 10])
        assert result["write_hb"].call_count == 3

    def test_baseline_reload_only_on_cycle_10(self, tmp_path):
        result = self._run_cycles(tmp_path, [1, 2, 10])
        # engine.update_baselines called on cycle 10 only
        assert result["engine"].update_baselines.call_count == 1

    def test_recompute_on_cycles_1_and_10(self, tmp_path):
        # _is_maintenance_cycle fires on 1 AND on multiples of 10
        result = self._run_cycles(tmp_path, [1, 2, 10])
        assert result["safe_rc"].await_count == 2  # cycle 1 and cycle 10

    def test_subscription_refresh_on_cycle_10(self, tmp_path):
        result = self._run_cycles(tmp_path, [1, 2, 10])
        assert result["refresh_subs"].await_count == 1  # only cycle 10

    def test_partition_maintenance_on_cycle_1(self, tmp_path):
        result = self._run_cycles(tmp_path, [1, 2, 10])
        assert result["ensure_part"].await_count == 1  # only cycle 1

    def test_recompute_state_populated_after_cycles(self, tmp_path):
        result = self._run_cycles(tmp_path, [1, 2])
        rs = result["recompute_state"]
        assert rs["last_recompute_ok"] is True
        assert rs["last_recompute_at"] is not None

    def test_subscription_list_updated_after_cycle_10(self, tmp_path):
        result = self._run_cycles(tmp_path, [10])
        # refresh_subscriptions returned (["tok-1"], []) → current_poly updated
        assert result["current_poly"] == ["tok-1"]
