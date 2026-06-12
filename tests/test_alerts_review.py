from __future__ import annotations

"""Tests for cmd_alerts_review and cmd_alerts_fp_rate commands."""

import argparse
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool_mock(fetch_return=None, execute_return=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock(return_value=execute_return or "INSERT 0 1")
    pool.close = AsyncMock()
    return pool


async def _async_create_pool(pool):
    """Coroutine that returns the given pool mock (simulates asyncpg.create_pool)."""
    return pool


# ---------------------------------------------------------------------------
# cmd_alerts_review — success path
# ---------------------------------------------------------------------------

def test_cmd_alerts_review_success(capsys):
    """cmd_alerts_review inserts into alert_reviews with correct arguments."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review

    _alert_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    args = argparse.Namespace(
        alert_id=_alert_id,
        label="fp",
        category="stale_baseline",
        notes="price was stale",
        reviewed_by="analyst1",
    )

    pool = _make_pool_mock()

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 0
    pool.execute.assert_awaited_once()
    call_args = pool.execute.call_args
    # First positional arg is the SQL string
    sql = call_args[0][0]
    assert "INSERT INTO alert_reviews" in sql
    # Remaining positional args are the bind parameters
    bound = call_args[0][1:]
    assert _alert_id in bound
    assert "fp" in bound


# ---------------------------------------------------------------------------
# cmd_alerts_review — FK violation path
# ---------------------------------------------------------------------------

def test_cmd_alerts_review_fk_violation(capsys):
    """cmd_alerts_review prints 'not found' message and returns 1 on FK violation."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_review

    _alert_id = "00000000-0000-0000-0000-000000000099"
    args = argparse.Namespace(
        alert_id=_alert_id,
        label="tp",
        category=None,
        notes=None,
        reviewed_by=None,
    )

    pool = _make_pool_mock()
    pool.execute = AsyncMock(side_effect=asyncpg.ForeignKeyViolationError())

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_review(args)

    assert rc == 1
    out = capsys.readouterr().out
    assert "not found" in out.lower() or _alert_id in out


# ---------------------------------------------------------------------------
# cmd_alerts_fp_rate — no reviews path
# ---------------------------------------------------------------------------

def test_cmd_alerts_fp_rate_no_reviews(capsys):
    """cmd_alerts_fp_rate prints 'No reviews' message and returns 0 when table is empty."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_fp_rate

    args = argparse.Namespace(since=None, rule=None)

    pool = _make_pool_mock(fetch_return=[])

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
         patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
         patch("pmfi.config.load_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
        rc = cmd_alerts_fp_rate(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "no reviews" in out.lower() or "review" in out.lower()


# ---------------------------------------------------------------------------
# cmd_alerts_fp_rate — with review rows
# ---------------------------------------------------------------------------

def test_cmd_alerts_fp_rate_with_reviews(capsys):
    """cmd_alerts_fp_rate returns 0 and output contains 'FP' and the FP count."""
    import asyncpg
    from pmfi.commands.alerts import cmd_alerts_fp_rate

    args = argparse.Namespace(since=None, rule=None)

    # Simulate rows: 3 FP + 7 TP for large_trade_absolute_v1
    rows = [
        {"label": "fp", "rule_key": "large_trade_absolute_v1", "cnt": 3},
        {"label": "tp", "rule_key": "large_trade_absolute_v1", "cnt": 7},
    ]
    pool = _make_pool_mock(fetch_return=rows)

    def _fake_run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    # Force the rich import to fail so the plain print fallback runs and
    # output is captured by capsys.
    rich_backup = sys.modules.pop("rich.console", None)
    rich_table_backup = sys.modules.pop("rich.table", None)
    sys.modules["rich.console"] = None  # type: ignore[assignment]
    sys.modules["rich.table"] = None  # type: ignore[assignment]
    try:
        with patch("pmfi.commands.alerts.asyncio.run", side_effect=_fake_run), \
             patch.object(asyncpg, "create_pool", side_effect=lambda *a, **kw: _async_create_pool(pool)), \
             patch("pmfi.config.load_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(database=MagicMock(url="postgresql://localhost/test"))
            rc = cmd_alerts_fp_rate(args)
    finally:
        # Restore rich modules
        if rich_backup is not None:
            sys.modules["rich.console"] = rich_backup
        else:
            sys.modules.pop("rich.console", None)
        if rich_table_backup is not None:
            sys.modules["rich.table"] = rich_table_backup
        else:
            sys.modules.pop("rich.table", None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "FP" in out or "fp" in out
    # The FP count (3) must appear somewhere in the output.
    assert "3" in out


# ---------------------------------------------------------------------------
# CLI arg-parse tests (no DB, no asyncio)
# ---------------------------------------------------------------------------

def test_alerts_review_cli_args_parse():
    """'alerts review <uuid> --label fp' parses to alert_id and label='fp'."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    _uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    args = parser.parse_args(["alerts", "review", _uuid, "--label", "fp"])
    assert args.alerts_cmd == "review"
    assert args.alert_id == _uuid
    assert args.label == "fp"


def test_alerts_fp_rate_cli_args_parse():
    """'alerts fp-rate --since 7d --rule large_trade_absolute_v1' parses correctly."""
    from pmfi.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args([
        "alerts", "fp-rate",
        "--since", "7d",
        "--rule", "large_trade_absolute_v1",
    ])
    assert args.alerts_cmd == "fp-rate"
    assert args.since == "7d"
    assert args.rule == "large_trade_absolute_v1"
