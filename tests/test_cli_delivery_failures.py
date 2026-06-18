import builtins
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from pmfi.cli import _build_parser, main


def _delivery_config():
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(database=database)


class _FakeDeliveryPool:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.sql: list[str] = []
        self.args: list[tuple] = []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        return self.rows

    async def close(self):
        self.closed = True


def test_delivery_failures_parser_accepts_json_format_and_limit():
    parser = _build_parser()
    args = parser.parse_args(["delivery-failures", "--format", "json", "--limit", "5"])

    assert args.command == "delivery-failures"
    assert args.format == "json"
    assert args.limit == 5


def test_delivery_failures_json_output_shape_without_live_postgres(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)
    pool = _FakeDeliveryPool(
        [
            {
                "delivery_id": "11111111-1111-1111-1111-111111111111",
                "alert_id": "22222222-2222-2222-2222-222222222222",
                "channel": "localhost_http_receiver",
                "destination": "http://127.0.0.1:8765/alerts",
                "status": "failed",
                "attempt_count": 3,
                "last_attempt_at": datetime(2026, 6, 16, 12, 35, tzinfo=timezone.utc),
                "delivered_at": None,
                "last_error": "connection refused",
                "created_at": datetime(2026, 6, 16, 12, 30, tzinfo=timezone.utc),
                "rule_key": "large_trade_absolute_v1",
                "severity": "high",
                "confidence": "medium",
                "venue_code": "polymarket",
                "market_id": "33333333-3333-3333-3333-333333333333",
                "market_title": "Will BTC close above 100k?",
                "summary": "Large buy detected",
                "payload_preview": '{"alert":"large buy"}',
            }
        ]
    )
    calls = {"create_pool": 0, "close_pool": 0}

    async def create_pool(*args, **kwargs):
        calls["create_pool"] += 1
        return pool

    async def close_pool(pool_arg):
        calls["close_pool"] += 1
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["delivery-failures", "--format", "json", "--limit", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload == {
        "ok": True,
        "count": 1,
        "delivery_failures": [
            {
                "delivery_id": "11111111-1111-1111-1111-111111111111",
                "alert_id": "22222222-2222-2222-2222-222222222222",
                "channel": "localhost_http_receiver",
                "destination": "http://127.0.0.1:8765/alerts",
                "status": "failed",
                "attempt_count": 3,
                "last_attempt_at": "2026-06-16T12:35:00+00:00",
                "delivered_at": None,
                "last_error": "connection refused",
                "created_at": "2026-06-16T12:30:00+00:00",
                "rule_key": "large_trade_absolute_v1",
                "severity": "high",
                "confidence": "medium",
                "venue_code": "polymarket",
                "market_id": "33333333-3333-3333-3333-333333333333",
                "market_title": "Will BTC close above 100k?",
                "summary": "Large buy detected",
                "payload_preview": '{"alert":"large buy"}',
            }
        ],
    }
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert pool.args == [(1,)]
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)
    assert all("alert_deliveries" in sql for sql in pool.sql)


def test_delivery_failures_table_empty_message(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)
    pool = _FakeDeliveryPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["delivery-failures"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "No non-delivered alert deliveries" in out


def test_delivery_failures_json_returns_nonzero_when_config_fails(monkeypatch, capsys):
    def fail_config():
        raise RuntimeError("bad config")

    monkeypatch.setattr("pmfi.config.load_config", fail_config)

    rc = main(["delivery-failures", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "bad config"


def test_delivery_failures_json_returns_nonzero_when_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["delivery-failures", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "DB unavailable: postgres unavailable"
    assert payload["next_actions"] == [
        "Start local Postgres with 'python scripts\\db_local.py up'.",
        "Verify local Postgres with 'python scripts\\db_local.py verify'.",
    ]


def test_delivery_failures_table_returns_actionable_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["delivery-failures"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "[delivery-failures] DB unavailable: postgres unavailable" in out
    assert "python scripts\\db_local.py up" in out
    assert "python scripts\\db_local.py verify" in out


def test_delivery_failures_json_returns_nonzero_when_query_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)

    class QueryFailPool(_FakeDeliveryPool):
        async def fetch(self, sql, *args):
            self.sql.append(sql)
            self.args.append(args)
            raise RuntimeError("query failed")

    pool = QueryFailPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["delivery-failures", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "query failed"
    assert pool.closed is True
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)
    assert all("alert_deliveries" in sql for sql in pool.sql)


def test_delivery_failures_json_returns_nonzero_when_close_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)
    pool = _FakeDeliveryPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        raise RuntimeError("close failed")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["delivery-failures", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "close failed"


def test_delivery_failures_does_not_import_live_adapters(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _delivery_config)
    pool = _FakeDeliveryPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters"):
            raise AssertionError(f"delivery-failures imported live adapter {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["delivery-failures", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
