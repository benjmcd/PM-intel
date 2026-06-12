"""Offline tests for cmd_stats, cmd_dead_letters, and cmd_report.

All DB calls are mocked — no real Postgres required.
"""
from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock, patch


def _make_args(**kwargs) -> argparse.Namespace:
    defaults = {"limit": 20, "since": "24h", "format": "table"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _pool_fetchval_side_effect(counts: dict):
    """Return a side_effect list that answers each fetchval call with a canned count."""
    # cmd_stats calls fetchval 11 times; we just return 0 for each.
    return [0] * 20


# ---------------------------------------------------------------------------
# cmd_stats
# ---------------------------------------------------------------------------

class TestCmdStats:
    def _make_pool(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = AsyncMock(return_value=[])
        pool.close = AsyncMock()
        return pool

    def test_returns_zero_on_success(self, capsys):
        from pmfi.commands.reporting import cmd_stats
        pool = self._make_pool()
        with patch("pmfi.db.create_pool", new=AsyncMock(return_value=pool)):
            with patch("pmfi.db.close_pool", new=AsyncMock()):
                rc = cmd_stats(_make_args())
        assert rc == 0

    def test_returns_one_on_db_error(self, capsys):
        from pmfi.commands.reporting import cmd_stats
        with patch("pmfi.db.create_pool", new=AsyncMock(side_effect=Exception("conn refused"))):
            rc = cmd_stats(_make_args())
        assert rc == 1


# ---------------------------------------------------------------------------
# cmd_dead_letters
# ---------------------------------------------------------------------------

class TestCmdDeadLetters:
    def test_no_dead_letters_returns_zero(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 0
        out = capsys.readouterr().out
        assert "No dead letters" in out

    def test_db_connect_failure_returns_one(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        with patch("asyncpg.create_pool", new=AsyncMock(side_effect=Exception("unreachable"))):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 1

    def test_dead_letters_present_returns_zero(self, capsys):
        from pmfi.commands.reporting import cmd_dead_letters
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{
            "created_at": "2026-01-01 12:00:00",
            "venue_code": "polymarket",
            "failure_stage": "normalization",
            "error_class": "invalid_price_or_size",
            "error_message": "price parse failed",
            "source_channel": "ws",
            "payload_preview": '{"asset_id": "abc"}',
        }])
        pool.close = AsyncMock()
        with patch("asyncpg.create_pool", new=AsyncMock(return_value=pool)):
            rc = cmd_dead_letters(_make_args(limit=20))
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_report
# ---------------------------------------------------------------------------

class TestCmdReport:
    def test_db_unavailable_returns_one(self, capsys):
        from pmfi.commands.reporting import cmd_report
        with patch("pmfi.db.create_pool", new=AsyncMock(side_effect=Exception("no db"))):
            rc = cmd_report(_make_args())
        assert rc == 1
        out = capsys.readouterr().out
        assert "unavailable" in out.lower() or "db" in out.lower()
