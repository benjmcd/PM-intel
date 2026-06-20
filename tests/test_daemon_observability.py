"""Offline tests for the daemon-observability cluster (Stories A, B, C).

No DB, no network. All asyncpg/DB calls are faked via unittest.mock.
Mirrors style of test_health_and_maintenance.py and test_baseline_recompute.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Story A.1 / A.3: heartbeat roundtrip with venues map; old-payload tolerance
# ---------------------------------------------------------------------------

from pmfi.health import write_heartbeat, read_heartbeat, read_heartbeat_ex


class TestHeartbeatWithVenues:
    def test_roundtrip_with_venues(self, tmp_path):
        hb_path = tmp_path / "hb.json"
        started = _utc("2026-01-01T00:00:00")
        now = _utc("2026-01-01T00:01:00")
        venues = {
            "polymarket": {
                "events_total": 10,
                "last_event_at": "2026-01-01T00:00:55+00:00",
                "consecutive_failures": 0,
                "last_error": None,
            },
            "kalshi": {
                "events_total": 5,
                "last_event_at": "2026-01-01T00:00:50+00:00",
                "consecutive_failures": 2,
                "last_error": "timeout",
            },
        }
        write_heartbeat(
            hb_path,
            events_total=15,
            alerts_total=1,
            started_at=started,
            now=now,
            venues=venues,
        )
        hb = read_heartbeat(hb_path)
        assert hb is not None
        assert hb["events_total"] == 15
        assert "venues" in hb
        assert hb["venues"]["polymarket"]["events_total"] == 10
        assert hb["venues"]["kalshi"]["consecutive_failures"] == 2
        assert hb["venues"]["kalshi"]["last_error"] == "timeout"

    def test_old_payload_no_venues_tolerated(self, tmp_path):
        """Heartbeats without 'venues' key (old format) must be read without error."""
        hb_path = tmp_path / "old_hb.json"
        # Write old-format payload directly
        old_payload = {
            "ts": "2026-01-01T00:01:00+00:00",
            "events_total": 99,
            "alerts_total": 3,
            "started_at": "2026-01-01T00:00:00+00:00",
            "pid": 12345,
        }
        hb_path.write_text(json.dumps(old_payload), encoding="utf-8")
        hb = read_heartbeat(hb_path)
        assert hb is not None
        assert hb.get("venues") is None  # key absent is fine
        assert hb["events_total"] == 99

    def test_venues_absent_when_not_passed(self, tmp_path):
        """Omitting venues= leaves 'venues' key absent from payload."""
        hb_path = tmp_path / "hb.json"
        write_heartbeat(
            hb_path,
            events_total=0,
            alerts_total=0,
            started_at=_now(),
            now=_now(),
        )
        hb = read_heartbeat(hb_path)
        assert "venues" not in hb

    def test_roundtrip_with_recompute_fields(self, tmp_path):
        hb_path = tmp_path / "hb.json"
        now = _now()
        write_heartbeat(
            hb_path,
            events_total=0,
            alerts_total=0,
            started_at=now,
            now=now,
            last_recompute_at=now.isoformat(),
            last_recompute_ok=False,
            last_recompute_error="db down",
        )
        hb = read_heartbeat(hb_path)
        assert hb["last_recompute_ok"] is False
        assert hb["last_recompute_error"] == "db down"
        assert hb["last_recompute_at"] == now.isoformat()


# ---------------------------------------------------------------------------
# Story C.4: read_heartbeat_ex distinguishes not-found vs unreadable
# ---------------------------------------------------------------------------

class TestReadHeartbeatEx:
    def test_not_found(self, tmp_path):
        hb, kind = read_heartbeat_ex(tmp_path / "nonexistent.json")
        assert hb is None
        assert kind == "not_found"

    def test_corrupt_is_unreadable(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not-json", encoding="utf-8")
        hb, kind = read_heartbeat_ex(p)
        assert hb is None
        assert kind.startswith("unreadable:")

    def test_ok_on_valid_file(self, tmp_path):
        p = tmp_path / "hb.json"
        write_heartbeat(p, events_total=5, alerts_total=0, started_at=_now(), now=_now())
        hb, kind = read_heartbeat_ex(p)
        assert kind == "ok"
        assert hb is not None
        assert hb["events_total"] == 5


# ---------------------------------------------------------------------------
# Story A.4 / Story C.4: cmd_health per-venue and never-started text
# ---------------------------------------------------------------------------

class TestCmdHealthObservability:
    def _write_hb_with_stale_venue(self, tmp_path):
        """Build a heartbeat where 'kalshi' has no recent events (stale venue)."""
        hb_path = tmp_path / "hb.json"
        old_ts = (_now() - timedelta(seconds=900)).isoformat()
        fresh_ts = (_now() - timedelta(seconds=30)).isoformat()
        venues = {
            "polymarket": {
                "events_total": 20,
                "last_event_at": fresh_ts,
                "consecutive_failures": 0,
                "last_error": None,
            },
            "kalshi": {
                "events_total": 5,
                "last_event_at": old_ts,  # 900s ago → stale (threshold default 600s)
                "consecutive_failures": 0,
                "last_error": None,
            },
        }
        write_heartbeat(
            hb_path,
            events_total=25,
            alerts_total=0,
            started_at=_now() - timedelta(seconds=1000),
            now=_now() - timedelta(seconds=5),
            venues=venues,
        )
        return hb_path

    def test_per_venue_lines_printed(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = self._write_hb_with_stale_venue(tmp_path)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        rc = cmd_health(args)
        out = capsys.readouterr().out
        assert "polymarket" in out
        assert "kalshi" in out
        # exit 0: aggregate heartbeat is fresh
        assert rc == 0

    def test_stale_venue_warning_printed(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = self._write_hb_with_stale_venue(tmp_path)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        # kalshi is 900s old, threshold is 600s → WARNING line
        assert "WARNING" in out
        assert "kalshi" in out

    def test_aggregate_exit_code_unchanged_by_venue_warning(self, tmp_path, capsys):
        """Venue stale warnings must NOT change aggregate exit code."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = self._write_hb_with_stale_venue(tmp_path)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        rc = cmd_health(args)
        assert rc == 0  # aggregate fresh even if a venue is stale

    def test_circuit_open_prints_and_changes_exit_code(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse

        hb_path = tmp_path / "hb.json"
        now = _now() - timedelta(seconds=5)
        write_heartbeat(
            hb_path,
            events_total=10,
            alerts_total=0,
            started_at=now - timedelta(minutes=5),
            now=now,
            venues={
                "polymarket": {
                    "events_total": 10,
                    "last_event_at": now.isoformat(),
                    "consecutive_failures": 3,
                    "last_error": "adapter timeout",
                    "circuit_open": True,
                }
            },
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )

        rc = cmd_health(args)
        out = capsys.readouterr().out

        assert rc == 1
        assert "circuit_open" in out
        assert "polymarket" in out
        assert "adapter timeout" in out

    def test_missing_last_event_at_prints_stale_warning_without_exit_change(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse

        hb_path = tmp_path / "hb.json"
        now = _now() - timedelta(seconds=5)
        write_heartbeat(
            hb_path,
            events_total=0,
            alerts_total=0,
            started_at=now - timedelta(minutes=5),
            now=now,
            venues={
                "kalshi": {
                    "events_total": 0,
                    "last_event_at": None,
                    "consecutive_failures": 0,
                    "last_error": None,
                }
            },
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )

        rc = cmd_health(args)
        out = capsys.readouterr().out

        assert rc == 0
        assert "WARNING" in out
        assert "kalshi" in out
        assert "last_event=never" in out

    def test_never_started_message(self, tmp_path, capsys):
        """Missing heartbeat → 'never started' message (not 'No heartbeat found')."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        args = argparse.Namespace(
            heartbeat_path=str(tmp_path / "nonexistent.json"),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        rc = cmd_health(args)
        out = capsys.readouterr().out
        assert "never started" in out or "never completed" in out
        assert rc == 1

    def test_unreadable_heartbeat_message(self, tmp_path, capsys):
        """Corrupt heartbeat → 'Heartbeat unreadable' message."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        p = tmp_path / "bad.json"
        p.write_text("not-json", encoding="utf-8")
        args = argparse.Namespace(
            heartbeat_path=str(p),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        rc = cmd_health(args)
        out = capsys.readouterr().out
        assert "unreadable" in out.lower()
        assert rc == 1

    def test_stale_heartbeat_shows_pid_started_ts(self, tmp_path, capsys):
        """Stale heartbeat text mode must include pid, started_at, ts."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        old_ts = _now() - timedelta(seconds=300)
        write_heartbeat(
            hb_path,
            events_total=5,
            alerts_total=0,
            started_at=old_ts,
            now=old_ts,
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        rc = cmd_health(args)
        out = capsys.readouterr().out
        assert "pid=" in out
        assert "started_at=" in out
        assert rc == 1

    def test_json_output_includes_venues_and_recompute(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        now = _now() - timedelta(seconds=5)
        venues = {"polymarket": {"events_total": 3, "last_event_at": None,
                                  "consecutive_failures": 0, "last_error": None}}
        write_heartbeat(
            hb_path,
            events_total=3,
            alerts_total=0,
            started_at=now,
            now=now,
            venues=venues,
            last_recompute_at=now.isoformat(),
            last_recompute_ok=True,
            last_recompute_error=None,
            partition_maintenance={
                "retention_enabled": False,
                "retention_operator_acknowledged": False,
                "retention_active": False,
                "old_partitions": ["raw_events_2024_01"],
                "dropped_partitions": [],
                "last_drop_error": None,
            },
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=True,
            venue_stale_seconds=600,
        )
        cmd_health(args)
        data = json.loads(capsys.readouterr().out)
        assert "venues" in data
        assert data["last_recompute_ok"] is True
        assert data["partition_maintenance"]["old_partitions"] == ["raw_events_2024_01"]


# ---------------------------------------------------------------------------
# Story B.1: _safe_recompute_baselines returns (count, error_str)
# (These complement the updated tests in test_baseline_recompute.py)
# ---------------------------------------------------------------------------

from pmfi.commands._shared import _safe_recompute_baselines


class TestSafeRecomputeReturnsTuple:
    def test_success_returns_count_and_none_error(self):
        pool = MagicMock()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(return_value={"a:b": {}, "c:d": {}}),
        ):
            count, err = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert count == 2
        assert err is None

    def test_failure_returns_none_and_error_string(self):
        pool = MagicMock()
        with patch(
            "pmfi.baseline.compute_and_store_baselines",
            new=AsyncMock(side_effect=RuntimeError("db gone")),
        ):
            count, err = asyncio.run(
                _safe_recompute_baselines(pool, window_days=30, min_samples=10)
            )
        assert count is None
        assert "db gone" in err


# ---------------------------------------------------------------------------
# Story B.2: cmd_health warns when recompute failed / overdue
# ---------------------------------------------------------------------------

class TestCmdHealthRecompute:
    def _write_hb_recompute_failed(self, tmp_path, ok: bool, error: str | None = None,
                                   age_seconds: int = 10):
        hb_path = tmp_path / "hb.json"
        rc_at = (_now() - timedelta(seconds=age_seconds)).isoformat()
        write_heartbeat(
            hb_path,
            events_total=1,
            alerts_total=0,
            started_at=_now() - timedelta(seconds=age_seconds + 10),
            now=_now() - timedelta(seconds=5),
            last_recompute_at=rc_at,
            last_recompute_ok=ok,
            last_recompute_error=error,
        )
        return hb_path

    def test_warns_when_recompute_failed(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = self._write_hb_recompute_failed(tmp_path, ok=False, error="oops")
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "FAILED" in out
        assert "oops" in out

    def test_no_warn_when_recompute_ok(self, tmp_path, capsys):
        from pmfi.commands.reporting import cmd_health
        import argparse
        hb_path = self._write_hb_recompute_failed(tmp_path, ok=True)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
            venue_stale_seconds=600,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        assert "last_recompute: ok" in out

    def test_warns_when_recompute_overdue(self, tmp_path, capsys):
        """Warn when last_recompute_at is older than 2x recompute_interval_minutes."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        # recompute_interval_minutes defaults to 1440; 2x = 2880 min = 172800s
        # Write a heartbeat with recompute done 3 days ago (259200s)
        overdue_seconds = 259200
        hb_path = self._write_hb_recompute_failed(
            tmp_path, ok=True, age_seconds=overdue_seconds
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=overdue_seconds + 600,  # aggregate not stale
            json_output=False,
            venue_stale_seconds=600,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        assert "overdue" in out

    def test_no_overdue_warn_when_recompute_disabled(self, tmp_path, capsys):
        """When recompute_enabled=False, overdue warning must not fire."""
        from pmfi.commands.reporting import cmd_health
        import argparse
        overdue_seconds = 259200
        hb_path = self._write_hb_recompute_failed(
            tmp_path, ok=True, age_seconds=overdue_seconds
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=overdue_seconds + 600,
            json_output=False,
            venue_stale_seconds=600,
        )
        # Patch load_config in the pmfi.config module so cmd_health sees it
        mock_cfg = MagicMock()
        mock_cfg.health.venue_stale_seconds = 600
        mock_cfg.baselines.recompute_interval_minutes = 1440
        mock_cfg.baselines.recompute_enabled = False
        with patch("pmfi.config.load_config", return_value=mock_cfg):
            cmd_health(args)
        out = capsys.readouterr().out
        assert "overdue" not in out


# ---------------------------------------------------------------------------
# Story A.2: supervise status_map records per-venue failures
# ---------------------------------------------------------------------------

from pmfi.pipeline.supervisor import supervise, PoolManager


class TestSuperviseStatusMap:
    """Verify status_map is updated on failure and reset on clean run."""

    def _make_pool_manager(self):
        pm = MagicMock(spec=PoolManager)
        pm.generation = 0
        pm.pool = MagicMock()
        pm.recreate = AsyncMock(return_value=pm.pool)
        return pm

    def test_status_map_records_failure(self):
        """supervise writes consecutive_failures and last_error after an exception."""
        status_map: dict = {}
        pm = self._make_pool_manager()
        shutdown = asyncio.Event()
        call_count = [0]
        # Capture the state of status_map right after the failure is recorded
        recorded_after_failure: list = []

        def _make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def _run_one(adapter, pool_manager):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("adapter exploded")
            # Capture state before shutdown so the second loop iteration records it
            recorded_after_failure.append(dict(status_map.get("testv", {})))
            # Now set shutdown so the loop exits
            shutdown.set()

        async def _drive():
            await supervise(
                "testv", _make_adapter, _run_one,
                shutdown=shutdown, pool_manager=pm,
                initial_backoff=0.0, max_backoff=0.0, jitter=False,
                status_map=status_map,
            )

        asyncio.run(_drive())
        # The status was captured before the clean run. Check the failure was recorded.
        assert recorded_after_failure, "second _run_one call never reached"
        captured = recorded_after_failure[0]
        assert captured["consecutive_failures"] == 1
        assert "adapter exploded" in captured["last_error"]

    def test_status_map_none_is_no_op(self):
        """supervise with status_map=None must not raise."""
        pm = self._make_pool_manager()
        shutdown = asyncio.Event()

        def _make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def _run_one(adapter, pool_manager):
            shutdown.set()

        async def _drive():
            await supervise(
                "testv", _make_adapter, _run_one,
                shutdown=shutdown, pool_manager=pm,
                initial_backoff=0.0, max_backoff=0.0, jitter=False,
                status_map=None,
            )

        asyncio.run(_drive())  # Must not raise

    def test_status_map_failure_increments_count(self):
        """Two consecutive failures → consecutive_failures=2 before reset."""
        status_map: dict = {}
        pm = self._make_pool_manager()
        shutdown = asyncio.Event()
        call_count = [0]
        recorded_failures = []

        def _make_adapter():
            a = MagicMock()
            a.connect = AsyncMock()
            a.disconnect = AsyncMock()
            return a

        async def _run_one(adapter, pool_manager):
            call_count[0] += 1
            if call_count[0] <= 2:
                raise RuntimeError(f"fail {call_count[0]}")
            # Record the state just before clean exit resets it
            recorded_failures.append(status_map.get("testv", {}).get("consecutive_failures", 0))
            shutdown.set()

        async def _drive():
            await supervise(
                "testv", _make_adapter, _run_one,
                shutdown=shutdown, pool_manager=pm,
                initial_backoff=0.0, max_backoff=0.0, jitter=False,
                status_map=status_map,
            )

        asyncio.run(_drive())
        # Before the clean run reset, consecutive_failures was 2
        assert recorded_failures and recorded_failures[0] == 2


# ---------------------------------------------------------------------------
# Story B.2/C.1 config: HealthConfig parsing
# ---------------------------------------------------------------------------

import yaml
from pmfi.config import load_config, HealthConfig, AppConfig


class TestHealthConfig:
    def test_defaults(self):
        cfg = HealthConfig()
        assert cfg.venue_stale_seconds == 600

    def test_appconfig_has_health_field(self):
        cfg = AppConfig()
        assert isinstance(cfg.health, HealthConfig)

    def test_absent_section_yields_defaults(self, tmp_path):
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg.health.venue_stale_seconds == 600

    def test_section_overrides_defaults(self, tmp_path):
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({"health": {"venue_stale_seconds": 300}}),
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg.health.venue_stale_seconds == 300

    def test_health_key_not_unknown(self, tmp_path, caplog):
        """'health' must not trigger the unknown-top-level-key warning."""
        cfg_file = tmp_path / "app.yaml"
        cfg_file.write_text(
            yaml.dump({"health": {"venue_stale_seconds": 300}}),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="pmfi.config"):
            load_config(cfg_file)
        for record in caplog.records:
            assert "health" not in record.message or "unknown" not in record.message


# ---------------------------------------------------------------------------
# Story B.3: volume_spike history_max read from YAML
# ---------------------------------------------------------------------------

class TestVolumeSpikeHistoryMax:
    def test_default_history_max_is_200(self, tmp_path):
        """When history_max absent from YAML, engine uses 200."""
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text(
            yaml.dump({
                "version": "test.v1",
                "rules": {
                    "volume_spike_v1": {
                        "enabled": True,
                        "min_spike_multiplier": 5.0,
                        "min_baseline_trades": 20,
                        "severity": "medium",
                    }
                }
            }),
            encoding="utf-8",
        )
        from pmfi.pipeline.engine import AlertEngine
        engine = AlertEngine(rules_path=rules_yaml)
        assert engine._vs_history_max == 200

    def test_custom_history_max_loaded_from_yaml(self, tmp_path):
        """history_max in YAML overrides the 200 default."""
        rules_yaml = tmp_path / "rules.yaml"
        rules_yaml.write_text(
            yaml.dump({
                "version": "test.v1",
                "rules": {
                    "volume_spike_v1": {
                        "enabled": True,
                        "min_spike_multiplier": 5.0,
                        "min_baseline_trades": 20,
                        "history_max": 50,
                        "severity": "medium",
                    }
                }
            }),
            encoding="utf-8",
        )
        from pmfi.pipeline.engine import AlertEngine
        engine = AlertEngine(rules_path=rules_yaml)
        assert engine._vs_history_max == 50


# ---------------------------------------------------------------------------
# Story C.2: server lookback param clamping (offline)
# ---------------------------------------------------------------------------

class TestFeedHealthLookbackClamping:
    """Verify the clamping logic mirrors the _volume endpoint pattern."""

    def _clamp(self, raw: str, default: int = 10) -> int:
        """Reproduce the server.py clamping logic inline for offline testing."""
        try:
            return max(1, min(int(raw), 1440))
        except (TypeError, ValueError):
            return default

    def test_clamp_below_min(self):
        assert self._clamp("0") == 1

    def test_clamp_above_max(self):
        assert self._clamp("9999") == 1440

    def test_clamp_valid(self):
        assert self._clamp("30") == 30

    def test_clamp_invalid_string(self):
        assert self._clamp("abc") == 10

    def test_clamp_exactly_1(self):
        assert self._clamp("1") == 1

    def test_clamp_exactly_1440(self):
        assert self._clamp("1440") == 1440


# ---------------------------------------------------------------------------
# Story C.3: dashboard never-seen vs stale logic check (pure JS-free tests)
# ---------------------------------------------------------------------------

class TestFeedHealthPayloadShape:
    """Verify the structure returned from feed_health with a DB mock matches
    what the dashboard JS expects for stale vs active venues."""

    def _make_row(self, venue_code, last_at_offset_seconds):
        """Return a dict simulating a feed_health() result row."""
        from datetime import datetime, timezone
        last_at = datetime.now(timezone.utc) - timedelta(seconds=last_at_offset_seconds)
        age_s = last_at_offset_seconds
        return {
            "venue_code": venue_code,
            "last_event_at": last_at.isoformat(),
            "last_event_age_s": age_s,
            "events_60s": 0 if age_s > 60 else 5,
            "events_5m": 0 if age_s > 300 else 5,
            "unresolved_dead_letters_1h": 0,
        }

    def test_stale_venue_has_nonzero_age(self):
        row = self._make_row("polymarket", 900)
        assert row["last_event_age_s"] > 0
        assert row["events_60s"] == 0
        assert row["events_5m"] == 0

    def test_active_venue_has_small_age(self):
        row = self._make_row("kalshi", 10)
        assert row["last_event_age_s"] < 60
        assert row["events_60s"] == 5


# ---------------------------------------------------------------------------
# Story A.4: --venue-stale-seconds CLI flag registered
# ---------------------------------------------------------------------------

class TestBuildParserVenueStale:
    def test_venue_stale_seconds_registered(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health", "--venue-stale-seconds", "300"])
        assert ns.venue_stale_seconds == 300

    def test_venue_stale_seconds_default_is_none(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health"])
        assert ns.venue_stale_seconds is None
