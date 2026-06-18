import builtins
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from pmfi.cli import _build_parser, main


def _incident_config():
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(database=database)


class _FakeIncidentPool:
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


def test_data_quality_incidents_parser_accepts_json_format_and_limit():
    parser = _build_parser()
    args = parser.parse_args(["data-quality-incidents", "--format", "json", "--limit", "5"])

    assert args.command == "data-quality-incidents"
    assert args.format == "json"
    assert args.limit == 5


def test_data_quality_incidents_json_output_shape_without_live_postgres(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)
    pool = _FakeIncidentPool(
        [
            {
                "incident_id": "11111111-1111-1111-1111-111111111111",
                "venue_code": "kalshi",
                "market_id": "22222222-2222-2222-2222-222222222222",
                "incident_type": "missing_trade_side",
                "severity": "medium",
                "status": "open",
                "started_at": datetime(2026, 6, 16, 12, 30, tzinfo=timezone.utc),
                "ended_at": None,
                "summary": "trade side missing from source payload",
                "details": {"source_field": "side"},
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

    rc = main(["data-quality-incidents", "--format", "json", "--limit", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload == {
        "ok": True,
        "count": 1,
        "data_quality_incidents": [
            {
                "incident_id": "11111111-1111-1111-1111-111111111111",
                "venue_code": "kalshi",
                "market_id": "22222222-2222-2222-2222-222222222222",
                "incident_type": "missing_trade_side",
                "severity": "medium",
                "status": "open",
                "started_at": "2026-06-16T12:30:00+00:00",
                "ended_at": None,
                "summary": "trade side missing from source payload",
                "details": {"source_field": "side"},
            }
        ],
    }
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert pool.args == [(1,)]
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)
    assert "v_open_data_quality_incidents" in pool.sql[0]


def test_data_quality_incidents_table_empty_message(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)
    pool = _FakeIncidentPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["data-quality-incidents"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "No open data-quality incidents" in out


def test_data_quality_incidents_json_returns_nonzero_when_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["data-quality-incidents", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "DB unavailable: postgres unavailable"
    assert payload["next_actions"] == [
        "Start local Postgres with 'python scripts\\db_local.py up'.",
        "Verify local Postgres with 'python scripts\\db_local.py verify'.",
    ]


def test_data_quality_incidents_table_returns_actionable_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["data-quality-incidents"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "[data-quality-incidents] DB unavailable: postgres unavailable" in out
    assert "python scripts\\db_local.py up" in out
    assert "python scripts\\db_local.py verify" in out


def test_data_quality_incidents_json_returns_nonzero_when_query_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)

    class QueryFailPool(_FakeIncidentPool):
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

    rc = main(["data-quality-incidents", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "query failed"
    assert pool.closed is True


def test_data_quality_incidents_json_returns_nonzero_when_config_fails(monkeypatch, capsys):
    def fail_config():
        raise RuntimeError("bad config")

    monkeypatch.setattr("pmfi.config.load_config", fail_config)

    rc = main(["data-quality-incidents", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "bad config"


def test_data_quality_incidents_json_returns_nonzero_when_close_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)
    pool = _FakeIncidentPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        raise RuntimeError("close failed")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["data-quality-incidents", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "close failed"


def test_data_quality_incidents_does_not_import_live_adapters(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _incident_config)
    pool = _FakeIncidentPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        await pool_arg.close()

    real_import = builtins.__import__

    def guard_import(name, *args, **kwargs):
        if name.startswith("pmfi.adapters"):
            raise AssertionError(f"data-quality-incidents imported live adapter {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)
    monkeypatch.setattr(builtins, "__import__", guard_import)

    rc = main(["data-quality-incidents", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True
