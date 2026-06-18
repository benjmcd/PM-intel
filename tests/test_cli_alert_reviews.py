from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pmfi.cli import _build_parser, main


ALERT_ID = "11111111-1111-1111-1111-111111111111"
REVIEW_ID = "22222222-2222-2222-2222-222222222222"


def _config():
    return SimpleNamespace(database=SimpleNamespace(url="postgresql://local/db"))


class _ReviewPool:
    def __init__(self, *, alert=None, reviews=None, fp_summaries=None):
        self.alert = alert
        self.reviews = [] if reviews is None else reviews
        self.fp_summaries = [] if fp_summaries is None else fp_summaries
        self.closed = False
        self.fetchrow_calls = []
        self.fetch_calls = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        if "INSERT INTO alert_reviews" in sql:
            return {
                "review_id": REVIEW_ID,
                "alert_id": args[0],
                "label": args[1],
                "false_positive_category": args[2],
                "notes": args[3],
                "reviewed_by": args[4],
                "reviewed_at": "2026-06-17T12:00:00+00:00",
            }
        return self.alert

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "latest_reviews" in sql:
            return self.fp_summaries
        return self.reviews

    async def close(self):
        self.closed = True


class _AlertsListPool:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False
        self.fetch_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.rows

    async def close(self):
        self.closed = True


def _alert_row(**overrides):
    row = {
        "alert_id": ALERT_ID,
        "rule_key": "large_trade_absolute_v1",
        "severity": "high",
        "confidence": "medium",
        "title": "Large trade",
        "summary": "Large yes buy",
        "status": "new",
        "venue_code": "polymarket",
        "market_id": "33333333-3333-3333-3333-333333333333",
        "outcome_key": "yes",
        "fired_at": "2026-06-17T11:55:00+00:00",
        "acknowledged_at": None,
        "resolved_at": None,
        "market_title": "Election market",
    }
    row.update(overrides)
    return row


def test_alert_review_parser_accepts_new_commands_options():
    parser = _build_parser()

    review = parser.parse_args([
        "alerts",
        "review",
        ALERT_ID,
        "--label",
        "tp",
        "--category",
        "bot",
        "--notes",
        "looks real",
        "--reviewer",
        "operator",
        "--format",
        "json",
    ])
    assert review.alerts_cmd == "review"
    assert review.alert_id == ALERT_ID
    assert review.label == "tp"
    assert review.category == "bot"
    assert review.notes == "looks real"
    assert review.reviewer == "operator"
    assert review.format == "json"

    reviews = parser.parse_args([
        "alerts",
        "reviews",
        "--format",
        "json",
        "--limit",
        "5",
        "--alert-id",
        ALERT_ID,
        "--label",
        "false-positive",
    ])
    assert reviews.alerts_cmd == "reviews"
    assert reviews.limit == 5
    assert reviews.alert_id == ALERT_ID
    assert reviews.label == "false-positive"

    fp_rate = parser.parse_args([
        "alerts",
        "fp-rate",
        "--format",
        "json",
        "--since",
        "14d",
        "--bucket",
        "hour",
        "--rule",
        "large_trade_absolute_v1",
        "--limit",
        "7",
    ])
    assert fp_rate.alerts_cmd == "fp-rate"
    assert fp_rate.format == "json"
    assert fp_rate.since == "14d"
    assert fp_rate.bucket == "hour"
    assert fp_rate.rule == "large_trade_absolute_v1"
    assert fp_rate.limit == 7


def test_alert_review_fake_db_success_inserts_and_closes_pool(monkeypatch, capsys):
    pool = _ReviewPool(alert=_alert_row())

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main([
        "alerts",
        "review",
        ALERT_ID,
        "--label",
        "false-positive",
        "--category",
        "known_bot",
        "--notes",
        "same wallet pattern",
        "--reviewer",
        "operator",
        "--format",
        "json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert pool.closed is True
    assert payload["ok"] is True
    assert payload["review"]["review_id"] == REVIEW_ID
    assert payload["review"]["alert_id"] == ALERT_ID
    assert payload["review"]["label"] == "false_positive"
    assert payload["review"]["false_positive_category"] == "known_bot"
    assert payload["review"]["notes"] == "same wallet pattern"
    assert payload["review"]["reviewed_by"] == "operator"
    assert payload["alert"]["rule_key"] == "large_trade_absolute_v1"
    assert len(pool.fetchrow_calls) == 2
    assert "SELECT" in pool.fetchrow_calls[0][0]
    assert "INSERT INTO alert_reviews" in pool.fetchrow_calls[1][0]


def test_alert_review_fake_db_alert_not_found_returns_useful_json(monkeypatch, capsys):
    pool = _ReviewPool(alert=None)

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["alerts", "review", ALERT_ID, "--label", "tp", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert pool.closed is True
    assert payload["ok"] is False
    assert payload["alert_id"] == ALERT_ID
    assert "alert not found" in payload["error"]
    assert any("pmfi alerts list --format json" in action for action in payload["next_actions"])
    assert len(pool.fetchrow_calls) == 1
    assert "INSERT INTO alert_reviews" not in pool.fetchrow_calls[0][0]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["alerts", "review", ALERT_ID, "--label", "maybe", "--format", "json"], "invalid review label"),
        (["alerts", "review", "not-a-uuid", "--label", "tp", "--format", "json"], "invalid alert_id"),
    ],
)
def test_alert_review_invalid_input_fails_before_db(monkeypatch, capsys, argv, expected):
    async def create_pool(*args, **kwargs):
        raise AssertionError("DB should not be touched for invalid input")

    def load_config():
        raise AssertionError("config should not be touched for invalid input")

    monkeypatch.setattr("pmfi.config.load_config", load_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(argv)
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert expected in payload["error"]


def test_alert_reviews_json_lists_rows_with_select_only_behavior(monkeypatch, capsys):
    pool = _ReviewPool(
        reviews=[
            {
                "review_id": REVIEW_ID,
                "alert_id": ALERT_ID,
                "label": "false_positive",
                "false_positive_category": "known_bot",
                "notes": "same wallet pattern",
                "reviewed_by": "operator",
                "reviewed_at": "2026-06-17T12:00:00+00:00",
                **_alert_row(),
            }
        ]
    )

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main([
        "alerts",
        "reviews",
        "--format",
        "json",
        "--limit",
        "5",
        "--alert-id",
        ALERT_ID,
        "--label",
        "fp",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert pool.closed is True
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["reviews"][0]["review_id"] == REVIEW_ID
    assert payload["reviews"][0]["alert_id"] == ALERT_ID
    assert payload["reviews"][0]["label"] == "false_positive"
    assert payload["reviews"][0]["rule_key"] == "large_trade_absolute_v1"
    assert len(pool.fetch_calls) == 1
    assert "SELECT" in pool.fetch_calls[0][0]
    assert "INSERT" not in pool.fetch_calls[0][0]
    assert pool.fetch_calls[0][1] == (ALERT_ID, "false_positive", 5)
    assert pool.fetchrow_calls == []


def test_alert_fp_rate_json_summarizes_latest_reviews(monkeypatch, capsys):
    pool = _ReviewPool(
        fp_summaries=[
            {
                "rule_key": "large_trade_absolute_v1",
                "bucket_start": "2026-06-17T00:00:00+00:00",
                "reviewed_count": 4,
                "false_positive_count": 2,
                "true_positive_count": 1,
                "noise_count": 1,
                "unsure_count": 0,
                "false_positive_rate": "0.5",
            }
        ]
    )

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main([
        "alerts",
        "fp-rate",
        "--format",
        "json",
        "--since",
        "30d",
        "--bucket",
        "day",
        "--rule",
        "large_trade_absolute_v1",
        "--limit",
        "10",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert pool.closed is True
    assert payload["ok"] is True
    assert payload["since"] == "30d"
    assert payload["bucket"] == "day"
    assert payload["rule"] == "large_trade_absolute_v1"
    assert payload["count"] == 1
    assert payload["summaries"][0] == {
        "rule_key": "large_trade_absolute_v1",
        "bucket": "2026-06-17T00:00:00+00:00",
        "reviewed_count": 4,
        "false_positive_count": 2,
        "true_positive_count": 1,
        "noise_count": 1,
        "unsure_count": 0,
        "false_positive_rate": 0.5,
    }


def test_alert_fp_rate_sql_uses_latest_review_per_alert(monkeypatch, capsys):
    pool = _ReviewPool(
        fp_summaries=[
            {
                "rule_key": "market_relative_large_trade_v1",
                "bucket_start": None,
                "reviewed_count": 1,
                "false_positive_count": 0,
                "true_positive_count": 1,
                "noise_count": 0,
                "unsure_count": 0,
                "false_positive_rate": 0,
            }
        ]
    )

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(["alerts", "fp-rate", "--format", "json", "--bucket", "all", "--limit", "3"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["summaries"][0]["bucket"] is None
    sql, args = pool.fetch_calls[0]
    assert "DISTINCT ON (ar.alert_id)" in sql
    assert "ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC" in sql
    assert "JOIN latest_reviews lr ON lr.alert_id = a.alert_id" in sql
    assert "COUNT(*) FILTER (WHERE lr.label = 'false_positive')" in sql
    assert args[-1] == 3


@pytest.mark.parametrize(
    "argv",
    [
        ["alerts", "review", ALERT_ID, "--label", "tp", "--format", "json"],
        ["alerts", "reviews", "--format", "json"],
        ["alerts", "fp-rate", "--format", "json"],
    ],
)
def test_alert_review_commands_report_db_unavailable_without_traceback(monkeypatch, capsys, argv):
    async def create_pool(*args, **kwargs):
        raise RuntimeError("db offline for test")

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["error"] == "DB unavailable: db offline for test"
    assert any("db_local.py up" in action for action in payload["next_actions"])
    assert any("db_local.py verify" in action for action in payload["next_actions"])
    assert "Traceback" not in captured.out
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["alerts", "fp-rate", "--format", "json", "--limit", "0"], "invalid --limit"),
        (["alerts", "fp-rate", "--format", "json", "--since", "later"], "invalid --since"),
    ],
)
def test_alert_fp_rate_invalid_input_fails_before_config_or_db(monkeypatch, capsys, argv, expected):
    async def create_pool(*args, **kwargs):
        raise AssertionError("DB should not be touched for invalid input")

    def load_config():
        raise AssertionError("config should not be touched for invalid input")

    monkeypatch.setattr("pmfi.config.load_config", load_config)
    monkeypatch.setattr("pmfi.db.create_pool", create_pool)

    rc = main(argv)
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert expected in payload["error"]


def test_alerts_list_json_includes_alert_id_in_fake_success(monkeypatch, capsys):
    pool = _AlertsListPool(rows=[_alert_row(score="0.91")])

    async def create_pool(*args, **kwargs):
        return pool

    import asyncpg

    monkeypatch.setattr("pmfi.config.load_config", _config)
    monkeypatch.setattr(asyncpg, "create_pool", create_pool)

    rc = main(["alerts", "list", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert pool.closed is True
    assert payload[0]["alert_id"] == ALERT_ID
    assert "a.alert_id::text AS alert_id" in pool.fetch_calls[0][0]
