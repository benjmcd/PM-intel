"""Offline tests for US-08 (partition maintenance cadence) and US-09 (heartbeat/health).

No DB, no network. All asyncpg calls are faked via unittest.mock.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# US-09: health.py helpers
# ---------------------------------------------------------------------------

from pmfi.health import (
    write_heartbeat,
    read_heartbeat,
    heartbeat_age_seconds,
    is_stale,
)


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestWriteReadRoundtrip:
    def test_roundtrip(self, tmp_path):
        hb_path = tmp_path / "hb.json"
        started = _utc("2026-01-01T00:00:00")
        now = _utc("2026-01-01T00:01:00")
        write_heartbeat(
            hb_path,
            events_total=42,
            alerts_total=7,
            started_at=started,
            now=now,
        )
        hb = read_heartbeat(hb_path)
        assert hb is not None
        assert hb["events_total"] == 42
        assert hb["alerts_total"] == 7
        assert "pid" in hb
        assert hb["ts"] == now.isoformat()
        assert hb["started_at"] == started.isoformat()

    def test_creates_parent_dirs(self, tmp_path):
        hb_path = tmp_path / "a" / "b" / "c" / "hb.json"
        write_heartbeat(hb_path, events_total=0, alerts_total=0,
                        started_at=_now(), now=_now())
        assert hb_path.exists()

    def test_read_missing_returns_none(self, tmp_path):
        assert read_heartbeat(tmp_path / "nonexistent.json") is None

    def test_read_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not-json", encoding="utf-8")
        assert read_heartbeat(p) is None


class TestHeartbeatAge:
    def test_correct_age(self):
        now = _utc("2026-01-01T00:02:00")
        hb = {"ts": "2026-01-01T00:01:30+00:00"}
        age = heartbeat_age_seconds(hb, now)
        assert age == pytest.approx(30.0)

    def test_missing_ts_returns_none(self):
        assert heartbeat_age_seconds({}, _now()) is None

    def test_invalid_ts_returns_none(self):
        assert heartbeat_age_seconds({"ts": "not-a-date"}, _now()) is None

    def test_naive_ts_treated_as_utc(self):
        now = _utc("2026-01-01T00:01:00")
        hb = {"ts": "2026-01-01T00:00:00"}  # naive
        age = heartbeat_age_seconds(hb, now)
        assert age == pytest.approx(60.0)


class TestIsStale:
    def test_none_hb_is_stale(self):
        assert is_stale(None, _now(), threshold_seconds=120) is True

    def test_fresh_hb_not_stale(self):
        now = _utc("2026-01-01T00:02:00")
        hb = {"ts": "2026-01-01T00:01:50+00:00"}  # 10s old
        assert is_stale(hb, now, threshold_seconds=120) is False

    def test_old_hb_is_stale(self):
        now = _utc("2026-01-01T00:05:00")
        hb = {"ts": "2026-01-01T00:00:00+00:00"}  # 5min old
        assert is_stale(hb, now, threshold_seconds=120) is True

    def test_exactly_at_threshold_is_stale(self):
        now = _utc("2026-01-01T00:02:00")
        hb = {"ts": "2026-01-01T00:00:00+00:00"}  # 120s old
        # age(120) > threshold(120) → False; age(120) is NOT > 120
        assert is_stale(hb, now, threshold_seconds=120) is False

    def test_missing_ts_is_stale(self):
        assert is_stale({}, _now(), threshold_seconds=120) is True


# ---------------------------------------------------------------------------
# US-09: pmfi health CLI — exits 1 when no heartbeat, 0 when fresh
# ---------------------------------------------------------------------------

class TestCmdHealth:
    def test_exits_1_when_no_heartbeat(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        args = argparse.Namespace(
            heartbeat_path=str(tmp_path / "nonexistent.json"),
            max_age_seconds=120.0,
            json_output=False,
        )
        rc = cmd_health(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "No heartbeat" in captured.out

    def test_exits_0_when_fresh(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        # Write a heartbeat timestamped 5 seconds ago
        started = datetime.now(timezone.utc) - timedelta(seconds=10)
        now_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        write_heartbeat(hb_path, events_total=10, alerts_total=2,
                        started_at=started, now=now_ts)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
        )
        rc = cmd_health(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "fresh" in out

    def test_exits_1_when_stale(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        # Write a heartbeat timestamped 300 seconds ago
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=300)
        write_heartbeat(hb_path, events_total=5, alerts_total=0,
                        started_at=old_ts, now=old_ts)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
        )
        rc = cmd_health(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "STALE" in out

    def test_json_output(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        now_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        write_heartbeat(hb_path, events_total=3, alerts_total=1,
                        started_at=now_ts, now=now_ts)
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=True,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["found"] is True
        assert data["events_total"] == 3

    def test_old_partition_warning_printed(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        now_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        write_heartbeat(
            hb_path,
            events_total=3,
            alerts_total=1,
            started_at=now_ts,
            now=now_ts,
            partition_maintenance={
                "retention_enabled": False,
                "retention_operator_acknowledged": False,
                "retention_active": False,
                "raw_retention_days": 90,
                "old_partitions": ["raw_events_2024_01"],
                "dropped_partitions": [],
                "last_drop_error": None,
            },
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "old partition" in out
        assert "raw_events_2024_01" in out
        assert "retention is disabled" in out

    def test_partition_drop_failure_printed(self, tmp_path, capsys):
        from pmfi.cli import cmd_health
        import argparse
        hb_path = tmp_path / "hb.json"
        now_ts = datetime.now(timezone.utc) - timedelta(seconds=5)
        write_heartbeat(
            hb_path,
            events_total=3,
            alerts_total=1,
            started_at=now_ts,
            now=now_ts,
            partition_maintenance={
                "retention_enabled": True,
                "retention_operator_acknowledged": True,
                "retention_active": True,
                "raw_retention_days": 90,
                "old_partitions": ["raw_events_2024_01"],
                "dropped_partitions": [],
                "last_drop_error": "drop blocked",
            },
        )
        args = argparse.Namespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=120.0,
            json_output=False,
        )
        cmd_health(args)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "partition retention drop failed" in out
        assert "drop blocked" in out


# ---------------------------------------------------------------------------
# US-08: find_partitions_older_than — pure name-parsing unit tests
# ---------------------------------------------------------------------------

class TestFindPartitionsOlderThan:
    """Test find_partitions_older_than with a fake asyncpg pool."""

    def _make_fake_pool(self, table_names: list[str]):
        """Build a minimal asyncpg pool mock returning synthetic pg_tables rows."""
        # Each row: {"tablename": name}
        rows_by_pattern: dict[str, list] = {}
        for name in table_names:
            # map to the table prefix it belongs to
            for prefix in ("raw_events", "normalized_trades", "metric_windows",
                           "market_snapshots", "orderbook_snapshots"):
                if name.startswith(prefix + "_"):
                    key = f"{prefix}_%"
                    rows_by_pattern.setdefault(key, []).append({"tablename": name})

        async def _fetch(query, pattern):
            return rows_by_pattern.get(pattern, [])

        conn_mock = AsyncMock()
        conn_mock.fetch = _fetch

        # AsyncContextManager for pool.acquire()
        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn_mock)
        acquire_cm.__aexit__ = AsyncMock(return_value=False)

        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=acquire_cm)
        return pool_mock

    def test_finds_old_partitions(self):
        from pmfi.db.migrations import find_partitions_older_than
        # raw_events_2024_01 is well over 90 days old
        pool = self._make_fake_pool(["raw_events_2024_01", "raw_events_2026_06"])
        result = asyncio.run(find_partitions_older_than(pool, before_days=90))
        assert "raw_events_2024_01" in result
        assert "raw_events_2026_06" not in result

    def test_empty_when_all_recent(self):
        from pmfi.db.migrations import find_partitions_older_than
        pool = self._make_fake_pool(["raw_events_2026_06"])
        result = asyncio.run(find_partitions_older_than(pool, before_days=90))
        assert result == []

    def test_skips_malformed_names(self):
        from pmfi.db.migrations import find_partitions_older_than
        pool = self._make_fake_pool(["raw_events_bad_suffix"])
        result = asyncio.run(find_partitions_older_than(pool, before_days=90))
        assert result == []

    def test_multiple_tables(self):
        from pmfi.db.migrations import find_partitions_older_than
        pool = self._make_fake_pool([
            "raw_events_2024_01",
            "normalized_trades_2023_12",
            "metric_windows_2026_06",
        ])
        result = asyncio.run(find_partitions_older_than(pool, before_days=90))
        assert "raw_events_2024_01" in result
        assert "normalized_trades_2023_12" in result
        assert "metric_windows_2026_06" not in result


class TestEnsureCurrentPartitions:
    def _make_execute_pool(self):
        executed: list[str] = []

        async def _execute(sql):
            executed.append(sql)

        conn_mock = AsyncMock()
        conn_mock.execute = _execute

        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn_mock)
        acquire_cm.__aexit__ = AsyncMock(return_value=False)

        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=acquire_cm)
        return pool_mock, executed

    def test_months_ahead_crosses_year_boundary(self):
        from pmfi.db.migrations import ensure_current_partitions
        pool, executed = self._make_execute_pool()

        asyncio.run(
            ensure_current_partitions(
                pool,
                months_ahead=2,
                now=_utc("2026-12-15T00:00:00"),
            )
        )

        joined = "\n".join(executed)
        assert "raw_events_2026_12" in joined
        assert "raw_events_2027_01" in joined
        assert "raw_events_2027_02" in joined
        assert len(executed) == 15


# ---------------------------------------------------------------------------
# US-08: _is_maintenance_cycle — pure cadence helper
# ---------------------------------------------------------------------------

class TestIsMaintenanceCycle:
    def test_fires_on_cycle_1(self):
        from pmfi.cli import _is_maintenance_cycle
        assert _is_maintenance_cycle(1, 1440) is True

    def test_fires_on_multiple_of_every(self):
        from pmfi.cli import _is_maintenance_cycle
        assert _is_maintenance_cycle(1440, 1440) is True
        assert _is_maintenance_cycle(2880, 1440) is True

    def test_does_not_fire_on_others(self):
        from pmfi.cli import _is_maintenance_cycle
        assert _is_maintenance_cycle(2, 1440) is False
        assert _is_maintenance_cycle(100, 1440) is False
        assert _is_maintenance_cycle(1439, 1440) is False

    def test_small_every(self):
        from pmfi.cli import _is_maintenance_cycle
        assert _is_maintenance_cycle(1, 5) is True
        assert _is_maintenance_cycle(5, 5) is True
        assert _is_maintenance_cycle(10, 5) is True
        assert _is_maintenance_cycle(3, 5) is False


# ---------------------------------------------------------------------------
# _build_parser still works with the new health subcommand
# ---------------------------------------------------------------------------

class TestBuildParserHealth:
    def test_health_subcommand_registered(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health"])
        assert ns.command == "health"
        assert ns.max_age_seconds is None  # default
        assert ns.json_output is False

    def test_health_max_age(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health", "--max-age-seconds", "300"])
        assert ns.max_age_seconds == 300.0

    def test_health_json_flag(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health", "--json"])
        assert ns.json_output is True

    def test_health_heartbeat_path(self):
        from pmfi.cli import _build_parser
        parser = _build_parser()
        ns = parser.parse_args(["health", "--heartbeat-path", "/tmp/hb.json"])
        assert ns.heartbeat_path == "/tmp/hb.json"
