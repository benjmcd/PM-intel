"""Tests for market discovery module (no live API, no asyncpg required)."""
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MarketRepoConn:
    def __init__(self):
        self.markets = {}
        self.outcomes = {}
        self.next_market_id = 1
        self.next_outcome_id = 1

    async def fetchrow(self, sql, *args):
        normalized = " ".join(sql.split()).lower()
        if "insert into markets" in normalized:
            venue_code, venue_market_id, title, status, category, close_ts, meta_json = args
            key = (venue_code, venue_market_id)
            if key not in self.markets:
                self.markets[key] = {
                    "market_id": f"market-{self.next_market_id}",
                    "venue_code": venue_code,
                    "venue_market_id": venue_market_id,
                    "watched": False,
                }
                self.next_market_id += 1
            row = self.markets[key]
            row.update(
                {
                    "title": title,
                    "status": status,
                    "category": category,
                    "close_ts": close_ts,
                    "raw_metadata": json.loads(meta_json) if meta_json else None,
                }
            )
            return {"market_id": row["market_id"]}
        if "insert into market_outcomes" in normalized:
            market_id, venue_code, venue_outcome_id, outcome_key, outcome_label, meta_json = args
            key = (market_id, outcome_key.lower())
            if key not in self.outcomes:
                self.outcomes[key] = {"outcome_id": f"outcome-{self.next_outcome_id}"}
                self.next_outcome_id += 1
            self.outcomes[key].update(
                {
                    "market_id": market_id,
                    "venue_code": venue_code,
                    "venue_outcome_id": venue_outcome_id,
                    "outcome_key": outcome_key.lower(),
                    "outcome_label": outcome_label,
                    "raw_metadata": json.loads(meta_json) if meta_json else {},
                    "is_active": True,
                }
            )
            return {"outcome_id": self.outcomes[key]["outcome_id"]}
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")

    async def execute(self, sql, *args):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("update markets set watched="):
            watched, venue_code, venue_market_id = args
            row = self.markets.get((venue_code, venue_market_id))
            if not row:
                return "UPDATE 0"
            row["watched"] = watched
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute SQL: {sql}")

    async def fetch(self, sql, *args):
        normalized = " ".join(sql.split()).lower()
        if "from market_outcomes mo join markets m" in normalized:
            rows = []
            by_market_id = {row["market_id"]: row for row in self.markets.values()}
            for outcome in self.outcomes.values():
                market = by_market_id[outcome["market_id"]]
                if outcome["venue_code"] != "polymarket" or not outcome["venue_outcome_id"]:
                    continue
                rows.append(
                    {
                        "venue_outcome_id": outcome["venue_outcome_id"],
                        "outcome_key": outcome["outcome_key"],
                        "outcome_label": outcome["outcome_label"],
                        "market_id": market["market_id"],
                        "venue_market_id": market["venue_market_id"],
                        "venue_code": market["venue_code"],
                    }
                )
            return sorted(rows, key=lambda row: row["venue_outcome_id"])
        if "from markets where watched=true" in normalized:
            venue_code = args[0] if args else None
            rows = [
                {
                    "market_id": row["market_id"],
                    "venue_code": row["venue_code"],
                    "venue_market_id": row["venue_market_id"],
                    "title": row["title"],
                    "category": row["category"],
                    "status": row["status"],
                }
                for row in self.markets.values()
                if row["watched"] and (venue_code is None or row["venue_code"] == venue_code)
            ]
            return sorted(rows, key=lambda row: (row["venue_code"], row["venue_market_id"]))
        raise AssertionError(f"unexpected fetch SQL: {sql}")


class _MarketRepoPool:
    def __init__(self):
        self.conn = _MarketRepoConn()

    def acquire(self):
        return _Acquire(self.conn)


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


def test_polymarket_discovery_syncs_market_outcomes_and_watch_mapping(monkeypatch):
    """Discovery payloads become DB rows; watched state comes from the existing DB helper."""
    from pmfi.db.repos.markets import fetch_watched_markets, set_market_watched
    from pmfi.markets import load_asset_id_mapping, sync_polymarket_markets

    payload = {
        "condition_id": "poly-condition-1",
        "question": "Will the local bootstrap path have token mappings?",
        "category": "ops",
        "enabled": True,
        "end_date_iso": "2026-06-30T12:00:00Z",
        "tokens": [
            {"token_id": "poly-token-yes", "outcome": "Yes"},
            {"asset_id": "poly-token-no", "outcome": "No"},
        ],
    }

    async def fetch_markets(**_kwargs):
        return [payload]

    pool = _MarketRepoPool()
    monkeypatch.setattr("pmfi.markets.fetch_polymarket_markets", fetch_markets)

    synced = asyncio.run(sync_polymarket_markets(pool, limit=5))
    market = pool.conn.markets[("polymarket", "poly-condition-1")]

    assert synced == 1
    assert market["title"] == "Will the local bootstrap path have token mappings?"
    assert market["category"] == "ops"
    assert market["raw_metadata"] == payload
    assert market["watched"] is False
    assert set(pool.conn.outcomes) == {
        ("market-1", "yes"),
        ("market-1", "no"),
    }
    assert pool.conn.outcomes[("market-1", "yes")]["venue_outcome_id"] == "poly-token-yes"
    assert pool.conn.outcomes[("market-1", "no")]["venue_outcome_id"] == "poly-token-no"

    watched = asyncio.run(
        set_market_watched(
            pool.conn,
            venue_code="polymarket",
            venue_market_id="poly-condition-1",
            watched=True,
        )
    )
    watched_rows = asyncio.run(fetch_watched_markets(pool.conn, venue_code="polymarket"))
    asset_map = asyncio.run(load_asset_id_mapping(pool))

    assert watched is True
    assert [row["venue_market_id"] for row in watched_rows] == ["poly-condition-1"]
    assert asset_map == {
        "poly-token-no": {
            "market_id": "market-1",
            "venue_market_id": "poly-condition-1",
            "venue_code": "polymarket",
            "outcome_key": "no",
            "outcome_label": "No",
        },
        "poly-token-yes": {
            "market_id": "market-1",
            "venue_market_id": "poly-condition-1",
            "venue_code": "polymarket",
            "outcome_key": "yes",
            "outcome_label": "Yes",
        },
    }


def test_kalshi_discovery_syncs_market_outcomes_and_watch_tickers(monkeypatch):
    """Kalshi discovery rows feed the watched ticker path used by ingest planning."""
    from pmfi.db.repos.markets import fetch_watched_markets, set_market_watched
    from pmfi.markets import sync_kalshi_markets

    payload = {
        "ticker": "KXLOCAL-26JUN",
        "title": "Will local-only bootstrap stay DB canonical?",
        "event_ticker": "KXLOCAL",
        "status": "open",
        "close_time": "2026-06-30T12:00:00Z",
    }

    async def fetch_markets(**_kwargs):
        return [payload]

    pool = _MarketRepoPool()
    monkeypatch.setattr("pmfi.markets.fetch_kalshi_markets", fetch_markets)

    synced = asyncio.run(sync_kalshi_markets(pool, limit=5))
    market = pool.conn.markets[("kalshi", "KXLOCAL-26JUN")]

    assert synced == 1
    assert market["title"] == "Will local-only bootstrap stay DB canonical?"
    assert market["category"] == "KXLOCAL"
    assert market["raw_metadata"] == payload
    assert market["watched"] is False
    assert {
        outcome["venue_outcome_id"]
        for outcome in pool.conn.outcomes.values()
        if outcome["market_id"] == "market-1"
    } == {"KXLOCAL-26JUN_yes", "KXLOCAL-26JUN_no"}

    watched = asyncio.run(
        set_market_watched(
            pool.conn,
            venue_code="kalshi",
            venue_market_id="KXLOCAL-26JUN",
            watched=True,
        )
    )
    watched_rows = asyncio.run(fetch_watched_markets(pool.conn, venue_code="kalshi"))
    kalshi_tickers = [row["venue_market_id"] for row in watched_rows]

    assert watched is True
    assert kalshi_tickers == ["KXLOCAL-26JUN"]


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
