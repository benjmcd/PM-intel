"""Offline tests for the in-daemon periodic baseline recompute.

No DB, no network.  All DB-touching helpers are patched via unittest.mock.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# _cycles_from_minutes — pure conversion helper
# ---------------------------------------------------------------------------

from pmfi.commands._shared import _cycles_from_minutes


class TestCyclesFromMinutes:
    def test_daily_60s_interval(self):
        # 1440 minutes / 60s interval == 1440 cycles
        assert _cycles_from_minutes(1440, 60) == 1440

    def test_floor_of_1(self):
        # Very short interval (< 1 cycle) must return at least 1
        assert _cycles_from_minutes(0, 60) == 1

    def test_hourly_60s_interval(self):
        assert _cycles_from_minutes(60, 60) == 60

    def test_30min_60s_interval(self):
        assert _cycles_from_minutes(30, 60) == 30

    def test_10min_60s_interval(self):
        assert _cycles_from_minutes(10, 60) == 10

    def test_daily_30s_interval(self):
        # 1440 minutes * 60 / 30s = 2880 cycles
        assert _cycles_from_minutes(1440, 30) == 2880

    def test_rounding(self):
        # 1 minute / 90s interval = 0.67 → rounds to 1
        assert _cycles_from_minutes(1, 90) == 1

    def test_minimum_is_1_not_zero(self):
        assert _cycles_from_minutes(0, 1) == 1


# ---------------------------------------------------------------------------
# _safe_recompute_baselines — failure isolation and call-arg forwarding
# ---------------------------------------------------------------------------

from pmfi.commands._shared import _safe_recompute_baselines


class TestSafeRecomputeBaselines:
    def _fake_pool(self):
        """Return a minimal MagicMock that satisfies the pool interface."""
        return MagicMock()

    def test_returns_entry_count_on_success(self):
        pool = self._fake_pool()
        fake_result = {"polymarket:abc": {}, "kalshi:xyz": {}}
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value=fake_result),
        ) as mock_fn:
            count, err = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert count == 2
        assert err is None
        mock_fn.assert_awaited_once_with(pool, window_days=30, min_samples=10)

    def test_forwards_window_days_and_min_samples(self):
        pool = self._fake_pool()
        fake_result = {"k:m": {}}
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value=fake_result),
        ) as mock_fn:
            asyncio.run(
                _safe_recompute_baselines(pool, window_days=14, min_samples=5)
            )
        mock_fn.assert_awaited_once_with(pool, window_days=14, min_samples=5)

    def test_returns_none_on_exception(self):
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            count, err = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert count is None
        assert err == "db down"

    def test_does_not_propagate_exception(self):
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(side_effect=Exception("unexpected")),
        ):
            # Must not raise
            asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )

    def test_prints_non_fatal_message_on_failure(self, caplog):
        import logging
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(side_effect=ValueError("oops")),
        ):
            with caplog.at_level(logging.WARNING, logger="pmfi.commands._shared"):
                asyncio.run(
                    _safe_recompute_baselines(pool, window_days=30, min_samples=10)
                )
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "[ingest] baseline recompute failed (non-fatal):" in messages
        assert "oops" in messages

    def test_empty_result_returns_zero(self):
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value={}),
        ):
            count, err = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert count == 0
        assert err is None


def test_compute_and_store_baselines_prunes_stale_market_rows():
    from pmfi.baseline import compute_and_store_baselines

    class Acquire:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Conn:
        def __init__(self):
            self.executed: list[tuple[str, tuple]] = []

        async def execute(self, sql, *args):
            self.executed.append((sql, args))

    class Pool:
        def __init__(self, conn):
            self.conn = conn

        def acquire(self):
            return Acquire(self.conn)

    conn = Conn()
    entries = {
        "polymarket:active-market": {
            "market_id": "11111111-1111-1111-1111-111111111111",
            "sample_size": 12,
            "p99_trade_usd": 100.0,
            "p995_trade_usd": 125.0,
        }
    }

    with (
        patch("pmfi.baseline.compute_baselines", new=AsyncMock(return_value=entries)),
        patch("pmfi.baseline.upsert_baseline", new=AsyncMock()),
    ):
        result = asyncio.run(
            compute_and_store_baselines(Pool(conn), window_days=7, min_samples=5)
        )

    assert result == entries
    assert any("DELETE FROM market_baselines" in sql for sql, _args in conn.executed)
    prune_sql, prune_args = conn.executed[-1]
    assert "lookback_seconds = $1" in prune_sql
    assert prune_args[0] == 7 * 86400
    assert prune_args[1] == ["11111111-1111-1111-1111-111111111111"]


# ---------------------------------------------------------------------------
# Config plumbing — BaselinesConfig and load_config
# ---------------------------------------------------------------------------

import yaml
from pmfi.config import load_config, BaselinesConfig, AppConfig


class TestBaselinesConfigDefaults:
    def test_defaults(self):
        bc = BaselinesConfig()
        assert bc.recompute_enabled is True
        assert bc.recompute_interval_minutes == 1440
        assert bc.window_days == 30
        assert bc.min_samples == 10

    def test_appconfig_has_baselines_field(self):
        cfg = AppConfig()
        assert isinstance(cfg.baselines, BaselinesConfig)


class TestLoadConfigBaselinesSection:
    def test_absent_section_yields_defaults(self, tmp_path):
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg.baselines.recompute_enabled is True
        assert cfg.baselines.recompute_interval_minutes == 1440
        assert cfg.baselines.window_days == 30
        assert cfg.baselines.min_samples == 10

    def test_section_overrides_defaults(self, tmp_path):
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({
                "baselines": {
                    "recompute_enabled": False,
                    "recompute_interval_minutes": 720,
                    "window_days": 7,
                    "min_samples": 3,
                }
            }),
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.baselines.recompute_enabled is False
        assert cfg.baselines.recompute_interval_minutes == 720
        assert cfg.baselines.window_days == 7
        assert cfg.baselines.min_samples == 3

    def test_partial_section_merges_with_defaults(self, tmp_path):
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({"baselines": {"recompute_enabled": False}}),
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.baselines.recompute_enabled is False
        assert cfg.baselines.recompute_interval_minutes == 1440

    def test_baselines_key_not_unknown(self, tmp_path, caplog):
        """'baselines' must not trigger the unknown-top-level-key warning."""
        import logging
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({"baselines": {"recompute_enabled": True}}),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="pmfi.config"):
            load_config(cfg_file)
        for record in caplog.records:
            assert "baselines" not in record.message or "unknown" not in record.message


# ---------------------------------------------------------------------------
# Re-export: helpers accessible from pmfi.cli (test-patch compatibility)
# ---------------------------------------------------------------------------

class TestCliReExports:
    def test_cycles_from_minutes_importable_from_cli(self):
        from pmfi.cli import _cycles_from_minutes as cfm
        assert cfm(60, 60) == 60

    def test_safe_recompute_baselines_importable_from_cli(self):
        from pmfi.cli import _safe_recompute_baselines as srb
        assert callable(srb)
