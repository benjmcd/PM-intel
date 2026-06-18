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
    assert ("POST", "/api/alerts/{alert_id}/review") in routes


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


def test_insert_alert_review_resolves_prefix_and_returns_inserted_metadata():
    from pmfi.db.repos.alerts import insert_alert_review

    inserted_at = datetime(2026, 6, 18, 12, 30, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.fetchrow_calls = []

        async def fetchrow(self, sql, *params):
            self.fetchrow_calls.append((sql, params))
            if "LIKE $1 || '%'" in sql:
                return {"alert_id": FULL_ALERT_ID}
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


def test_insert_alert_review_returns_none_when_prefix_not_found():
    from pmfi.db.repos.alerts import insert_alert_review

    class Conn:
        async def fetchrow(self, sql, *params):
            assert "LIKE $1 || '%'" in sql
            return None

    result = asyncio.run(
        insert_alert_review(Conn(), "deadbeef", label="fp")
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
