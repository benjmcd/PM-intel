"""Gap 3: cmd_watch offline tests.

Drives cmd_watch with a mocked asyncpg pool whose fetch returns canned alert
rows. Tests:
- no-filter path
- rule + venue + severity combined filter
- SQL $N placeholders are consistent with params length
- loop exits on KeyboardInterrupt and returns rc == 0
- DB connect failure returns early (does not raise)

No real DB. No network. rich must be installed (it is in dev extras).
"""
from __future__ import annotations

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    *,
    interval: float = 5.0,
    limit: int = 15,
    rule: str | None = None,
    venue: str | None = None,
    severity: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        interval=interval,
        limit=limit,
        rule=rule,
        venue=venue,
        severity=severity,
    )


def _canned_alert_row() -> dict:
    """Return a dict that looks like an asyncpg Record for an alert."""
    return {
        "fired_at": "2026-01-01 12:00:00",
        "rule_key": "large_trade_absolute_v1",
        "severity": "high",
        "confidence": "high",
        "score": "0.95",
        "venue_code": "polymarket",
        "outcome_key": "yes",
        "data_quality": "ok",
        "market_title": "BTC above 100k?",
    }


def _make_mock_pool(alert_rows=None, metrics_row=None):
    """Return a mock asyncpg pool whose fetch/fetchrow return canned data."""
    if alert_rows is None:
        alert_rows = [_canned_alert_row()]
    if metrics_row is None:
        metrics_row = {"alert_count": 1, "last_alert": "2026-01-01 12:00:00"}

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=alert_rows)
    pool.fetchrow = AsyncMock(return_value=metrics_row)
    pool.close = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Helper: patch asyncpg.create_pool inside cmd_watch's _run() closure.
# cmd_watch calls asyncpg.create_pool directly inside reporting.py's _run.
# ---------------------------------------------------------------------------

def _patch_asyncpg_create_pool(mock_pool):
    """Return a context manager that patches asyncpg.create_pool globally.

    cmd_watch imports asyncpg with a local `import asyncpg` inside _run(), so
    the binding resolves through the top-level asyncpg module rather than as an
    attribute of pmfi.commands.reporting.  Patching asyncpg.create_pool directly
    intercepts the call regardless of where the import happens.
    """
    return patch(
        "asyncpg.create_pool",
        new=AsyncMock(return_value=mock_pool),
    )


# ---------------------------------------------------------------------------
# Patch rich.live.Live so it does not try to write to a terminal and
# make the while-True loop exit after the first iteration via KeyboardInterrupt.
# ---------------------------------------------------------------------------

class _FakeLive:
    """Minimal Live context manager that records update() calls."""

    def __init__(self, *args, **kwargs):
        self.updates: list = []
        self._enter_count = 0

    def __enter__(self):
        self._enter_count += 1
        return self

    def __exit__(self, *exc):
        return False  # do not suppress exceptions

    def update(self, renderable):
        self.updates.append(renderable)
        # After the first update, raise KeyboardInterrupt to break the while loop
        raise KeyboardInterrupt


# ---------------------------------------------------------------------------
# No-filter path
# ---------------------------------------------------------------------------

class TestCmdWatchNoFilter:
    def test_returns_zero_on_keyboard_interrupt(self, capsys):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args()

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                rc = cmd_watch(args)

        assert rc == 0

    def test_fetch_called_with_no_where_clause(self):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args()

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        assert pool.fetch.call_count >= 1
        sql_arg = pool.fetch.call_args[0][0]
        # No filters → WHERE clause must be absent
        assert "WHERE" not in sql_arg

    def test_fetch_passes_limit_as_last_param(self):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args(limit=7)

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        positional_params = pool.fetch.call_args[0][1:]  # everything after the SQL string
        assert 7 in positional_params

    def test_pool_closed_after_loop(self):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args()

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        pool.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Combined filter: rule + venue + severity
# ---------------------------------------------------------------------------

class TestCmdWatchFilters:
    def _run_with_filters(self, rule=None, venue=None, severity=None):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args(rule=rule, venue=venue, severity=severity)

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                rc = cmd_watch(args)

        return rc, pool

    def test_combined_filter_returns_zero(self):
        rc, _ = self._run_with_filters(
            rule="large_trade_absolute_v1",
            venue="polymarket",
            severity="high",
        )
        assert rc == 0

    def test_sql_has_where_clause_with_all_three_filters(self):
        _, pool = self._run_with_filters(
            rule="large_trade_absolute_v1",
            venue="polymarket",
            severity="high",
        )
        sql = pool.fetch.call_args[0][0]
        assert "WHERE" in sql
        assert "rule_key" in sql
        assert "venue_code" in sql
        assert "severity" in sql

    def test_placeholder_numbers_match_params_count(self):
        """Max $N in SQL must equal the number of positional params passed to fetch."""
        import re
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args(
            rule="large_trade_absolute_v1",
            venue="polymarket",
            severity="high",
            limit=10,
        )

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        call_args = pool.fetch.call_args
        sql = call_args[0][0]
        params = call_args[0][1:]  # positional args after SQL

        placeholders = re.findall(r"\$(\d+)", sql)
        assert placeholders, "Expected at least one $N placeholder in SQL"
        max_n = max(int(p) for p in placeholders)
        assert max_n == len(params), (
            f"SQL has max placeholder ${max_n} but {len(params)} params were passed: {params}"
        )

    def test_placeholder_numbers_match_params_rule_only(self):
        """Single filter: $1=rule_value, $2=limit."""
        import re
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args(rule="my_rule", limit=5)

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        call_args = pool.fetch.call_args
        sql = call_args[0][0]
        params = call_args[0][1:]
        placeholders = re.findall(r"\$(\d+)", sql)
        max_n = max(int(p) for p in placeholders)
        assert max_n == len(params)
        assert "my_rule" in params
        assert 5 in params

    def test_placeholder_numbers_match_params_venue_only(self):
        import re
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args(venue="kalshi", limit=3)

        with _patch_asyncpg_create_pool(pool):
            with patch("rich.live.Live", _FakeLive):
                cmd_watch(args)

        call_args = pool.fetch.call_args
        sql = call_args[0][0]
        params = call_args[0][1:]
        placeholders = re.findall(r"\$(\d+)", sql)
        max_n = max(int(p) for p in placeholders)
        assert max_n == len(params)
        assert "kalshi" in params

    def test_rule_value_passed_as_param(self):
        _, pool = self._run_with_filters(rule="volume_spike_v1")
        params = pool.fetch.call_args[0][1:]
        assert "volume_spike_v1" in params

    def test_venue_value_passed_as_param(self):
        _, pool = self._run_with_filters(venue="kalshi")
        params = pool.fetch.call_args[0][1:]
        assert "kalshi" in params

    def test_severity_value_passed_as_param(self):
        _, pool = self._run_with_filters(severity="medium")
        params = pool.fetch.call_args[0][1:]
        assert "medium" in params


# ---------------------------------------------------------------------------
# DB connect failure path
# ---------------------------------------------------------------------------

class TestCmdWatchDbFailure:
    def test_returns_zero_when_db_connect_fails(self):
        """DB connect failure causes _run to return early; cmd_watch still returns 0."""
        from pmfi.commands.reporting import cmd_watch

        args = _make_args()

        with patch(
            "asyncpg.create_pool",
            new=AsyncMock(side_effect=Exception("connection refused")),
        ):
            rc = cmd_watch(args)

        assert rc == 0

    def test_no_fetch_called_when_db_connect_fails(self):
        from pmfi.commands.reporting import cmd_watch

        pool = _make_mock_pool()
        args = _make_args()

        with patch(
            "asyncpg.create_pool",
            new=AsyncMock(side_effect=Exception("connection refused")),
        ):
            cmd_watch(args)

        pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# ImportError path (rich missing)
# ---------------------------------------------------------------------------

class TestCmdWatchNoRich:
    def test_returns_1_when_rich_missing(self, capsys):
        from pmfi.commands.reporting import cmd_watch

        args = _make_args()

        # Simulate rich not installed by making the import inside cmd_watch raise
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        import builtins
        real_import = builtins.__import__

        def _no_rich(name, *args, **kwargs):
            if name.startswith("rich"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_no_rich):
            rc = cmd_watch(args)

        assert rc == 1
        out = capsys.readouterr().out
        assert "rich" in out.lower()
