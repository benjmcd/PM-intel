import json
from datetime import datetime, timezone
from types import SimpleNamespace

from pmfi.cli import _build_parser, main


def _dead_letter_config():
    database = SimpleNamespace(url="postgresql://pmfi:secret@localhost:5433/pmfi")
    return SimpleNamespace(database=database)


class _FakeDeadLetterPool:
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


def test_dead_letters_parser_accepts_json_format():
    parser = _build_parser()
    args = parser.parse_args(["dead-letters", "--format", "json", "--limit", "5"])
    assert args.command == "dead-letters"
    assert args.format == "json"
    assert args.limit == 5


def test_dead_letters_json_output_shape_without_live_postgres(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _dead_letter_config)
    pool = _FakeDeadLetterPool(
        [
            {
                "created_at": datetime(2026, 6, 16, 12, 30, tzinfo=timezone.utc),
                "venue_code": "polymarket",
                "raw_event_id": 42,
                "source_channel": "ws",
                "source_event_id": "evt-42",
                "venue_market_id": "mkt-42",
                "failure_stage": "normalize",
                "error_class": "NormalizationError",
                "error_message": "bad price",
                "payload_preview": '{"price":"bad"}',
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

    rc = main(["dead-letters", "--format", "json", "--limit", "1"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["count"] == 1
    assert len(payload["dead_letters"]) == 1
    row = payload["dead_letters"][0]
    assert row == {
        "created_at": "2026-06-16T12:30:00+00:00",
        "venue_code": "polymarket",
        "raw_event_id": 42,
        "source_channel": "ws",
        "source_event_id": "evt-42",
        "venue_market_id": "mkt-42",
        "failure_stage": "normalize",
        "error_class": "NormalizationError",
        "error_message": "bad price",
        "payload_preview": '{"price":"bad"}',
    }
    assert calls == {"create_pool": 1, "close_pool": 1}
    assert pool.closed is True
    assert pool.args == [(1,)]
    assert all(sql.lstrip().lower().startswith("select") for sql in pool.sql)


def test_dead_letters_json_returns_nonzero_when_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _dead_letter_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["dead-letters", "--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "DB unavailable: postgres unavailable"
    assert payload["next_actions"] == [
        "Start local Postgres with 'python scripts\\db_local.py up'.",
        "Verify local Postgres with 'python scripts\\db_local.py verify'.",
    ]


def test_dead_letters_table_returns_actionable_db_unavailable(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _dead_letter_config)

    async def fail_create_pool(*args, **kwargs):
        raise RuntimeError("postgres unavailable")

    monkeypatch.setattr("pmfi.db.create_pool", fail_create_pool)

    rc = main(["dead-letters"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "[dead-letters] DB unavailable: postgres unavailable" in out
    assert "python scripts\\db_local.py up" in out
    assert "python scripts\\db_local.py verify" in out


def test_dead_letters_json_returns_nonzero_when_query_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _dead_letter_config)

    class QueryFailPool(_FakeDeadLetterPool):
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

    rc = main(["dead-letters", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "query failed"
    assert pool.closed is True


def test_dead_letters_json_returns_nonzero_when_config_fails(monkeypatch, capsys):
    def fail_config():
        raise RuntimeError("bad config")

    monkeypatch.setattr("pmfi.config.load_config", fail_config)

    rc = main(["dead-letters", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "bad config"


def test_dead_letters_json_returns_nonzero_when_close_fails(monkeypatch, capsys):
    monkeypatch.setattr("pmfi.config.load_config", _dead_letter_config)
    pool = _FakeDeadLetterPool([])

    async def create_pool(*args, **kwargs):
        return pool

    async def close_pool(pool_arg):
        raise RuntimeError("close failed")

    monkeypatch.setattr("pmfi.db.create_pool", create_pool)
    monkeypatch.setattr("pmfi.db.close_pool", close_pool)

    rc = main(["dead-letters", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "close failed"
