"""Offline unit tests for resolve_alert_id (prefix vs full UUID handling)."""
from unittest.mock import AsyncMock, MagicMock

_FULL = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


async def test_full_uuid_skips_db():
    """A 36-char UUID with 4 hyphens is returned directly without any DB call."""
    from pmfi.db.repos.alerts import resolve_alert_id
    conn = MagicMock()
    result = await resolve_alert_id(conn, _FULL)
    assert result == _FULL
    conn.fetch.assert_not_called()


async def test_prefix_issues_like_query():
    """A short prefix triggers a LIKE query and returns the matched UUID."""
    from pmfi.db.repos.alerts import resolve_alert_id
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"alert_id": _FULL}])
    result = await resolve_alert_id(conn, "aaaaaaaa")
    assert result == _FULL
    sql = conn.fetch.call_args[0][0]
    assert "LIKE" in sql
    assert "LIMIT 2" in sql


async def test_prefix_no_match_returns_none():
    """A prefix matching nothing returns None."""
    from pmfi.db.repos.alerts import resolve_alert_id
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    result = await resolve_alert_id(conn, "deadbeef")
    assert result is None


async def test_prefix_multiple_matches_returns_none():
    """A non-unique prefix fails closed instead of choosing the newest match."""
    from pmfi.db.repos.alerts import resolve_alert_id
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"alert_id": _FULL},
        {"alert_id": "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"},
    ])
    result = await resolve_alert_id(conn, "aaaaaaaa")
    assert result is None


async def test_get_alert_by_id_resolves_prefix():
    """get_alert_by_id with a short prefix calls resolve_alert_id then fetches by full UUID."""
    from pmfi.db.repos.alerts import get_alert_by_id
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"alert_id": _FULL}])
    conn.fetchrow = AsyncMock(side_effect=[
        {                      # main fetchrow call
            "alert_id": _FULL, "rule_key": "r", "rule_version": "1",
            "severity": "high", "confidence": "high", "score": 0.9,
            "title": "t", "summary": "s", "evidence": {}, "data_quality": "ok",
            "outcome_key": "yes", "fired_at": None, "created_at": None,
            "raw_event_id": None, "trade_id": None,
            "market_title": "Test Market", "venue_market_id": "tm-1",
            "market_venue_code": "polymarket",
        },
    ])
    result = await get_alert_by_id(conn, "aaaaaaaa")
    assert result is not None
    assert result["alert_id"] == _FULL


async def test_get_alert_by_id_prefix_not_found():
    """get_alert_by_id with a prefix that resolves to nothing returns None."""
    from pmfi.db.repos.alerts import get_alert_by_id
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    result = await get_alert_by_id(conn, "notfound")
    assert result is None
    conn.fetchrow.assert_not_called()
