"""Tests for PR-3 bug fixes: DB-canonical baselines, stale-baseline SQL guard,
and cmd_ingest preflight exit code.

Each test is designed to FAIL on the old code and PASS on the fixed code.
No database required — all tests are offline/mock-driven.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Fix 1: cmd_replay DB-canonical baselines
# ---------------------------------------------------------------------------

def test_cmd_replay_from_db_passes_none_baselines(tmp_path, monkeypatch):
    """Fix 1: cmd_replay with from_db=True must never forward JSON file baselines.

    Old code loaded config/baselines.json eagerly and passed the dict to
    replay_from_db(). Fixed code always passes baselines=None to the DB path,
    forcing replay.py to load fresh baselines from the DB.

    Even when config/baselines.json exists with data, replay_from_db must
    receive baselines=None.
    """
    # Arrange: create config/baselines.json with stale data under tmp_path
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    stale_baselines = {"poly:mkt": {"p99_trade_usd": 9999}}
    (config_dir / "baselines.json").write_text(
        json.dumps(stale_baselines), encoding="utf-8"
    )

    # Point cli.ROOT at tmp_path so cmd_replay resolves baselines.json there
    monkeypatch.setattr("pmfi.cli.ROOT", tmp_path)

    # Track what baselines arg replay_from_db receives
    captured: dict = {}

    async def _fake_replay_from_db(pool, *, limit, verbose, baselines, **kwargs):
        captured["baselines"] = baselines
        return []

    # Minimal fake config object
    fake_cfg = MagicMock()
    fake_cfg.database.url = "postgresql://fake/db"

    mock_pool = AsyncMock()

    with (
        patch("pmfi.config.load_config", return_value=fake_cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.replay.replay_from_db", side_effect=_fake_replay_from_db),
        patch("pmfi.delivery.stdout.deliver_stdout", new=AsyncMock()),
    ):
        args = argparse.Namespace(
            from_db=True,
            persist=False,
            fixture_dir=None,
            limit=10,
            verbose=False,
        )
        from pmfi.cli import cmd_replay
        cmd_replay(args)

    assert "baselines" in captured, "replay_from_db was not called"
    assert captured["baselines"] is None, (
        f"cmd_replay must pass baselines=None to replay_from_db (DB-canonical), "
        f"but got: {captured['baselines']!r}"
    )


def test_cmd_replay_pure_fixture_may_use_file_baselines(tmp_path, monkeypatch):
    """Fix 1 guard: cmd_replay with from_db=False, persist=False (pure-fixture path)
    is allowed to load baselines from config/baselines.json.

    This is the INTENDED behavior — the JSON fallback is only acceptable when
    no DB is involved.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    file_baselines = {"poly:mkt": {"p99_trade_usd": 42.0}}
    (config_dir / "baselines.json").write_text(
        json.dumps(file_baselines), encoding="utf-8"
    )

    monkeypatch.setattr("pmfi.cli.ROOT", tmp_path)

    captured: dict = {}

    def _fake_replay_fixtures(fixture_dir, *, verbose, baselines):
        captured["baselines"] = baselines
        return []

    with (
        patch("pmfi.replay.replay_fixtures", side_effect=_fake_replay_fixtures),
        patch("pmfi.delivery.stdout.deliver_stdout", new=AsyncMock()),
    ):
        args = argparse.Namespace(
            from_db=False,
            persist=False,
            fixture_dir=str(tmp_path / "fixtures"),
            verbose=False,
        )
        from pmfi.cli import cmd_replay
        cmd_replay(args)

    assert "baselines" in captured, "replay_fixtures was not called"
    assert captured["baselines"] == file_baselines, (
        f"pure-fixture path should load file baselines, got: {captured['baselines']!r}"
    )


# ---------------------------------------------------------------------------
# Fix 2: Stale baseline SQL guard
# ---------------------------------------------------------------------------

def test_fetch_all_baselines_query_filters_stale():
    """Fix 2: fetch_all_baselines SQL must include a staleness filter.

    The fixed query adds:
      AND b.computed_at >= now() - (b.lookback_seconds * 2 || ' seconds')::interval

    This documents the requirement at the source level so any regression
    (removing the WHERE clause) causes this test to fail.
    """
    from pmfi.db.repos.baselines import fetch_all_baselines

    source = inspect.getsource(fetch_all_baselines)

    assert "computed_at" in source, (
        "fetch_all_baselines must reference 'computed_at' in its staleness filter"
    )
    assert "lookback_seconds" in source, (
        "fetch_all_baselines staleness filter must use 'lookback_seconds' "
        "to make the cutoff proportional to the baseline window"
    )
    # Ensure the filter is a >= comparison (not just a mention of the column)
    assert ">=" in source, (
        "fetch_all_baselines must use >= to filter out stale baselines "
        "(computed_at >= now() - ...)"
    )


# ---------------------------------------------------------------------------
# Fix 3: cmd_ingest preflight exit code
# ---------------------------------------------------------------------------

def test_cmd_ingest_preflight_no_watched_markets_exits_nonzero():
    """Fix 3: cmd_ingest must return 1 when no watched markets exist.

    Old code discarded the return value of asyncio.run(_run()), so the
    preflight 'return 1' inside _run() was silently swallowed and
    cmd_ingest returned 0 (success) even on misconfiguration.

    Fixed code: rc = asyncio.run(_run()); if rc: return rc

    This test verifies that fetch_watched_markets returning [] causes
    cmd_ingest to propagate exit code 1.
    """
    # Minimal fake config with required attributes
    fake_cfg = MagicMock()
    fake_cfg.database.url = "postgresql://fake/db"
    fake_cfg.features.enable_polymarket_live = False
    fake_cfg.features.enable_kalshi_live = False
    fake_cfg.alerts.default_delivery = "stdout"

    # Mock pool that supports `async with pool.acquire() as conn:`
    mock_conn = AsyncMock()
    mock_acquire_cm = MagicMock()
    mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)

    mock_pool = AsyncMock()
    mock_pool.acquire = MagicMock(return_value=mock_acquire_cm)

    with (
        patch("pmfi.config.load_config", return_value=fake_cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.db.migrations.startup_maintenance", new=AsyncMock()),
        patch("pmfi.baseline.load_baselines", new=AsyncMock(return_value={})),
        patch(
            "pmfi.db.repos.markets.fetch_watched_markets",
            new=AsyncMock(return_value=[]),  # no watched markets → preflight fails
        ),
        patch("pmfi.markets.load_asset_id_mapping", new=AsyncMock(return_value={})),
    ):
        from pmfi.cli import cmd_ingest

        args = argparse.Namespace(
            venue=["polymarket", "kalshi"],
            dry_run=False,
            log_level="WARNING",
        )
        rc = cmd_ingest(args)

    assert rc == 1, (
        f"cmd_ingest must return 1 when no watched markets exist (preflight failure), "
        f"but returned: {rc!r}"
    )
