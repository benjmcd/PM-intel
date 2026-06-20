from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer


FULL_ALERT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_parse_alert_review_body_accepts_only_review_contract_fields():
    from pmfi.dashboard.server import _parse_alert_review_body

    parsed = _parse_alert_review_body({
        "label": "tp",
        "category": "confirmed_flow",
        "notes": "lineage checked",
        "reviewed_by": "local-operator",
    })

    assert parsed == {
        "label": "tp",
        "category": "confirmed_flow",
        "notes": "lineage checked",
        "reviewed_by": "local-operator",
    }


@pytest.mark.parametrize(
    "body, detail",
    [
        (None, "object"),
        ([], "object"),
        ({}, "label"),
        ({"label": "bad"}, "label"),
        ({"label": 1}, "label"),
        ({"label": "tp", "notes": 3}, "notes"),
        ({"label": "tp", "category": None}, "category"),
        ({"label": "tp", "reviewed_by": ["op"]}, "reviewed_by"),
        ({"label": "tp", "reviewed_by": "co" + "dex-tier1"}, "human/local operator"),
    ],
)
def test_parse_alert_review_body_rejects_malformed_or_unsafe_fields(body, detail):
    from pmfi.dashboard.server import _parse_alert_review_body

    with pytest.raises(ValueError) as exc:
        _parse_alert_review_body(body)

    assert detail in str(exc.value)


def test_dashboard_app_registers_local_review_post_route():
    from pmfi.dashboard.server import _create_dashboard_app

    app = _create_dashboard_app(pool=object())
    routes = {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
        if route.resource is not None
    }

    assert ("GET", "/api/alerts") in routes
    assert ("GET", "/api/alerts/{alert_id}/reviews") in routes
    assert ("GET", "/api/raw-events/{raw_event_id}") in routes
    assert ("POST", "/api/alerts/{alert_id}/review") in routes


async def test_dashboard_raw_event_lookup_route_is_read_only_and_validated(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_fetch(conn, raw_event_ids, *, include_payload):
        calls.append({
            "conn": conn,
            "raw_event_ids": raw_event_ids,
            "include_payload": include_payload,
        })
        if raw_event_ids == [123]:
            return [
                {
                    "raw_event_id": 123,
                    "venue_code": "kalshi",
                    "source_channel": "trade_poll",
                    "source_event_type": "trade",
                    "source_event_id": "venue-123",
                    "venue_market_id": "KXTEST",
                    "payload_preview": '{"trade": 123}',
                    "payload": {"trade": 123},
                    "trade_id": "trade-uuid",
                    "venue_trade_id": "venue-trade-123",
                    "outcome_key": "yes",
                    "directional_side": "yes",
                    "price": 0.52,
                    "contracts": 10,
                    "capital_at_risk_usd": 520,
                    "payout_notional_usd": 1000,
                    "warnings": [],
                }
            ]
        return []

    monkeypatch.setattr("pmfi.raw_event_lookup.fetch_raw_event_lookup_rows", fake_fetch)
    server = TestServer(_create_dashboard_app(_Pool(conn="fake-conn")))
    client = TestClient(server)
    await client.start_server()
    try:
        invalid = await client.get("/api/raw-events/not-an-int")
        invalid_body = await invalid.json()
        assert invalid.status == 400
        assert "integer" in invalid_body["detail"]
        assert calls == []

        zero = await client.get("/api/raw-events/0")
        zero_body = await zero.json()
        assert zero.status == 400
        assert "positive" in zero_body["detail"]
        assert calls == []

        missing = await client.get("/api/raw-events/999")
        missing_body = await missing.json()
        assert missing.status == 404
        assert missing_body["schema_version"] == "raw_event_lookup.v1"
        assert missing_body["missing_raw_event_ids"] == [999]

        ok = await client.get("/api/raw-events/123?include_payload=1")
        body = await ok.json()
        assert ok.status == 200
        assert body["local_only"] is True
        assert body["read_only"] is True
        assert body["db_mutation"] is False
        assert body["live_calls"] is False
        assert body["include_payload"] is True
        assert body["rows"][0]["raw_event_id"] == 123
        assert body["rows"][0]["trade"]["trade_id"] == "trade-uuid"
        assert body["rows"][0]["payload"] == {"trade": 123}
        assert calls == [
            {
                "conn": "fake-conn",
                "raw_event_ids": [999],
                "include_payload": False,
            },
            {
                "conn": "fake-conn",
                "raw_event_ids": [123],
                "include_payload": True,
            },
        ]
    finally:
        await client.close()


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn=None):
        self.conn = conn or object()

    def acquire(self):
        return _Acquire(self.conn)


async def test_dashboard_review_post_rejects_non_json_and_foreign_origin(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    async def fake_insert(*args, **kwargs):
        raise AssertionError("rejected requests must not write")

    monkeypatch.setattr("pmfi.db.repos.alerts.insert_alert_review", fake_insert)
    server = TestServer(_create_dashboard_app(_Pool()))
    client = TestClient(server)
    await client.start_server()
    try:
        non_json = await client.post(
            f"/api/alerts/{FULL_ALERT_ID}/review",
            data='{"label":"tp"}',
            headers={"Content-Type": "text/plain"},
        )
        assert non_json.status == 400
        assert "application/json" in (await non_json.json())["detail"]

        foreign = await client.post(
            f"/api/alerts/{FULL_ALERT_ID}/review",
            json={"label": "tp"},
            headers={"Origin": "http://evil.example"},
        )
        assert foreign.status == 403
        assert "same-origin" in (await foreign.json())["detail"]
    finally:
        await client.close()


async def test_dashboard_review_post_maps_validation_not_found_and_success(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_insert(conn, alert_id, *, label, category, notes, reviewed_by):
        calls.append({
            "conn": conn,
            "alert_id": alert_id,
            "label": label,
            "category": category,
            "notes": notes,
            "reviewed_by": reviewed_by,
        })
        if alert_id == "missing":
            return None
        return {
            "review_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "alert_id": FULL_ALERT_ID,
            "label": label,
            "category": category,
            "notes": notes,
            "reviewed_by": reviewed_by,
            "reviewed_at": "2026-06-18T12:00:00+00:00",
        }

    monkeypatch.setattr("pmfi.db.repos.alerts.insert_alert_review", fake_insert)
    server = TestServer(_create_dashboard_app(_Pool(conn="fake-conn")))
    client = TestClient(server)
    await client.start_server()
    try:
        invalid = await client.post(f"/api/alerts/{FULL_ALERT_ID}/review", json={"label": "bad"})
        assert invalid.status == 400
        assert calls == []

        missing = await client.post("/api/alerts/missing/review", json={"label": "fp"})
        assert missing.status == 404
        assert calls[-1]["alert_id"] == "missing"

        ok = await client.post(
            f"/api/alerts/{FULL_ALERT_ID}/review",
            json={
                "label": "noise",
                "category": "quoted_category",
                "notes": "\"onmouseover=alert(1)\"",
                "reviewed_by": "local-dashboard",
            },
        )
        body = await ok.json()
        assert ok.status == 200
        assert body["ok"] is True
        assert body["alert_id"] == FULL_ALERT_ID
        assert body["review"]["label"] == "noise"
        assert calls[-1] == {
            "conn": "fake-conn",
            "alert_id": FULL_ALERT_ID,
            "label": "noise",
            "category": "quoted_category",
            "notes": "\"onmouseover=alert(1)\"",
            "reviewed_by": "local-dashboard",
        }
    finally:
        await client.close()


async def test_dashboard_review_history_get_maps_validation_not_found_and_success(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_history(conn, alert_id, *, limit):
        calls.append({"conn": conn, "alert_id": alert_id, "limit": limit})
        if alert_id == "bad_lookup":
            raise ValueError("alert_id contains unsupported characters")
        if alert_id == "missing":
            return None
        return {
            "alert_id": FULL_ALERT_ID,
            "reviews": [
                {
                    "review_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                    "alert_id": FULL_ALERT_ID,
                    "label": "noise",
                    "category": "quoted_category",
                    "notes": "\"onmouseover=alert(1)\"",
                    "reviewed_by": "local-dashboard",
                    "reviewed_at": "2026-06-18T12:00:00+00:00",
                }
            ],
        }

    monkeypatch.setattr("pmfi.dashboard.queries.alert_review_history", fake_history)
    server = TestServer(_create_dashboard_app(_Pool(conn="fake-conn")))
    client = TestClient(server)
    await client.start_server()
    try:
        invalid_limit = await client.get(f"/api/alerts/{FULL_ALERT_ID}/reviews?limit=0")
        invalid_limit_body = await invalid_limit.json()
        assert invalid_limit.status == 400
        assert "limit" in invalid_limit_body["detail"]
        assert calls == []

        invalid_lookup = await client.get("/api/alerts/bad_lookup/reviews?limit=5")
        invalid_lookup_body = await invalid_lookup.json()
        assert invalid_lookup.status == 400
        assert "alert_id" in invalid_lookup_body["detail"]

        missing = await client.get("/api/alerts/missing/reviews?limit=5")
        missing_body = await missing.json()
        assert missing.status == 404
        assert missing_body["error"] == "not found"

        ok = await client.get(f"/api/alerts/{FULL_ALERT_ID[:8]}/reviews?limit=5")
        body = await ok.json()
        assert ok.status == 200
        assert body["alert_id"] == FULL_ALERT_ID
        assert body["limit"] == 5
        assert body["reviews"][0]["label"] == "noise"
        assert body["reviews"][0]["notes"] == "\"onmouseover=alert(1)\""
        assert "generated_at" in body
        assert calls == [
            {"conn": "fake-conn", "alert_id": "bad_lookup", "limit": 5},
            {"conn": "fake-conn", "alert_id": "missing", "limit": 5},
            {"conn": "fake-conn", "alert_id": FULL_ALERT_ID[:8], "limit": 5},
        ]
    finally:
        await client.close()


def test_insert_alert_review_resolves_prefix_and_returns_inserted_metadata():
    from pmfi.db.repos.alerts import insert_alert_review

    inserted_at = datetime(2026, 6, 18, 12, 30, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.fetch_calls = []
            self.fetchrow_calls = []

        async def fetch(self, sql, *params):
            self.fetch_calls.append((sql, params))
            if "LIKE $1 || '%'" in sql:
                return [{"alert_id": FULL_ALERT_ID}]
            raise AssertionError(f"unexpected SQL: {sql}")

        async def fetchrow(self, sql, *params):
            self.fetchrow_calls.append((sql, params))
            if "INSERT INTO alert_reviews" in sql:
                return {
                    "review_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                    "alert_id": FULL_ALERT_ID,
                    "label": params[1],
                    "false_positive_category": params[2],
                    "notes": params[3],
                    "reviewed_by": params[4],
                    "reviewed_at": inserted_at,
                }
            raise AssertionError(f"unexpected SQL: {sql}")

    conn = Conn()
    result = asyncio.run(
        insert_alert_review(
            conn,
            "aaaaaaaa",
            label="noise",
            category="low_notional",
            notes="too small",
            reviewed_by="local-dashboard",
        )
    )

    assert result == {
        "review_id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "alert_id": FULL_ALERT_ID,
        "label": "noise",
        "category": "low_notional",
        "notes": "too small",
        "reviewed_by": "local-dashboard",
        "reviewed_at": inserted_at.isoformat(),
    }
    sql_text = "\n".join(sql for sql, _params in conn.fetchrow_calls)
    assert "INSERT INTO alert_reviews" in sql_text
    assert "UPDATE " not in sql_text.upper()
    assert "DELETE " not in sql_text.upper()
    resolve_sql = "\n".join(sql for sql, _params in conn.fetch_calls)
    assert "LIMIT 2" in resolve_sql


def test_insert_alert_review_returns_none_when_prefix_not_found():
    from pmfi.db.repos.alerts import insert_alert_review

    class Conn:
        async def fetch(self, sql, *params):
            assert "LIKE $1 || '%'" in sql
            return []

    result = asyncio.run(
        insert_alert_review(Conn(), "deadbeef", label="fp")
    )

    assert result is None


def test_insert_alert_review_returns_none_when_prefix_is_ambiguous():
    from pmfi.db.repos.alerts import insert_alert_review

    class Conn:
        async def fetch(self, sql, *params):
            assert "LIKE $1 || '%'" in sql
            return [
                {"alert_id": FULL_ALERT_ID},
                {"alert_id": "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"},
            ]

        async def fetchrow(self, sql, *params):
            raise AssertionError("ambiguous prefix should fail before insert")

    result = asyncio.run(
        insert_alert_review(Conn(), "aaaaaaaa", label="fp")
    )

    assert result is None


def test_insert_alert_review_rejects_unknown_labels_before_db_access():
    from pmfi.db.repos.alerts import insert_alert_review

    class Conn:
        async def fetchrow(self, sql, *params):
            raise AssertionError("invalid labels should fail before DB access")

    with pytest.raises(ValueError) as exc:
        asyncio.run(
            insert_alert_review(
                Conn(),
                FULL_ALERT_ID,
                label="maybe",
            )
        )

    assert "label" in str(exc.value)


def test_alert_review_history_rejects_malformed_full_uuid_before_db_access():
    from pmfi.dashboard.queries import alert_review_history

    class Conn:
        async def fetchval(self, sql, *params):
            raise AssertionError("malformed alert ids should fail before DB access")

        async def fetchrow(self, sql, *params):
            raise AssertionError("malformed alert ids should fail before DB access")

        async def fetch(self, sql, *params):
            raise AssertionError("malformed alert ids should fail before DB access")

    with pytest.raises(ValueError) as exc:
        asyncio.run(
            alert_review_history(
                Conn(),
                "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
            )
        )

    assert "UUID" in str(exc.value)
