"""Offline test: compute_market_baselines must NOT write to market_baselines.

After US-05 the deprecated compute_market_baselines() is a pure diagnostic function
that returns computed rows but does NOT call upsert_baseline.  This test verifies
that guarantee without requiring a real DB connection.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_compute_market_baselines_does_not_upsert():
    """compute_market_baselines must not call upsert_baseline under any path."""
    from pmfi.baseline import compute_market_baselines

    fake_rows = [
        {
            "market_id": "aaaabbbb-cccc-dddd-eeee-ffffaaaabbbb",
            "venue_code": "kalshi",
            "venue_market_id": "KX-TEST-001",
            "sample_size": 15,
            "p50_trade_usd": 50.0,
            "p95_trade_usd": 90.0,
            "p99_trade_usd": 120.0,
            "p995_trade_usd": 130.0,
            "median_5m_flow_usd": 500.0,
            "p99_5m_flow_usd": 2000.0,
        }
    ]

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=fake_rows)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    with patch("pmfi.baseline.upsert_baseline") as mock_upsert:
        results = asyncio.run(compute_market_baselines(mock_pool, lookback_seconds=3600))
        mock_upsert.assert_not_called(), (
            "compute_market_baselines must not call upsert_baseline — "
            "it is now a read-only diagnostic function"
        )

    assert len(results) == 1
    assert results[0]["venue_code"] == "kalshi"
    assert results[0]["p99_trade_usd"] == 120.0


def test_compute_market_baselines_returns_list_not_dict():
    """compute_market_baselines returns a list of dicts, not a keyed dict like compute_baselines."""
    from pmfi.baseline import compute_market_baselines

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    result = asyncio.run(compute_market_baselines(mock_pool))
    assert isinstance(result, list), "compute_market_baselines must return a list"


def test_cli_baseline_singular_redirects_to_canonical(capsys):
    """'pmfi baseline compute' (singular) must print a deprecation notice and
    delegate to the canonical _cmd_baselines_compute path, not the old metric_windows path."""
    import argparse
    from unittest.mock import patch

    from pmfi.cli import cmd_baseline

    args = argparse.Namespace(baseline_cmd="compute", lookback_days=7)

    # Patch the canonical handler so it does nothing (avoids DB call)
    with patch("pmfi.cli._cmd_baselines_compute", return_value=0) as mock_canonical:
        rc = cmd_baseline(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "DEPRECATED" in out, "Singular baseline compute must print a deprecation notice"
    mock_canonical.assert_called_once()
    called_args = mock_canonical.call_args[0][0]
    assert called_args.days == 7, "days must be mapped from lookback_days"
    assert called_args.save is False


def test_cli_baseline_singular_list_redirects_to_canonical(capsys):
    """'pmfi baseline list' (singular) must print a deprecation notice and
    delegate to the canonical _cmd_baselines_show path."""
    import argparse
    from unittest.mock import patch

    from pmfi.cli import cmd_baseline

    args = argparse.Namespace(baseline_cmd="list")

    with patch("pmfi.cli._cmd_baselines_show", return_value=0) as mock_show:
        rc = cmd_baseline(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "DEPRECATED" in out, "Singular baseline list must print a deprecation notice"
    mock_show.assert_called_once()
