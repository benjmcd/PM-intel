"""Tests for market discovery module (no live API, no asyncpg required)."""
from unittest.mock import AsyncMock, patch, MagicMock
import pytest


def _make_gamma_market(condition_id, question, volume_num, token_ids, outcomes, slug="", end_date=None):
    """Helper: build a Gamma API market dict with JSON-encoded clobTokenIds and outcomes."""
    import json
    return {
        "conditionId": condition_id,
        "question": question,
        "volumeNum": volume_num,
        "clobTokenIds": json.dumps(token_ids),
        "outcomes": json.dumps(outcomes),
        "slug": slug,
        "endDate": end_date,
        "active": True,
        "closed": False,
    }


def _make_gamma_mock_session(gamma_markets):
    """Return a mock aiohttp session whose .get() yields the given Gamma array."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value=gamma_markets)
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def test_fetch_polymarket_markets_filters_by_min_volume():
    """min_volume filter removes low-volume markets from results."""
    import asyncio
    from pmfi.markets import fetch_polymarket_markets

    gamma_markets = [
        _make_gamma_market("m1", "Q1", 50000.0, ["tok1", "tok2"], ["Yes", "No"]),
        _make_gamma_market("m2", "Q2", 500.0, ["tok3", "tok4"], ["Yes", "No"]),
    ]
    mock_session = _make_gamma_mock_session(gamma_markets)

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

    gamma_markets = [
        _make_gamma_market(f"m{i}", f"Q{i}", 100000.0, [f"tok{i}a", f"tok{i}b"], ["Yes", "No"])
        for i in range(10)
    ]
    mock_session = _make_gamma_mock_session(gamma_markets)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_polymarket_markets(limit=3))

    assert len(result) == 3


def test_fetch_polymarket_markets_multi_outcome():
    """A Gamma market with 3 outcomes produces 3 tokens, JSON-string parsing works."""
    import asyncio
    from pmfi.markets import fetch_polymarket_markets

    gamma_markets = [
        _make_gamma_market(
            "m-multi", "Who wins?", 75000.0,
            ["tok-yes", "tok-no", "tok-maybe"],
            ["Yes", "No", "Maybe"],
            slug="who-wins",
        )
    ]
    mock_session = _make_gamma_mock_session(gamma_markets)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_polymarket_markets())

    assert len(result) == 1
    m = result[0]
    assert m["condition_id"] == "m-multi"
    assert len(m["tokens"]) == 3
    assert m["tokens"][0] == {"token_id": "tok-yes", "outcome": "Yes"}
    assert m["tokens"][1] == {"token_id": "tok-no", "outcome": "No"}
    assert m["tokens"][2] == {"token_id": "tok-maybe", "outcome": "Maybe"}


def test_fetch_polymarket_markets_malformed_clob_token_ids_skipped():
    """A market with unparseable clobTokenIds is skipped without crashing."""
    import asyncio
    from pmfi.markets import fetch_polymarket_markets

    gamma_markets = [
        {
            "conditionId": "m-bad",
            "question": "Bad market",
            "volumeNum": 99999.0,
            "clobTokenIds": "NOT VALID JSON [[[",
            "outcomes": '["Yes", "No"]',
            "slug": "bad",
            "active": True,
            "closed": False,
        },
        _make_gamma_market("m-good", "Good market", 50000.0, ["tok1", "tok2"], ["Yes", "No"]),
    ]
    mock_session = _make_gamma_mock_session(gamma_markets)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_polymarket_markets())

    # Bad market skipped; good market still returned
    assert len(result) == 1
    assert result[0]["condition_id"] == "m-good"


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


def test_fetch_kalshi_market_uses_single_market_endpoint():
    """fetch_kalshi_market returns the market dict from /markets/{ticker}."""
    import asyncio
    from pmfi.markets import fetch_kalshi_market

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "market": {
            "ticker": "KXBTC-26JUN-100000",
            "title": "Bitcoin above 100k?",
            "status": "open",
        }
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
        result = asyncio.run(fetch_kalshi_market("KXBTC-26JUN-100000"))

    assert result["ticker"] == "KXBTC-26JUN-100000"
    assert mock_session.get.call_args.args[0].endswith("/markets/KXBTC-26JUN-100000")
    assert mock_session.get.call_args.kwargs["timeout"] == mock_aiohttp.ClientTimeout.return_value


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
    request_params = mock_session.get.call_args.kwargs["params"]
    assert request_params["ticker"] == "K-MKT-1"


def test_fetch_kalshi_trades_all_market_omits_ticker_and_includes_min_ts():
    """All-market Kalshi trade discovery omits ticker and includes min_ts."""
    import asyncio
    from pmfi.markets import fetch_kalshi_trades

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={
        "trades": [
            {"trade_id": "t1", "ticker": "K-MKT-1", "created_time": "2026-01-01T12:00:00Z"},
            {"trade_id": "t2", "ticker": "K-MKT-2", "created_time": "2026-01-01T12:01:00Z"},
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
        result = asyncio.run(fetch_kalshi_trades(ticker=None, limit=10, min_ts=1_750_000_000))

    assert len(result) == 2
    request_params = mock_session.get.call_args.kwargs["params"]
    assert "ticker" not in request_params
    assert request_params["min_ts"] == 1_750_000_000
    assert request_params["limit"] == 10


def test_kalshi_trade_to_raw_event_shape():
    """kalshi_trade_to_raw_event produces a valid RawEvent from a REST trade dict."""
    from datetime import datetime, timedelta, timezone
    from pmfi.markets import kalshi_trade_to_raw_event
    # Use a recent timestamp within the parse_ts sanity guard window (<30d past).
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    trade = {
        "trade_id": "t-xyz",
        "ticker": "KXBTCD-23DEC3100",
        "yes_price": 55,
        "no_price": 45,
        "count": 200,
        "created_time": recent_ts,
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


def test_recent_trades_cli_accepts_args():
    """markets recent-trades CLI args parse correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "markets", "recent-trades",
        "--limit", "25",
        "--since-minutes", "120",
        "--format", "json",
        "--force",
    ])
    assert args.markets_cmd == "recent-trades"
    assert args.limit == 25
    assert args.since_minutes == 120
    assert args.format == "json"
    assert args.force is True


def test_refresh_watchlist_cli_accepts_explicit_write_args():
    """markets refresh-watchlist parses the repeatable Kalshi operator workflow."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "markets", "refresh-watchlist",
        "--limit", "25",
        "--since-minutes", "30",
        "--top", "4",
        "--format", "json",
        "--force",
        "--sync",
        "--watch",
        "--replace-watch",
    ])
    assert args.markets_cmd == "refresh-watchlist"
    assert args.limit == 25
    assert args.since_minutes == 30
    assert args.top == 4
    assert args.format == "json"
    assert args.force is True
    assert args.sync is True
    assert args.watch is True
    assert args.replace_watch is True


def test_sync_one_cli_accepts_kalshi_watch_args():
    """markets sync-one accepts a Kalshi ticker and optional watch flag."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args([
        "markets", "sync-one", "KXBTC-26JUN-100000",
        "--venue", "kalshi",
        "--watch",
    ])
    assert args.markets_cmd == "sync-one"
    assert args.ticker == "KXBTC-26JUN-100000"
    assert args.venue == "kalshi"
    assert args.watch is True


def test_sync_one_command_syncs_and_prints_success(capsys):
    """sync-one calls the single-market sync helper and reports watch state."""
    import argparse
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_pool = MagicMock()
    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    args = argparse.Namespace(ticker="KXBTC-26JUN-100000", venue="kalshi", watch=True)

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.markets.sync_kalshi_market", new=AsyncMock(return_value=1)) as sync_one,
    ):
        from pmfi.commands.markets import _cmd_markets_sync_one
        rc = _cmd_markets_sync_one(args)

    assert rc == 0
    sync_one.assert_awaited_once_with(mock_pool, "KXBTC-26JUN-100000", watched=True)
    out = capsys.readouterr().out
    assert "Synced kalshi:KXBTC-26JUN-100000" in out
    assert "marked watched" in out


def test_recent_trades_json_aggregates_by_ticker(capsys):
    """recent-trades JSON is parseable and grouped by ticker with sample fields."""
    import argparse
    import json
    from unittest.mock import AsyncMock, patch

    trades = [
        {
            "ticker": "K-MKT-1",
            "trade_id": "old",
            "created_time": "2026-01-01T12:00:00Z",
            "count_fp": 100,
            "yes_price_dollars": "0.41",
            "no_price_dollars": "0.59",
        },
        {
            "ticker": "K-MKT-1",
            "trade_id": "new",
            "created_time": "2026-01-01T12:05:00Z",
            "count_fp": 200,
            "yes_price_dollars": "0.42",
            "no_price_dollars": "0.58",
        },
        {
            "ticker": "K-MKT-2",
            "trade_id": "other",
            "created_time": "2026-01-01T12:03:00Z",
            "count": 3,
            "yes_price": 42,
        },
    ]
    args = argparse.Namespace(limit=50, since_minutes=60, format="json", force=True)

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)) as fetch:
        from pmfi.commands.markets import _cmd_markets_recent_trades
        rc = _cmd_markets_recent_trades(args)

    assert rc == 0
    fetch.assert_awaited_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "ticker": "K-MKT-1",
            "trade_count": 2,
            "first_trade_at": "2026-01-01T12:00:00Z",
            "last_trade_at": "2026-01-01T12:05:00Z",
            "sample_trade_id": "new",
            "sample_count": 200,
            "sample_yes_price": "0.42",
            "sample_no_price": "0.58",
            "follow_up": "pmfi markets sync-one K-MKT-1 --venue kalshi --watch",
        },
        {
            "ticker": "K-MKT-2",
            "trade_count": 1,
            "first_trade_at": "2026-01-01T12:03:00Z",
            "last_trade_at": "2026-01-01T12:03:00Z",
            "sample_trade_id": "other",
            "sample_count": 3,
            "sample_yes_price": 42,
            "sample_no_price": None,
            "follow_up": "pmfi markets sync-one K-MKT-2 --venue kalshi --watch",
        },
    ]


def test_recent_trades_requires_live_gate(capsys, monkeypatch):
    """recent-trades refuses live network access unless gated or forced."""
    import argparse
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(limit=10, since_minutes=30, format="table", force=False)

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock()) as fetch:
        from pmfi.commands.markets import _cmd_markets_recent_trades
        rc = _cmd_markets_recent_trades(args)

    assert rc == 1
    assert "PMFI_ENABLE_LIVE" in capsys.readouterr().out
    fetch.assert_not_called()


def test_recent_trades_table_is_read_only_and_prints_fetch_followup(capsys, monkeypatch):
    """recent-trades table output does not write files or touch the DB."""
    import argparse
    import builtins
    from unittest.mock import AsyncMock, MagicMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(limit=10, since_minutes=30, format="table", force=True)
    trades = [
        {"ticker": "K-MKT-1", "trade_id": "t1", "created_time": "2026-01-01T12:00:00Z"},
    ]

    _real_open = builtins.open

    def _guard_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in ("w", "a", "x", "+")):
            raise AssertionError(f"recent-trades must not open for write: {file!r} mode={mode!r}")
        return _real_open(file, mode, *a, **k)

    _no_write = MagicMock(side_effect=AssertionError("recent-trades must not write files"))

    with (
        patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)),
        patch("pmfi.db.create_pool", new=AsyncMock(side_effect=AssertionError("recent-trades must not use DB"))),
        patch("builtins.open", _guard_open),
        patch("pathlib.Path.write_text", _no_write),
        patch("pathlib.Path.write_bytes", _no_write),
    ):
        from pmfi.commands.markets import _cmd_markets_recent_trades
        rc = _cmd_markets_recent_trades(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "pmfi markets sync-one K-MKT-1 --venue kalshi --watch" in out
    assert "pmfi markets watch" not in out
    assert "recent-trades is read-only and does not sync markets" in out


def test_refresh_watchlist_dry_run_is_read_only_json(capsys, monkeypatch):
    """refresh-watchlist plans top recent tickers without DB writes by default."""
    import argparse
    import json
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    trades = [
        {"ticker": "K-MKT-1", "trade_id": "t1", "created_time": "2026-01-01T12:00:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "t2", "created_time": "2026-01-01T12:05:00Z"},
        {"ticker": "K-MKT-2", "trade_id": "t3", "created_time": "2026-01-01T12:03:00Z"},
    ]
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="json",
        force=True,
        sync=False,
        watch=False,
    )

    with (
        patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)) as fetch,
        patch("pmfi.db.create_pool", new=AsyncMock(side_effect=AssertionError("dry-run must not use DB"))),
        patch("pmfi.markets.sync_kalshi_market", new=AsyncMock(side_effect=AssertionError("dry-run must not sync"))),
    ):
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 0
    fetch.assert_awaited_once()
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry-run"
    assert payload["selected_tickers"] == ["K-MKT-1", "K-MKT-2"]
    assert payload["synced"] == []
    assert payload["watch"] is False


def test_refresh_watchlist_syncs_and_watches_top_tickers(capsys, monkeypatch):
    """refresh-watchlist --sync --watch syncs only the selected top recent tickers."""
    import argparse
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, call, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    mock_pool = MagicMock()
    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    trades = [
        {"ticker": "K-MKT-2", "trade_id": "b1", "created_time": "2026-01-01T12:02:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a1", "created_time": "2026-01-01T12:00:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a2", "created_time": "2026-01-01T12:05:00Z"},
    ]
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=True,
        sync=True,
        watch=True,
    )

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)) as create_pool,
        patch("pmfi.db.close_pool", new=AsyncMock()) as close_pool,
        patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)),
        patch("pmfi.markets.sync_kalshi_market", new=AsyncMock(return_value=1)) as sync_one,
    ):
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 0
    create_pool.assert_awaited_once_with("postgresql://unit-test")
    sync_one.assert_has_awaits([
        call(mock_pool, "K-MKT-1", watched=True),
        call(mock_pool, "K-MKT-2", watched=True),
    ])
    close_pool.assert_awaited_once_with(mock_pool)
    out = capsys.readouterr().out
    assert "Synced 2/2 selected Kalshi market(s)" in out
    assert "marked watched" in out


def test_refresh_watchlist_replace_watch_unwatches_stale_kalshi(capsys, monkeypatch):
    """--replace-watch scopes the Kalshi watchlist to selected recent tickers."""
    import argparse
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, call, patch

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    mock_pool = _Pool()
    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    trades = [
        {"ticker": "K-MKT-2", "trade_id": "b1", "created_time": "2026-01-01T12:02:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a1", "created_time": "2026-01-01T12:00:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a2", "created_time": "2026-01-01T12:05:00Z"},
    ]
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=True,
        sync=True,
        watch=True,
        replace_watch=True,
    )

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)) as create_pool,
        patch("pmfi.db.close_pool", new=AsyncMock()) as close_pool,
        patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)),
        patch("pmfi.markets.sync_kalshi_market", new=AsyncMock(return_value=1)) as sync_one,
        patch(
            "pmfi.db.repos.markets.fetch_watched_markets",
            new=AsyncMock(return_value=[
                {"venue_market_id": "K-MKT-1"},
                {"venue_market_id": "K-MKT-2"},
                {"venue_market_id": "K-OLD"},
            ]),
        ) as fetch_watched,
        patch("pmfi.db.repos.markets.set_markets_watched_bulk", new=AsyncMock(return_value=1)) as set_bulk,
    ):
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 0
    create_pool.assert_awaited_once_with("postgresql://unit-test")
    sync_one.assert_has_awaits([
        call(mock_pool, "K-MKT-1", watched=True),
        call(mock_pool, "K-MKT-2", watched=True),
    ])
    fetch_watched.assert_awaited_once()
    set_bulk.assert_awaited_once()
    assert set_bulk.call_args.kwargs["venue_code"] == "kalshi"
    assert set_bulk.call_args.kwargs["venue_market_ids"] == ["K-OLD"]
    assert set_bulk.call_args.kwargs["watched"] is False
    close_pool.assert_awaited_once_with(mock_pool)
    out = capsys.readouterr().out
    assert "Synced 2/2 selected Kalshi market(s)" in out
    assert "Unwatched 1 stale Kalshi market(s)" in out


def test_refresh_watchlist_replace_watch_skips_unwatch_when_selected_sync_fails(capsys, monkeypatch):
    """--replace-watch must not narrow the watchlist after a partial sync failure."""
    import argparse
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, call, patch

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    mock_pool = _Pool()
    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    trades = [
        {"ticker": "K-MKT-2", "trade_id": "b1", "created_time": "2026-01-01T12:02:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a1", "created_time": "2026-01-01T12:00:00Z"},
        {"ticker": "K-MKT-1", "trade_id": "a2", "created_time": "2026-01-01T12:05:00Z"},
    ]
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=True,
        sync=True,
        watch=True,
        replace_watch=True,
    )

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()) as close_pool,
        patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock(return_value=trades)),
        patch("pmfi.markets.sync_kalshi_market", new=AsyncMock(side_effect=[1, 0])) as sync_one,
        patch("pmfi.db.repos.markets.fetch_watched_markets", new=AsyncMock()) as fetch_watched,
        patch("pmfi.db.repos.markets.set_markets_watched_bulk", new=AsyncMock()) as set_bulk,
    ):
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 1
    sync_one.assert_has_awaits([
        call(mock_pool, "K-MKT-1", watched=True),
        call(mock_pool, "K-MKT-2", watched=True),
    ])
    fetch_watched.assert_not_awaited()
    set_bulk.assert_not_awaited()
    close_pool.assert_awaited_once_with(mock_pool)
    out = capsys.readouterr().out
    assert "Synced 1/2 selected Kalshi market(s)" in out
    assert "Skipped replace-watch because not all selected Kalshi markets synced." in out
    assert "Failed to sync: K-MKT-2" in out


def test_refresh_watchlist_watch_requires_sync(capsys, monkeypatch):
    """--watch fails closed unless --sync also makes the write explicit."""
    import argparse
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=True,
        sync=False,
        watch=True,
    )

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock()) as fetch:
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 1
    assert "--watch requires --sync" in capsys.readouterr().out
    fetch.assert_not_called()


def test_refresh_watchlist_replace_watch_requires_sync_and_watch(capsys, monkeypatch):
    """--replace-watch cannot mutate DB unless sync/watch are both explicit."""
    import argparse
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=True,
        sync=True,
        watch=False,
        replace_watch=True,
    )

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock()) as fetch:
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 1
    assert "--replace-watch requires --sync --watch" in capsys.readouterr().out
    fetch.assert_not_called()


def test_refresh_watchlist_requires_live_gate(capsys, monkeypatch):
    """refresh-watchlist refuses live network access unless gated or forced."""
    import argparse
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=2,
        format="table",
        force=False,
        sync=False,
        watch=False,
    )

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock()) as fetch:
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 1
    out = capsys.readouterr().out
    assert "PMFI_ENABLE_LIVE" in out
    assert "refresh-watchlist" in out
    fetch.assert_not_called()


def test_refresh_watchlist_rejects_nonpositive_top_before_fetch(capsys, monkeypatch):
    """--top must be positive and fails before a live fetch."""
    import argparse
    from unittest.mock import AsyncMock, patch

    monkeypatch.delenv("PMFI_ENABLE_LIVE", raising=False)
    args = argparse.Namespace(
        limit=10,
        since_minutes=30,
        top=0,
        format="table",
        force=True,
        sync=False,
        watch=False,
    )

    with patch("pmfi.markets.fetch_kalshi_trades", new=AsyncMock()) as fetch:
        from pmfi.commands.markets import _cmd_markets_refresh_watchlist
        rc = _cmd_markets_refresh_watchlist(args)

    assert rc == 1
    assert "--top must be a positive integer" in capsys.readouterr().out
    fetch.assert_not_called()


def test_fetch_kalshi_trades_max_pages_stops_after_one_page():
    """fetch_kalshi_trades with max_pages=1 stops after one page even when cursor is present."""
    import asyncio
    from pmfi.markets import fetch_kalshi_trades

    # Page 1 returns 2 trades + a cursor indicating a second page exists.
    page1_response = MagicMock()
    page1_response.raise_for_status = MagicMock()
    page1_response.json = AsyncMock(return_value={
        "trades": [
            {"trade_id": "p1-t1", "ticker": "K-MKT-1"},
            {"trade_id": "p1-t2", "ticker": "K-MKT-1"},
        ],
        "cursor": "page2cursor",
    })
    page1_response.__aenter__ = AsyncMock(return_value=page1_response)
    page1_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = page1_response
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_kalshi_trades("K-MKT-1", limit=100, max_pages=1))

    # Only one page fetched — session.get called exactly once.
    assert mock_session.get.call_count == 1
    assert len(result) == 2
    assert result[0]["trade_id"] == "p1-t1"


def test_fetch_kalshi_trades_no_max_pages_follows_cursor():
    """fetch_kalshi_trades with max_pages=None follows cursor to second page."""
    import asyncio
    from pmfi.markets import fetch_kalshi_trades

    page1_response = MagicMock()
    page1_response.raise_for_status = MagicMock()
    page1_response.json = AsyncMock(return_value={
        "trades": [{"trade_id": "p1-t1"}],
        "cursor": "page2cursor",
    })
    page1_response.__aenter__ = AsyncMock(return_value=page1_response)
    page1_response.__aexit__ = AsyncMock(return_value=False)

    page2_response = MagicMock()
    page2_response.raise_for_status = MagicMock()
    page2_response.json = AsyncMock(return_value={
        "trades": [{"trade_id": "p2-t1"}],
        "cursor": None,
    })
    page2_response.__aenter__ = AsyncMock(return_value=page2_response)
    page2_response.__aexit__ = AsyncMock(return_value=False)

    call_count = [0]

    def _get_side_effect(*args, **kwargs):
        call_count[0] += 1
        return page1_response if call_count[0] == 1 else page2_response

    mock_session = MagicMock()
    mock_session.get.side_effect = _get_side_effect
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = mock_session
        mock_aiohttp.ClientTimeout = MagicMock()
        result = asyncio.run(fetch_kalshi_trades("K-MKT-1", limit=100))

    assert mock_session.get.call_count == 2
    assert len(result) == 2
    trade_ids = [t["trade_id"] for t in result]
    assert "p1-t1" in trade_ids
    assert "p2-t1" in trade_ids


# ---------------------------------------------------------------------------
# Outcome classification tests (Part B)
# ---------------------------------------------------------------------------

def _classify_outcome(outcome_label: str) -> tuple[str, bool]:
    """Mirror the classification logic from sync_polymarket_markets for unit testing."""
    import re
    label_lower = outcome_label.lower().strip()
    if label_lower in ("yes", "no"):
        outcome_key = label_lower
        is_binary = True
    else:
        outcome_key = re.sub(r"[^a-z0-9]+", "-", outcome_label.lower().strip()).strip("-")
        if not outcome_key:
            outcome_key = "unknown"
        is_binary = False
    return outcome_key, is_binary


def test_outcome_label_yes_is_binary():
    """Label 'Yes' -> outcome_key='yes', is_binary=True."""
    key, binary = _classify_outcome("Yes")
    assert key == "yes"
    assert binary is True


def test_outcome_label_no_is_binary():
    """Label 'No' -> outcome_key='no', is_binary=True."""
    key, binary = _classify_outcome("No")
    assert key == "no"
    assert binary is True


def test_outcome_label_yes_lowercase_is_binary():
    """Label 'yes' (already lower) -> outcome_key='yes', is_binary=True."""
    key, binary = _classify_outcome("yes")
    assert key == "yes"
    assert binary is True


def test_outcome_label_biden_not_coerced():
    """Label 'Biden' -> outcome_key='biden' (NOT 'no'), is_binary=False."""
    key, binary = _classify_outcome("Biden")
    assert key == "biden"
    assert key not in ("yes", "no"), "Must not coerce non-yes/no label to yes/no"
    assert binary is False


def test_outcome_label_biden_2024_slug():
    """Label 'Biden 2024' -> outcome_key='biden-2024', is_binary=False."""
    key, binary = _classify_outcome("Biden 2024")
    assert key == "biden-2024"
    assert binary is False


def test_outcome_label_preserves_non_binary_verbatim_as_slug():
    """Any multi-word label becomes a deterministic slug, never yes/no."""
    key, binary = _classify_outcome("Donald Trump")
    assert key == "donald-trump"
    assert binary is False


def test_outcome_label_yes_in_label_not_coerced():
    """Label containing 'yes' but not exactly 'yes' must not be classified as binary yes."""
    # Old bug: "yes" in outcome_label.lower() -> outcome_key = "yes"
    key, binary = _classify_outcome("Maybe Yes")
    assert key != "yes"
    assert binary is False


def test_outcome_label_no_in_label_not_coerced():
    """Label containing 'no' but not exactly 'no' must not be classified as binary no."""
    key, binary = _classify_outcome("Unknown")
    assert key != "no"
    assert binary is False


# ---------------------------------------------------------------------------
# Slug-collision disambiguation: real sync_polymarket_markets path (Part C)
# ---------------------------------------------------------------------------

def test_sync_polymarket_markets_slug_collision_disambiguates():
    """Two tokens whose labels both slug to 'trump' must get DISTINCT outcome_keys.

    Exercises the real disambiguation logic inside sync_polymarket_markets
    (not the local _classify_outcome mirror) to guard against silent overwrite.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch, call

    # Build a single market whose two tokens both slug to "trump"
    market_dict = {
        "condition_id": "cond-collision-test",
        "question": "Who wins?",
        "category": "politics",
        "end_date_iso": None,
        "volume": 50000.0,
        "tokens": [
            {"token_id": "aaa111", "outcome": "Trump!"},
            {"token_id": "bbb222", "outcome": "Trump?"},
        ],
    }

    # Mock pool / conn using the MagicMock pattern from test_runner_suppression.py
    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    # Capture every outcome_key passed to upsert_market_outcome
    upsert_outcome_mock = AsyncMock()

    with (
        patch("pmfi.markets.fetch_polymarket_markets", new=AsyncMock(return_value=[market_dict])),
        patch("pmfi.db.repos.markets.upsert_market_full", new=AsyncMock(return_value="market-id-001")),
        patch("pmfi.db.repos.markets.upsert_market_outcome", new=upsert_outcome_mock),
    ):
        asyncio.run(__import__("pmfi.markets", fromlist=["sync_polymarket_markets"]).sync_polymarket_markets(mock_pool))

    # upsert_market_outcome must have been called exactly twice (one per token)
    assert upsert_outcome_mock.call_count == 2, (
        f"Expected 2 upsert_market_outcome calls, got {upsert_outcome_mock.call_count}"
    )

    # Extract the outcome_key from each call's keyword arguments
    outcome_keys = [c.kwargs["outcome_key"] for c in upsert_outcome_mock.call_args_list]

    # Both tokens must be represented — no silent overwrite
    assert len(set(outcome_keys)) == 2, (
        f"outcome_keys must be distinct (no overwrite), got: {outcome_keys}"
    )

    # The first token gets the plain slug; the second gets a suffix to avoid collision
    assert "trump" in outcome_keys, f"Expected 'trump' in outcome_keys, got {outcome_keys}"
    assert any(k.startswith("trump-") for k in outcome_keys), (
        f"Expected one outcome_key starting with 'trump-', got {outcome_keys}"
    )

    # Both original token_ids must appear in the venue_outcome_id calls
    venue_ids = [c.kwargs["venue_outcome_id"] for c in upsert_outcome_mock.call_args_list]
    assert "aaa111" in venue_ids, f"Expected token aaa111 in venue_outcome_ids, got {venue_ids}"
    assert "bbb222" in venue_ids, f"Expected token bbb222 in venue_outcome_ids, got {venue_ids}"


# ---------------------------------------------------------------------------
# Volume-first discovery UX tests (Part D)
# ---------------------------------------------------------------------------

def test_sync_polymarket_markets_passes_volume():
    """sync_polymarket_markets passes volume kwarg to upsert_market_full."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    market_dict = {
        "condition_id": "cond-vol-test",
        "question": "Volume test?",
        "category": "test",
        "end_date_iso": None,
        "volume": 12345.0,
        "tokens": [{"token_id": "tok1", "outcome": "Yes"}, {"token_id": "tok2", "outcome": "No"}],
    }

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    upsert_full_mock = AsyncMock(return_value="market-id-vol")

    with (
        patch("pmfi.markets.fetch_polymarket_markets", new=AsyncMock(return_value=[market_dict])),
        patch("pmfi.db.repos.markets.upsert_market_full", new=upsert_full_mock),
        patch("pmfi.db.repos.markets.upsert_market_outcome", new=AsyncMock()),
    ):
        asyncio.run(__import__("pmfi.markets", fromlist=["sync_polymarket_markets"]).sync_polymarket_markets(mock_pool))

    assert upsert_full_mock.call_count == 1
    call_kwargs = upsert_full_mock.call_args.kwargs
    assert call_kwargs.get("volume") == 12345.0, f"Expected volume=12345.0, got {call_kwargs.get('volume')}"


def test_sync_kalshi_markets_passes_volume():
    """sync_kalshi_markets passes volume kwarg to upsert_market_full."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    market_dict = {
        "ticker": "KXTEST-001",
        "title": "Kalshi vol test",
        "volume": 8200.0,
        "status": "open",
        "event_ticker": "KXTEST",
    }

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    upsert_full_mock = AsyncMock(return_value="market-id-kal-vol")

    with (
        patch("pmfi.markets.fetch_kalshi_markets", new=AsyncMock(return_value=[market_dict])),
        patch("pmfi.db.repos.markets.upsert_market_full", new=upsert_full_mock),
        patch("pmfi.db.repos.markets.upsert_market_outcome", new=AsyncMock()),
    ):
        asyncio.run(__import__("pmfi.markets", fromlist=["sync_kalshi_markets"]).sync_kalshi_markets(mock_pool))

    assert upsert_full_mock.call_count == 1
    call_kwargs = upsert_full_mock.call_args.kwargs
    assert call_kwargs.get("volume") == 8200.0, f"Expected volume=8200.0, got {call_kwargs.get('volume')}"


def test_sync_passes_zero_volume_not_none():
    """A market with volume 0 must pass volume=0.0 (NOT None) so the ranking cache
    reflects the real zero rather than COALESCE-preserving a stale prior value."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    market_dict = {
        "condition_id": "cond-zero-vol",
        "question": "Zero volume?",
        "category": "test",
        "end_date_iso": None,
        "volume": 0.0,
        "tokens": [{"token_id": "tokz", "outcome": "Yes"}],
    }

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    upsert_full_mock = AsyncMock(return_value="market-id-zero")

    with (
        patch("pmfi.markets.fetch_polymarket_markets", new=AsyncMock(return_value=[market_dict])),
        patch("pmfi.db.repos.markets.upsert_market_full", new=upsert_full_mock),
        patch("pmfi.db.repos.markets.upsert_market_outcome", new=AsyncMock()),
    ):
        asyncio.run(__import__("pmfi.markets", fromlist=["sync_polymarket_markets"]).sync_polymarket_markets(mock_pool))

    call_kwargs = upsert_full_mock.call_args.kwargs
    assert call_kwargs.get("volume") == 0.0, f"Expected volume=0.0, got {call_kwargs.get('volume')!r}"
    assert call_kwargs.get("volume") is not None, "explicit zero volume must not be coerced to None"


def test_upsert_market_full_accepts_volume():
    """upsert_market_full passes volume param correctly, SQL contains COALESCE for volume."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"market_id": "uuid-vol-001"})

    async def _run():
        from pmfi.db.repos.markets import upsert_market_full
        return await upsert_market_full(
            mock_conn,
            venue_code="polymarket",
            venue_market_id="cond-vol-001",
            title="Test",
            volume=12345.0,
        )

    result = asyncio.run(_run())
    assert result == "uuid-vol-001"

    # Check the SQL contains COALESCE for volume (injection guard on the query text)
    call_args = mock_conn.fetchrow.call_args
    sql = call_args[0][0]
    assert "COALESCE(EXCLUDED.volume, markets.volume)" in sql, (
        f"SQL must contain COALESCE for volume, got: {sql}"
    )
    # Check 12345.0 is in the params
    params = list(call_args[0][1:])
    assert 12345.0 in params, f"Expected 12345.0 in params, got {params}"


def test_upsert_market_full_volume_none():
    """upsert_market_full passes None for volume when not provided."""
    import asyncio
    from unittest.mock import AsyncMock

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"market_id": "uuid-vol-002"})

    async def _run():
        from pmfi.db.repos.markets import upsert_market_full
        return await upsert_market_full(
            mock_conn,
            venue_code="polymarket",
            venue_market_id="cond-vol-002",
            title="Test no volume",
        )

    asyncio.run(_run())
    call_args = mock_conn.fetchrow.call_args
    params = list(call_args[0][1:])
    assert None in params, f"Expected None in params for missing volume, got {params}"


def test_fmt_volume_magnitudes():
    """_fmt_volume formats various magnitudes correctly without currency symbol."""
    from decimal import Decimal
    from pmfi.commands.markets import _fmt_volume
    assert _fmt_volume(2_500_000) == "2.50M"
    assert _fmt_volume(8200) == "8.20K"
    assert _fmt_volume(495) == "495.00"
    assert _fmt_volume(None) == "—"  # em-dash
    # The numeric(20,2) column round-trips as Decimal via asyncpg — must not
    # raise on Decimal/float arithmetic (regression: live discover crashed here).
    assert _fmt_volume(Decimal("2500000.00")) == "2.50M"
    assert _fmt_volume(Decimal("8200.00")) == "8.20K"
    assert _fmt_volume(Decimal("0")) == "0.00"


def test_fetch_markets_ranked_sort_whitelist():
    """fetch_markets_ranked uses whitelisted sort clause; invalid sort falls back to volume."""
    from pmfi.db.repos.markets import _SORT_CLAUSES, fetch_markets_ranked
    import asyncio
    from unittest.mock import AsyncMock

    # Verify the whitelist maps correctly
    assert _SORT_CLAUSES["volume"] == "volume DESC NULLS LAST"
    assert _SORT_CLAUSES["trades"] == "trade_count DESC"
    assert _SORT_CLAUSES["last-trade"] == "last_trade_at DESC NULLS LAST"

    # Unknown sort key falls back to volume clause
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    async def _run():
        return await fetch_markets_ranked(mock_conn, sort="INVALID; DROP TABLE markets;--", limit=5)

    asyncio.run(_run())
    call_args = mock_conn.fetch.call_args
    sql = call_args[0][0]
    # Raw user input must NOT appear in the SQL
    assert "INVALID" not in sql, "Raw sort input must never be interpolated into SQL"
    assert "DROP TABLE" not in sql, "SQL injection must not be possible via sort param"
    # Must fall back to the volume clause
    assert "volume DESC NULLS LAST" in sql, f"Expected volume fallback in SQL, got: {sql}"


def test_fetch_markets_ranked_search_matches_title_or_market_id():
    """markets list --search must find copied venue ids as well as titles."""
    from pmfi.db.repos.markets import fetch_markets_ranked
    import asyncio
    from unittest.mock import AsyncMock

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])

    async def _run():
        return await fetch_markets_ranked(mock_conn, search="KXBTCD-26JUN1817", limit=5)

    asyncio.run(_run())
    call_args = mock_conn.fetch.call_args
    sql = call_args[0][0]
    params = call_args[0][1:]
    assert "m.title ILIKE $1 OR m.venue_market_id ILIKE $1" in sql
    assert params[0] == "%KXBTCD-26JUN1817%"


# ---------------------------------------------------------------------------
# CLI parse tests for new flags
# ---------------------------------------------------------------------------

def test_cli_markets_list_sort_and_min_volume():
    """markets list --sort volume --min-volume 5000 parses correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "list", "--sort", "volume", "--min-volume", "5000"])
    assert args.sort == "volume"
    assert args.min_volume == 5000.0


def test_cli_markets_list_format_json():
    """markets list parses format/venue flags without changing defaults."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    default_args = parser.parse_args(["markets", "list"])
    json_args = parser.parse_args(["markets", "list", "--venue", "kalshi", "--format", "json"])
    assert default_args.format == "table"
    assert default_args.venue is None
    assert json_args.format == "json"
    assert json_args.venue == "kalshi"


def test_markets_list_json_outputs_exact_ids_read_only(capsys):
    """JSON list mode renders exact DB market identifiers without files or live API."""
    import argparse
    import json
    from datetime import datetime, timezone
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_rows = [
        {
            "venue_code": "kalshi",
            "venue_market_id": "KXBTCD-23DEC3100-LONG-EXACT-TICKER",
            "title": "Bitcoin above threshold",
            "status": "open",
            "watched": True,
            "volume": Decimal("12345.67"),
            "trade_count": 9,
            "last_trade_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        }
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    ranked_mock = AsyncMock(return_value=fake_rows)
    args = argparse.Namespace(
        format="json",
        venue="kalshi",
        limit=10,
        watched=True,
        search="bitcoin",
        sort="last-trade",
        min_volume=1000.0,
    )
    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    no_write = MagicMock(side_effect=AssertionError("markets list json must not write files"))

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.db.repos.markets.fetch_markets_ranked", new=ranked_mock),
        patch("pathlib.Path.write_text", no_write),
        patch("pathlib.Path.write_bytes", no_write),
        patch("pmfi.markets.fetch_kalshi_markets", new=AsyncMock()) as live_fetch,
    ):
        from pmfi.commands.markets import _cmd_markets_list
        rc = _cmd_markets_list(args)

    assert rc == 0
    live_fetch.assert_not_called()
    ranked_mock.assert_awaited_once()
    assert ranked_mock.call_args.kwargs == {
        "venue_code": "kalshi",
        "watched": True,
        "search": "bitcoin",
        "min_volume": 1000.0,
        "sort": "last-trade",
        "limit": 10,
    }

    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "venue_code": "kalshi",
            "venue_market_id": "KXBTCD-23DEC3100-LONG-EXACT-TICKER",
            "title": "Bitcoin above threshold",
            "status": "open",
            "watched": True,
            "volume": "12345.67",
            "trade_count": 9,
            "last_trade_at": "2026-01-02T03:04:05+00:00",
        }
    ]


def test_markets_list_json_empty_outputs_array(capsys):
    """JSON list mode remains parseable when no markets match."""
    import argparse
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    cfg = SimpleNamespace(database=SimpleNamespace(url="postgresql://unit-test"))
    args = argparse.Namespace(
        format="json",
        venue=None,
        limit=20,
        watched=False,
        search=None,
        sort="volume",
        min_volume=None,
    )

    with (
        patch("pmfi.config.load_config", return_value=cfg),
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.db.repos.markets.fetch_markets_ranked", new=AsyncMock(return_value=[])),
    ):
        from pmfi.commands.markets import _cmd_markets_list
        rc = _cmd_markets_list(args)

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_markets_discover_watch_top():
    """markets discover --watch-top 5 parses correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "discover", "--watch-top", "5"])
    assert args.watch_top == 5


def test_cli_markets_watch_top():
    """markets watch --top 10 --venue polymarket parses correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "watch", "--top", "10", "--venue", "polymarket"])
    assert args.top == 10
    assert args.venue == "polymarket"
    assert args.market_id is None


def test_cli_markets_watch_search():
    """markets watch --search bitcoin parses correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "watch", "--search", "bitcoin"])
    assert args.search == "bitcoin"
    assert args.market_id is None


def test_cli_markets_unwatch_search():
    """markets unwatch --search expired parses correctly."""
    from pmfi.cli import _build_parser
    parser = _build_parser()
    args = parser.parse_args(["markets", "unwatch", "--search", "expired"])
    assert args.search == "expired"
    assert args.market_id is None


def test_cli_markets_watch_no_mode_triggers_validation_error(capsys):
    """markets watch with no positional and no mode returns exit code 1."""
    from pmfi.cli import main
    rc = main(["markets", "watch"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "exactly one" in out.lower() or "provide" in out.lower() or "error" in out.lower()


def test_cli_markets_watch_top_zero_fails_before_db(capsys):
    """markets watch --top must reject non-positive counts before DB access."""
    from unittest.mock import AsyncMock, patch

    from pmfi.cli import main

    with patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool:
        rc = main(["markets", "watch", "--top", "0"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "--top must be a positive integer" in out
    create_pool.assert_not_called()


def test_cli_markets_watch_top_negative_fails_before_db(capsys):
    """markets watch --top must reject negative counts before DB access."""
    from unittest.mock import AsyncMock, patch

    from pmfi.cli import main

    with patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool:
        rc = main(["markets", "watch", "--top", "-2"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "--top must be a positive integer" in out
    create_pool.assert_not_called()


# ---------------------------------------------------------------------------
# Stateless bulk-watch test (no file written)
# ---------------------------------------------------------------------------

def test_watch_top_resolves_and_bulk_sets():
    """watch --top 3 calls set_markets_watched_bulk with exact IDs and writes no file.

    The statelessness check is enforced by guarding every file-write path
    (open in a write mode, Path.write_text/write_bytes) during the call — if
    the watch path ever persisted a session/index file, the test fails loudly.
    Config reads (mode 'r') are allowed through.
    """
    import asyncio
    import argparse
    import builtins
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_rows = [
        {"venue_code": "polymarket", "venue_market_id": "id-1", "title": "M1", "status": "active",
         "watched": False, "volume": 500000.0, "trade_count": 10, "last_trade_at": None},
        {"venue_code": "polymarket", "venue_market_id": "id-2", "title": "M2", "status": "active",
         "watched": False, "volume": 300000.0, "trade_count": 5, "last_trade_at": None},
        {"venue_code": "polymarket", "venue_market_id": "id-3", "title": "M3", "status": "active",
         "watched": False, "volume": 100000.0, "trade_count": 2, "last_trade_at": None},
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    bulk_mock = AsyncMock(return_value=3)
    ranked_mock = AsyncMock(return_value=fake_rows)

    args = argparse.Namespace(
        market_id=None, top=3, search=None, venue="polymarket"
    )

    _real_open = builtins.open

    def _guard_open(file, mode="r", *a, **k):
        if any(c in str(mode) for c in ("w", "a", "x", "+")):
            raise AssertionError(f"stateless watch must not open for write: {file!r} mode={mode!r}")
        return _real_open(file, mode, *a, **k)

    _no_write = MagicMock(side_effect=AssertionError("stateless watch must not write a file"))

    with (
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.db.repos.markets.fetch_markets_ranked", new=ranked_mock),
        patch("pmfi.db.repos.markets.set_markets_watched_bulk", new=bulk_mock),
        patch("builtins.open", _guard_open),
        patch("pathlib.Path.write_text", _no_write),
        patch("pathlib.Path.write_bytes", _no_write),
    ):
        from pmfi.commands.markets import _cmd_markets_set_watched
        rc = _cmd_markets_set_watched(args, watched=True)

    assert rc == 0
    assert bulk_mock.call_count == 1
    call_kwargs = bulk_mock.call_args.kwargs
    assert call_kwargs["venue_market_ids"] == ["id-1", "id-2", "id-3"]
    assert call_kwargs["watched"] is True


# ---------------------------------------------------------------------------
# Discover prints ranked preview
# ---------------------------------------------------------------------------

def test_discover_prints_ranked_preview(capsys):
    """discover shows Volume column + inline watch lines; count==0 doesn't crash."""
    import asyncio
    import argparse
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_ranked = [
        {"venue_code": "polymarket", "venue_market_id": "cond-aaa", "title": "Bitcoin 100k?",
         "status": "active", "watched": False, "volume": 2_500_000.0, "trade_count": 50, "last_trade_at": None},
        {"venue_code": "polymarket", "venue_market_id": "cond-bbb", "title": "ETH flip?",
         "status": "active", "watched": False, "volume": 8200.0, "trade_count": 10, "last_trade_at": None},
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    ranked_mock = AsyncMock(return_value=fake_ranked)

    args = argparse.Namespace(
        venue="polymarket", limit=100, min_volume=None, watch_top=None
    )

    with (
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.markets.sync_polymarket_markets", new=AsyncMock(return_value=2)),
        patch("pmfi.db.repos.markets.fetch_markets_ranked", new=ranked_mock),
    ):
        from pmfi.commands.markets import _cmd_markets_discover
        rc = _cmd_markets_discover(args)

    out = capsys.readouterr().out
    assert rc == 0
    # Volume values must appear (formatted)
    assert "2.50M" in out or "Volume" in out
    # Inline watch copy-paste lines must appear
    assert "pmfi markets watch cond-aaa" in out
    assert "pmfi markets watch cond-bbb" in out


def test_discover_count_zero_does_not_crash(capsys):
    """discover with count==0 prints the summary line and doesn't crash."""
    import argparse
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    args = argparse.Namespace(
        venue="polymarket", limit=100, min_volume=None, watch_top=None
    )

    with (
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.markets.sync_polymarket_markets", new=AsyncMock(return_value=0)),
    ):
        from pmfi.commands.markets import _cmd_markets_discover
        rc = _cmd_markets_discover(args)

    out = capsys.readouterr().out
    assert rc == 0
    assert "Synced 0" in out


def test_discover_watch_top_zero_fails_before_db_or_sync(capsys):
    """discover --watch-top must reject non-positive counts before DB or REST work."""
    import argparse
    from unittest.mock import AsyncMock, patch

    args = argparse.Namespace(
        venue="polymarket", limit=100, min_volume=None, watch_top=0
    )

    with (
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.markets.sync_polymarket_markets", new=AsyncMock()) as sync_markets,
    ):
        from pmfi.commands.markets import _cmd_markets_discover
        rc = _cmd_markets_discover(args)

    out = capsys.readouterr().out
    assert rc == 1
    assert "--watch-top must be a positive integer" in out
    create_pool.assert_not_called()
    sync_markets.assert_not_called()


def test_discover_watch_top_negative_fails_before_db_or_sync(capsys):
    """discover --watch-top must reject negative counts before DB or REST work."""
    import argparse
    from unittest.mock import AsyncMock, patch

    args = argparse.Namespace(
        venue="polymarket", limit=100, min_volume=None, watch_top=-2
    )

    with (
        patch("pmfi.db.create_pool", new=AsyncMock()) as create_pool,
        patch("pmfi.markets.sync_polymarket_markets", new=AsyncMock()) as sync_markets,
    ):
        from pmfi.commands.markets import _cmd_markets_discover
        rc = _cmd_markets_discover(args)

    out = capsys.readouterr().out
    assert rc == 1
    assert "--watch-top must be a positive integer" in out
    create_pool.assert_not_called()
    sync_markets.assert_not_called()


def test_discover_watch_top_honors_n_beyond_preview(capsys):
    """discover --watch-top 15 fetches >=15 rows and watches all 15, while the
    printed preview stays capped at the top 10 (guards the silent-truncation fix)."""
    import argparse
    from unittest.mock import AsyncMock, MagicMock, patch

    fake_ranked = [
        {"venue_code": "polymarket", "venue_market_id": f"cond-{i:02d}", "title": f"M{i}",
         "status": "active", "watched": False, "volume": float((20 - i) * 1000),
         "trade_count": 0, "last_trade_at": None}
        for i in range(15)
    ]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    ranked_mock = AsyncMock(return_value=fake_ranked)
    bulk_mock = AsyncMock(return_value=15)

    args = argparse.Namespace(
        venue="polymarket", limit=100, min_volume=None, watch_top=15
    )

    with (
        patch("pmfi.db.create_pool", new=AsyncMock(return_value=mock_pool)),
        patch("pmfi.db.close_pool", new=AsyncMock()),
        patch("pmfi.markets.sync_polymarket_markets", new=AsyncMock(return_value=15)),
        patch("pmfi.db.repos.markets.fetch_markets_ranked", new=ranked_mock),
        patch("pmfi.db.repos.markets.set_markets_watched_bulk", new=bulk_mock),
    ):
        from pmfi.commands.markets import _cmd_markets_discover
        rc = _cmd_markets_discover(args)

    assert rc == 0
    # The ranked fetch must request enough rows to honor watch_top (not the 10 cap).
    assert ranked_mock.call_args.kwargs["limit"] == 15, (
        f"expected fetch limit 15 to honor --watch-top, got {ranked_mock.call_args.kwargs.get('limit')}"
    )
    # All 15 must be watched, not silently truncated to the 10-row preview.
    assert len(bulk_mock.call_args.kwargs["venue_market_ids"]) == 15
    # The inline copy-paste preview stays capped at 10 lines.
    out = capsys.readouterr().out
    assert out.count("pmfi markets watch ") == 10, (
        f"preview should show exactly 10 inline watch lines, got {out.count('pmfi markets watch ')}"
    )
