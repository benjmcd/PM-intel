"""Offline test: sync_kalshi_markets forwards the venue status field to upsert_market_full."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_sync_kalshi_markets_forwards_status():
    """sync_kalshi_markets must pass status= from the API dict to upsert_market_full."""
    from pmfi.markets import sync_kalshi_markets

    fake_market = {
        "ticker": "KALSHI-TEST-001",
        "title": "Test market",
        "event_ticker": "TEST",
        "status": "settled",
        "close_time": None,
    }

    mock_upsert_market = AsyncMock(return_value="market-id-abc")
    mock_upsert_outcome = AsyncMock(return_value=None)
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("pmfi.markets.fetch_kalshi_markets", AsyncMock(return_value=[fake_market])),
        patch("pmfi.db.repos.markets.upsert_market_full", mock_upsert_market),
        patch("pmfi.db.repos.markets.upsert_market_outcome", mock_upsert_outcome),
    ):
        # Re-patch inside the function's local import scope
        import pmfi.markets as _markets_mod
        orig_sync = _markets_mod.sync_kalshi_markets

        async def _run():
            # Patch the symbols as they are imported inside sync_kalshi_markets
            with (
                patch(
                    "pmfi.markets.fetch_kalshi_markets",
                    AsyncMock(return_value=[fake_market]),
                ),
            ):
                # We need to patch inside the function's local namespace.
                # sync_kalshi_markets does: from pmfi.db.repos.markets import upsert_market_full
                # so we patch the module-level target.
                import pmfi.db.repos.markets as _repo
                _repo_upsert = AsyncMock(return_value="market-id-abc")
                _repo_outcome = AsyncMock(return_value=None)
                with (
                    patch.object(_repo, "upsert_market_full", _repo_upsert),
                    patch.object(_repo, "upsert_market_outcome", _repo_outcome),
                ):
                    await orig_sync(mock_pool, limit=1)
                    assert _repo_upsert.called, "upsert_market_full was not called"
                    call_kwargs = _repo_upsert.call_args.kwargs
                    assert "status" in call_kwargs, (
                        f"upsert_market_full was not called with status=; kwargs={call_kwargs}"
                    )
                    assert call_kwargs["status"] == "settled", (
                        f"Expected status='settled', got {call_kwargs['status']!r}"
                    )

        asyncio.run(_run())


def test_sync_kalshi_market_upserts_single_market_outcomes_volume_and_watch():
    """sync_kalshi_market reuses Kalshi market fields and can mark the market watched."""
    from pmfi.markets import sync_kalshi_market

    fake_market = {
        "ticker": "KALSHI-TEST-ONE",
        "title": "Single ticker test market",
        "event_ticker": "TEST-EVENT",
        "category": "fallback-category",
        "status": "open",
        "close_time": "2026-06-30T12:00:00Z",
        "volume": None,
        "volume_fp": "12345.67",
    }

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    async def _run():
        import pmfi.db.repos.markets as _repo

        upsert_market = AsyncMock(return_value="market-id-one")
        upsert_outcome = AsyncMock(return_value=None)
        set_watched = AsyncMock(return_value=True)
        with (
            patch("pmfi.markets.fetch_kalshi_market", AsyncMock(return_value=fake_market)),
            patch.object(_repo, "upsert_market_full", upsert_market),
            patch.object(_repo, "upsert_market_outcome", upsert_outcome),
            patch.object(_repo, "set_market_watched", set_watched),
        ):
            count = await sync_kalshi_market(mock_pool, "KALSHI-TEST-ONE", watched=True)

        assert count == 1
        upsert_market.assert_awaited_once()
        market_kwargs = upsert_market.call_args.kwargs
        assert market_kwargs["venue_code"] == "kalshi"
        assert market_kwargs["venue_market_id"] == "KALSHI-TEST-ONE"
        assert market_kwargs["title"] == "Single ticker test market"
        assert market_kwargs["category"] == "TEST-EVENT"
        assert market_kwargs["status"] == "open"
        assert market_kwargs["raw_metadata"] is fake_market
        assert market_kwargs["volume"] == 12345.67
        assert market_kwargs["close_ts"].isoformat() == "2026-06-30T12:00:00+00:00"

        assert upsert_outcome.await_count == 2
        outcome_keys = [call.kwargs["outcome_key"] for call in upsert_outcome.await_args_list]
        assert outcome_keys == ["yes", "no"]
        outcome_ids = [call.kwargs["venue_outcome_id"] for call in upsert_outcome.await_args_list]
        assert outcome_ids == ["KALSHI-TEST-ONE_yes", "KALSHI-TEST-ONE_no"]

        set_watched.assert_awaited_once_with(
            mock_conn,
            venue_code="kalshi",
            venue_market_id="KALSHI-TEST-ONE",
            watched=True,
        )

    asyncio.run(_run())
