"""Tests for market discovery module (no live API, no asyncpg required)."""
from unittest.mock import AsyncMock, patch, MagicMock
import pytest


def test_fetch_polymarket_markets_filters_by_min_volume():
    """min_volume filter removes low-volume markets from results."""
    import asyncio
    from pmfi.markets import fetch_polymarket_markets

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "data": [
            {"condition_id": "m1", "question": "Q1", "volume": "50000"},
            {"condition_id": "m2", "question": "Q2", "volume": "500"},
        ],
        "next_cursor": None,
    })
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()

        result = asyncio.run(fetch_polymarket_markets(min_volume=10000))

    assert len(result) == 1
    assert result[0]["condition_id"] == "m1"


def test_fetch_polymarket_markets_limit_respected():
    """limit parameter caps the returned list."""
    import asyncio
    from pmfi.markets import fetch_polymarket_markets

    all_markets = [{"condition_id": f"m{i}", "volume": "100000"} for i in range(10)]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={"data": all_markets, "next_cursor": None})
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_polymarket_markets(limit=3))

    assert len(result) == 3


def test_markets_module_importable():
    import pmfi.markets  # noqa: F401


def test_http_delivery_importable():
    import pmfi.delivery.http  # noqa: F401


def test_delivery_server_importable():
    import pmfi.delivery.server  # noqa: F401
