from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from pmfi.domain import NormalizedTrade, RawEvent
from pmfi.normalization import NormalizationError
from pmfi.replay import replay_fixtures, replay_fixtures_persist, replay_from_db, ReplayResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"

def test_replay_returns_results():
    results = replay_fixtures(FIXTURE_DIR)
    assert isinstance(results, list)
    assert len(results) >= 1
    for r in results:
        assert isinstance(r, ReplayResult)
        assert r.trade is not None
        assert isinstance(r.alerts, list)

def test_replay_verbose_does_not_raise():
    results = replay_fixtures(FIXTURE_DIR, verbose=True)
    assert len(results) >= 1

def test_replay_empty_dir(tmp_path):
    results = replay_fixtures(tmp_path)
    assert results == []


def test_replay_verbose_reports_unsupported_venue(tmp_path, capsys):
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "polymarket.json").write_text(
        """
{
  "venue_code": "polymarket",
  "source_channel": "market_ws",
  "source_event_type": "last_trade_price",
  "source_event_id": "pm-good",
  "venue_market_id": "pm-good-market",
  "payload": {
    "price": "0.42",
    "size": "100",
    "market": "pm-good-market",
    "outcome": "yes"
  }
}
""",
        encoding="utf-8",
    )
    (fixture_dir / "unsupported.json").write_text(
        """
{
  "venue_code": "predictit",
  "source_channel": "fixture",
  "source_event_type": "trade",
  "source_event_id": "unsupported-1",
  "venue_market_id": "predictit-market",
  "payload": {
    "price": "0.50",
    "size": "10"
  }
}
""",
        encoding="utf-8",
    )

    results = replay_fixtures(fixture_dir, verbose=True)
    out = capsys.readouterr().out

    assert [Path(result.fixture_path).name for result in results] == ["polymarket.json"]
    assert results[0].trade.venue_code == "polymarket"
    assert "norm error unsupported.json: unsupported venue: predictit" in out


def test_replay_fixtures_persist_skips_dead_lettered_malformed_summary(monkeypatch, tmp_path):
    good_path = tmp_path / "good.json"
    bad_path = tmp_path / "bad.json"
    good_path.write_text("{}", encoding="utf-8")
    bad_path.write_text("{}", encoding="utf-8")

    good_raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="good",
        venue_market_id="good-market",
        received_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        payload={"price": "0.60", "size": "100", "market": "good-market", "outcome": "yes"},
    )
    bad_raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="bad",
        venue_market_id="bad-market",
        received_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        payload={"price": "not-a-number", "size": "100", "market": "bad-market"},
    )
    good_trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="good-market",
        outcome_key="yes",
        price=Decimal("0.60"),
        contracts=Decimal("100"),
        capital_at_risk_usd=Decimal("60.00"),
        payout_notional_usd=Decimal("100"),
        source_payload=good_raw.payload,
    )

    async def startup_maintenance(_pool):
        return None

    async def load_baselines(_pool):
        return {}

    process_event = AsyncMock()

    def load_raw_event(path):
        return good_raw if Path(path).name == "good.json" else bad_raw

    def normalize_event(raw):
        if raw.source_event_id == "bad":
            raise NormalizationError("invalid decimal for price: 'not-a-number'")
        return good_trade

    monkeypatch.setattr("pmfi.db.migrations.startup_maintenance", startup_maintenance)
    monkeypatch.setattr("pmfi.baseline.load_baselines", load_baselines)
    monkeypatch.setattr("pmfi.pipeline.runner.process_event", process_event)
    monkeypatch.setattr("pmfi.replay.load_raw_event", load_raw_event)
    monkeypatch.setattr("pmfi.replay.normalize_event", normalize_event)

    results = asyncio.run(replay_fixtures_persist(tmp_path, object(), verbose=True))

    assert [Path(result.fixture_path).name for result in results] == ["good.json"]
    assert results[0].trade == good_trade
    assert process_event.await_count == 2


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, rows):
        self.rows = rows

    def acquire(self):
        return _Acquire(self)

    async def fetch(self, *_args):
        return self.rows


def test_replay_from_db_skips_malformed_raw_rows():
    rows = [
        {
            "venue_code": "polymarket",
            "source_channel": "market_ws",
            "source_event_type": "last_trade_price",
            "source_event_id": "bad",
            "venue_market_id": "bad-market",
            "exchange_ts": None,
            "received_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
            "payload": {"price": "not-a-number", "size": "100", "market": "bad-market"},
        },
        {
            "venue_code": "polymarket",
            "source_channel": "market_ws",
            "source_event_type": "last_trade_price",
            "source_event_id": "good",
            "venue_market_id": "good-market",
            "exchange_ts": None,
            "received_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
            "payload": {"price": "0.60", "size": "100", "market": "good-market", "outcome": "yes"},
        },
    ]

    results = asyncio.run(replay_from_db(_Pool(rows), verbose=True))

    assert len(results) == 1
    assert results[0].fixture_path == "db:good-market"
    assert results[0].trade.venue_market_id == "good-market"
