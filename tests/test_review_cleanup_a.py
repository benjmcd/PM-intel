from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_evidence_remote_sanitizer_strips_userinfo_and_scanner_flags_it() -> None:
    from pmfi.qualification.evidence import contains_secret_text, sanitize_git_remote

    raw_url = "https://user:token-value@example.com/org/repo.git"

    assert sanitize_git_remote(raw_url) == "https://example.com/org/repo.git"
    assert contains_secret_text("", {"remote": raw_url}) is True
    assert "token-value" not in sanitize_git_remote(raw_url)


def test_evidence_remote_sanitizer_handles_malformed_userinfo_forms() -> None:
    from pmfi.qualification.evidence import contains_secret_text, sanitize_git_remote

    cases = {
        "https://user:p@ss/word@host": "https://host",
        "https:\\\\user:tok@host": "https://host",
        "oauth2:tok@[::1]": "oauth2:tok@[::1]",
    }

    for raw, expected in cases.items():
        sanitized = sanitize_git_remote(raw)
        assert sanitized == expected
        if sanitized and "://" in sanitized:
            assert contains_secret_text("", {"remote": sanitized}) is False


def test_db_local_applies_dead_letters_dedupe_guard_migration() -> None:
    from scripts import db_local

    assert "sql/014_dead_letters_dedupe_guard.sql" in db_local.SQL_FILES


def test_dead_letters_dedupe_guard_reconciles_duplicates_without_delete() -> None:
    from inspect import getsource

    from pmfi.db import migrations
    from scripts import db_local

    migration_sql = (db_local.ROOT / "sql" / "014_dead_letters_dedupe_guard.sql").read_text(
        encoding="utf-8"
    )
    startup_source = getsource(migrations.apply_schema_migrations)

    for text in (migration_sql, startup_source):
        assert "DO $$" in text
        assert "ROW_NUMBER() OVER" in text
        assert "UPDATE dead_letters" in text
        assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_dead_letters_raw_stage_class_dedupe" in text
        assert "DELETE FROM dead_letters" not in text


def test_schema_fingerprint_includes_all_sql_migrations(tmp_path) -> None:
    from pmfi.qualification.evidence import schema_fingerprint

    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    first = sql_dir / "001_init.sql"
    second = sql_dir / "002_extra.sql"
    first.write_text("create table one(id int);\n", encoding="utf-8")
    second.write_text("alter table one add column name text;\n", encoding="utf-8")

    original = schema_fingerprint(sql_dir)
    second.write_text("alter table one add column changed text;\n", encoding="utf-8")

    assert schema_fingerprint(sql_dir) != original


def test_dq1_process_events_measures_uncapped_burst_size(monkeypatch) -> None:
    from pmfi.domain import RawEvent
    from pmfi.qualification import dq1_capture

    async def fake_pipeline(events, pool, engine, handler):
        processed = 0
        async for _event in events:
            processed += 1
        return processed

    now = dq1_capture.datetime.now(dq1_capture.timezone.utc)
    events = [
        RawEvent(
            venue_code="polymarket",
            source_channel="dq1-burst-test",
            source_event_type="last_trade_price",
            source_event_id=f"burst-{idx}",
            venue_market_id="DQ1-BURST-TEST",
            exchange_ts=now,
            received_at=now,
            payload={
                "trade_id": f"burst-{idx}",
                "market": "DQ1-BURST-TEST",
                "outcome": "yes",
                "side": "buy",
                "price": "0.50",
                "size": "1",
            },
        )
        for idx in range(6)
    ]
    state = {"buffer_high_water_mark": 0, "generated_observations": 0, "extracted_raw_events": 0}
    monkeypatch.setattr(dq1_capture, "run_adapter_pipeline", fake_pipeline)

    processed = asyncio.run(
        dq1_capture._process_events(None, object(), events, buffer_limit=4, state=state)
    )

    assert processed == 6
    assert state["buffer_high_water_mark"] == 6


def test_dq1_checkpoint_requires_full_page_processing() -> None:
    from pmfi.qualification.dq1_capture import _page_completed

    assert _page_completed([object(), object()], processed_count=1) is False
    assert _page_completed([object(), object()], processed_count=2) is True
    assert _page_completed([], processed_count=0) is True


def test_dq1_lineage_verification_requires_stored_observation_metadata() -> None:
    from pmfi.qualification.dq1_capture import _count_verified_lineages

    manifest = {
        "pages": [
            {
                "page_id": "page-001",
                "frame_id": "frame-001",
                "items": [
                    {
                        "source_event_id": "source-001",
                        "expect_persisted": True,
                        "payload": {
                            "trade_id": "source-001",
                            "dq1_observation": {
                                "page_id": "page-001",
                                "frame_id": "frame-001",
                                "item_ordinal": 0,
                            },
                        },
                    }
                ],
            }
        ]
    }
    rows = [
        {
            "source_event_id": "source-001",
            "payload": {"trade_id": "source-001"},
            "payload_hash": "unused",
        }
    ]

    assert _count_verified_lineages(rows, manifest) == 0

    rows[0]["payload"] = {
        "trade_id": "source-001",
        "dq1_observation": {
            "page_id": "page-001",
            "frame_id": "frame-001",
            "item_ordinal": 0,
        },
    }

    assert _count_verified_lineages(rows, manifest) == 1


def test_single_active_lock_retries_transient_connect_failure(monkeypatch) -> None:
    from pmfi.db.advisory_lock import SingleActiveIngestLock

    class Conn:
        def __init__(self) -> None:
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def fetchval(self, *_args):
            return True

        async def close(self) -> None:
            self.closed = True

    attempts = {"count": 0}

    async def fake_connect(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("postgres still starting")
        return Conn()

    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("pmfi.db.advisory_lock.asyncpg.connect", fake_connect)
    monkeypatch.setattr("pmfi.db.advisory_lock.asyncio.sleep", fake_sleep)

    async def run() -> None:
        lock = SingleActiveIngestLock("postgresql://example", retries=2, retry_delay=0.01)
        try:
            assert await lock.acquire() is True
        finally:
            await lock.close()

    asyncio.run(run())
    assert attempts["count"] == 2


def test_fetch_polymarket_markets_paginates_past_first_100() -> None:
    from pmfi.markets import fetch_polymarket_markets

    def market(idx: int) -> dict:
        return {
            "conditionId": f"cond-{idx}",
            "question": f"Question {idx}",
            "slug": f"slug-{idx}",
            "clobTokenIds": f'["token-{idx}-yes","token-{idx}-no"]',
            "outcomes": '["Yes","No"]',
            "volumeNum": "1000",
        }

    pages = [[market(idx) for idx in range(100)], [market(idx) for idx in range(100, 150)]]
    captured_offsets: list[int] = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        async def json(self):
            return self.payload

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, _url, *, params, timeout):
            captured_offsets.append(int(params.get("offset", 0)))
            return Response(pages.pop(0))

    with patch("pmfi.markets.aiohttp") as mock_aiohttp:
        mock_aiohttp.ClientSession.return_value = Session()
        mock_aiohttp.ClientTimeout.return_value = object()
        result = asyncio.run(fetch_polymarket_markets(limit=150))

    assert len(result) == 150
    assert captured_offsets == [0, 100]


def test_large_trade_margin_uses_closest_satisfied_absolute_threshold() -> None:
    from pmfi.domain import NormalizedTrade
    from pmfi.scoring import score_large_trade

    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="closest-margin",
        outcome_key="yes",
        price=Decimal("0.50"),
        contracts=Decimal("1000000"),
        capital_at_risk_usd=Decimal("26000"),
        payout_notional_usd=Decimal("1000000"),
        directional_side="yes",
    )

    decision = score_large_trade(trade)

    assert decision.emit_alert is True
    assert set(decision.reason_codes) == {"capital_at_risk_threshold", "payout_notional_threshold"}
    assert decision.evidence["margin_to_threshold"] == 0.04


def test_market_relative_margin_binds_to_configured_floor_when_percentile_is_lower() -> None:
    from pmfi.domain import NormalizedTrade
    from pmfi.pipeline.engine import AlertEngine

    trade = NormalizedTrade(
        venue_code="polymarket",
        venue_market_id="low-percentile-market",
        outcome_key="yes",
        price=Decimal("0.50"),
        contracts=Decimal("11000"),
        capital_at_risk_usd=Decimal("5500"),
        payout_notional_usd=Decimal("11000"),
        directional_side="yes",
    )
    engine = AlertEngine(
        baselines={
            "polymarket:low-percentile-market": {
                "p99_trade_usd": 1000,
                "p995_trade_usd": 2000,
                "sample_size": 20,
            }
        }
    )

    decisions = [
        decision
        for decision in engine.evaluate(trade)
        if decision.rule_id == "market_relative_large_trade_v1"
    ]

    assert len(decisions) == 1
    assert decisions[0].evidence["threshold_percentile"] == "p995"
    assert decisions[0].evidence["margin_to_threshold"] == 0.1


def test_verify_forces_pytest_plugin_autoload_disabled(monkeypatch) -> None:
    from scripts import verify

    commands: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_load_script(_rel_path: str):
        return SimpleNamespace(main=lambda *_args: 0)

    def fake_compile_dir(_path, *, quiet: int):
        return True

    def fake_run_subprocess(args: list[str], *, env=None, timeout_seconds: int = 180) -> None:
        commands.append((args, env))

    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "0")
    monkeypatch.setattr(verify, "load_script", fake_load_script)
    monkeypatch.setattr(verify.compileall, "compile_dir", fake_compile_dir)
    monkeypatch.setattr(verify, "run_subprocess", fake_run_subprocess)

    assert verify.main() == 0
    assert commands[0][1]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
