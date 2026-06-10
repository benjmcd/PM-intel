"""Offline tests for US-04: in-daemon periodic baseline recompute.

No DB, no network.  All DB-touching helpers are patched via unittest.mock.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

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
        from unittest.mock import MagicMock
        return MagicMock()

    def test_returns_entry_count_on_success(self):
        pool = self._fake_pool()
        fake_result = {"polymarket:abc": {}, "kalshi:xyz": {}}
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value=fake_result),
        ) as mock_fn:
            result = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert result == 2
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
            result = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert result is None

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

    def test_prints_non_fatal_message_on_failure(self, capsys):
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(side_effect=ValueError("oops")),
        ):
            asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        captured = capsys.readouterr()
        assert "[ingest] baseline recompute failed (non-fatal):" in captured.out
        assert "oops" in captured.out

    def test_empty_result_returns_zero(self):
        pool = self._fake_pool()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value={}),
        ):
            result = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert result == 0


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
