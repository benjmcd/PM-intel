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


def test_fetch_kalshi_markets_filters_by_volume():
    """min_volume filter removes low-volume Kalshi markets from results."""
    import asyncio
    from pmfi.markets import fetch_kalshi_markets

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "markets": [
            {"ticker": "KXBTCD-23DEC3100", "title": "Bitcoin > 31k", "volume": 50000, "status": "open"},
            {"ticker": "KXBTCD-23DEC1000", "title": "Bitcoin > 10k", "volume": 100, "status": "open"},
        ],
        "cursor": None,
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
        result = asyncio.run(fetch_kalshi_markets(min_volume=10000))

    assert len(result) == 1
    assert result[0]["ticker"] == "KXBTCD-23DEC3100"


def test_fetch_kalshi_markets_limit_respected():
    """limit parameter caps the returned Kalshi market list."""
    import asyncio
    from pmfi.markets import fetch_kalshi_markets

    all_markets = [{"ticker": f"K-MKT-{i}", "volume": 10000, "title": f"Market {i}"} for i in range(10)]
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={"markets": all_markets, "cursor": None})
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_kalshi_markets(limit=3))

    assert len(result) == 3


def test_discover_cli_accepts_venue_kalshi():
    """markets discover --venue kalshi is a valid CLI invocation."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "discover", "--venue", "kalshi", "--limit", "20"])
    assert args.venue == "kalshi"
    assert args.limit == 20


def test_fetch_kalshi_trades_returns_list():
    """fetch_kalshi_trades returns a list of trade dicts from the API."""
    import asyncio
    from pmfi.markets import fetch_kalshi_trades

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "trades": [
            {"trade_id": "t1", "ticker": "K-MKT-1", "yes_price": 55, "no_price": 45, "count": 100, "created_time": "2024-01-01T12:00:00Z"},
            {"trade_id": "t2", "ticker": "K-MKT-1", "yes_price": 56, "no_price": 44, "count": 50, "created_time": "2024-01-01T12:01:00Z"},
        ],
        "cursor": None,
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
        result = asyncio.run(fetch_kalshi_trades("K-MKT-1", limit=10))

    assert len(result) == 2
    assert result[0]["trade_id"] == "t1"


def test_kalshi_trade_to_raw_event_shape():
    """kalshi_trade_to_raw_event produces a valid RawEvent from a REST trade dict."""
    from pmfi.markets import kalshi_trade_to_raw_event
    trade = {
        "trade_id": "t-xyz",
        "ticker": "KXBTCD-23DEC3100",
        "yes_price": 55,
        "no_price": 45,
        "count": 200,
        "created_time": "2024-01-01T12:00:00Z",
    }
    raw = kalshi_trade_to_raw_event(trade, "KXBTCD-23DEC3100")
    assert raw.venue_code == "kalshi"
    assert raw.source_channel == "rest_trades"
    assert raw.source_event_type == "trade"
    assert raw.source_event_id == "t-xyz"
    assert raw.venue_market_id == "KXBTCD-23DEC3100"
    assert raw.exchange_ts is not None
    assert raw.payload["yes_price"] == 55


def test_fetch_trades_cli_accepts_args():
    """markets fetch-trades CLI args parse correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "fetch-trades", "KXBTCD-23DEC3100", "--limit", "25", "--save-fixtures"])
    assert args.ticker == "KXBTCD-23DEC3100"
    assert args.limit == 25
    assert args.save_fixtures is True
