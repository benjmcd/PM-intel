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
