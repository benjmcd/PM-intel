from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from db_scratch import (
    TESTISO_DB_PREFIX,
    ScratchDatabase,
    create_test_scratch_database,
    drop_test_scratch_database,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq4_live_manifest.yaml"
SOURCE_CHANNEL = "dq4_offline_invariant_v1"
GOOD_HEALTH = {"status": "OK", "intake_allowed": True}
GOOD_STATUS = {
    "kalshi": {"circuit_open": False},
    "polymarket": {"circuit_open": False},
}
_SCRATCH_DB: ScratchDatabase | None = None


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _scratch_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("DQ4 non-live scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module")
def _dq4_live_trial_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("dq4_live_trial")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


@pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)
def test_dq4_non_live_db_tests_use_scratch_db_not_configured_primary(
    _dq4_live_trial_scratch_database,
) -> None:
    assert _SCRATCH_DB is not None
    assert _scratch_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}dq4_live_trial_")
    assert _SCRATCH_DB.name in _scratch_dsn()


async def _insert_raw_event(conn, *, venue: str, market_id, market: str, event_id: str, event_type: str, received_at):
    return await conn.fetchval(
        """INSERT INTO raw_events
             (venue_code, source_channel, source_event_type, source_event_id,
              market_id, venue_market_id, exchange_ts, received_at, payload)
           VALUES
             ($1, $2, $3, $4, $5::uuid, $6, $7, $7,
              jsonb_build_object('market', $6::text, 'id', $4::text))
           RETURNING raw_event_id""",
        venue,
        SOURCE_CHANNEL,
        event_type,
        event_id,
        market_id,
        market,
        received_at,
    )


async def _insert_normalized_trade(conn, *, raw_id: int, venue: str, market_id, trade_id: str, received_at):
    await conn.execute(
        """INSERT INTO normalized_trades
             (raw_event_id, raw_event_received_at, venue_code, venue_trade_id,
              market_id, outcome_key, aggressor_side, directional_side,
              side_confidence, price, contracts, capital_at_risk_usd,
              payout_notional_usd, exchange_ts, received_at,
              normalization_version, source_payload)
           VALUES
             ($1, $2, $3, $4, $5::uuid, 'yes', 'buy', 'yes',
              'high', 0.50, 10, 5, 10, $2, $2,
              'dq4.test', '{}'::jsonb)""",
        raw_id,
        received_at,
        venue,
        trade_id,
        market_id,
    )


def test_dq4_invariant_logic_fires_structural_red_controls() -> None:
    from pmfi.qualification.dq4_live import (
        classify_dq4_dual_venue_liveness,
        evaluate_dq4_integrity_invariants,
        evaluate_dq4_pass_invariants,
    )

    good = {
        "coverage": {"counts": {"unaccounted": 0}},
        "duplicate_canonical_facts": 0,
        "dead_letter_rate": 0.0,
        "dead_letter_rate_threshold": 0.05,
        "per_venue_counts": {"kalshi": 1, "polymarket": 2},
        "required_venues": ["kalshi", "polymarket"],
        "min_per_venue": 1,
        "operational_health": GOOD_HEALTH,
        "status_map": GOOD_STATUS,
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }

    assert all(evaluate_dq4_pass_invariants(good).values())
    assert all(evaluate_dq4_integrity_invariants(good).values())
    assert classify_dq4_dual_venue_liveness(good)["status"] == "OBSERVED"

    unaccounted = {**good, "coverage": {"counts": {"unaccounted": 1}}}
    assert evaluate_dq4_pass_invariants(unaccounted)["no_silent_loss_every_raw_event_accounted"] is False

    duplicate = {**good, "duplicate_canonical_facts": 1}
    assert evaluate_dq4_pass_invariants(duplicate)["no_duplicate_canonical_facts_in_window"] is False

    dead_letter_rate = {**good, "dead_letter_rate": 0.06}
    assert evaluate_dq4_pass_invariants(dead_letter_rate)["dead_letter_rate_within_threshold"] is False

    missing_venue = {**good, "per_venue_counts": {"kalshi": 1, "polymarket": 0}}
    assert evaluate_dq4_pass_invariants(missing_venue)["both_venues_captured"] is False

    unhealthy = {**good, "status_map": {"polymarket": {"circuit_open": True}}}
    assert evaluate_dq4_pass_invariants(unhealthy)["operational_health_ok_during_run"] is False


def test_dq4_quiet_required_venue_is_liveness_inconclusive_not_integrity_failure() -> None:
    from pmfi.qualification.dq4_live import (
        classify_dq4_dual_venue_liveness,
        evaluate_dq4_integrity_invariants,
        evaluate_dq4_pass_invariants,
    )

    quiet_venue = {
        "coverage": {"counts": {"unaccounted": 0}},
        "duplicate_canonical_facts": 0,
        "dead_letter_rate": 0.0,
        "dead_letter_rate_threshold": 0.05,
        "per_venue_counts": {"kalshi": 3, "polymarket": 0},
        "required_venues": ["kalshi", "polymarket"],
        "min_per_venue": 1,
        "operational_health": GOOD_HEALTH,
        "status_map": GOOD_STATUS,
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }

    assert evaluate_dq4_pass_invariants(quiet_venue)["both_venues_captured"] is False
    assert all(evaluate_dq4_integrity_invariants(quiet_venue).values())
    assert classify_dq4_dual_venue_liveness(quiet_venue) == {
        "status": "INCONCLUSIVE_BOUNDED",
        "observed_venues": ["kalshi"],
        "missing_venues": ["polymarket"],
        "min_per_venue": 1,
    }


def test_dq4_missing_health_and_secret_inputs_fail_closed() -> None:
    from pmfi.qualification.dq4_live import evaluate_dq4_integrity_invariants

    missing_inputs = {
        "coverage": {"counts": {"unaccounted": 0}},
        "duplicate_canonical_facts": 0,
        "dead_letter_rate": 0.0,
        "dead_letter_rate_threshold": 0.05,
        "per_venue_counts": {"kalshi": 1, "polymarket": 1},
        "required_venues": ["kalshi", "polymarket"],
        "min_per_venue": 1,
    }

    invariants = evaluate_dq4_integrity_invariants(missing_inputs)

    assert invariants["operational_health_ok_during_run"] is False
    assert invariants["no_secrets_in_fixtures_logs_or_evidence"] is False


def test_dq4_persisted_ingest_honors_existing_max_events_cap() -> None:
    import inspect

    from pmfi.cli import cmd_ingest

    source = inspect.getsource(cmd_ingest)
    assert 'max_events = getattr(args, "max_events", 0)' in source
    assert "if max_events and _events_seen[0] >= max_events:" in source
    assert "shutdown.set()" in source


@pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)
def test_dq4_seeded_window_measurements_are_scoped_and_cleaned(
    _dq4_live_trial_scratch_database,
) -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq4_live import (
        cleanup_dq4_offline_rows,
        collect_dq4_window_measurements,
        evaluate_dq4_pass_invariants,
    )

    async def _run() -> None:
        pool = await create_pool(_scratch_dsn())
        try:
            await cleanup_dq4_offline_rows(pool, SOURCE_CHANNEL)
            async with pool.acquire() as conn:
                started_at = datetime.now(timezone.utc).replace(microsecond=0)
                ended_at = started_at + timedelta(seconds=30)
                market_rows = {}
                for venue, market in (
                    ("polymarket", "DQ4-LIVE-POLY"),
                    ("kalshi", "DQ4-LIVE-KALSHI"),
                ):
                    market_rows[venue] = await conn.fetchval(
                        """INSERT INTO markets
                             (venue_code, venue_market_id, title, status, watched)
                           VALUES ($1, $2, $3, 'active', false)
                           ON CONFLICT (venue_code, venue_market_id)
                           DO UPDATE SET title = EXCLUDED.title
                           RETURNING market_id""",
                        venue,
                        market,
                        f"DQ4 offline {venue}",
                    )
                poly_raw = await conn.fetchval(
                    """INSERT INTO raw_events
                         (venue_code, source_channel, source_event_type, source_event_id,
                          market_id, venue_market_id, exchange_ts, received_at, payload)
                       VALUES
                         ('polymarket', $1, 'last_trade_price', 'dq4-poly-trade',
                          $2::uuid, 'DQ4-LIVE-POLY', $3, $3, '{"trade_id":"dq4-poly-trade","market":"DQ4-LIVE-POLY"}'::jsonb)
                       RETURNING raw_event_id""",
                    SOURCE_CHANNEL,
                    market_rows["polymarket"],
                    started_at + timedelta(seconds=1),
                )
                await conn.execute(
                    """INSERT INTO raw_events
                         (venue_code, source_channel, source_event_type, source_event_id,
                          market_id, venue_market_id, exchange_ts, received_at, payload)
                       VALUES
                         ('polymarket', $1, 'price_change', 'dq4-poly-skip',
                          $2::uuid, 'DQ4-LIVE-POLY', $3, $3, '{"market":"DQ4-LIVE-POLY"}'::jsonb)""",
                    SOURCE_CHANNEL,
                    market_rows["polymarket"],
                    started_at + timedelta(seconds=2),
                )
                kalshi_raw = await conn.fetchval(
                    """INSERT INTO raw_events
                         (venue_code, source_channel, source_event_type, source_event_id,
                          market_id, venue_market_id, exchange_ts, received_at, payload)
                       VALUES
                         ('kalshi', $1, 'trade', 'dq4-kalshi-trade',
                          $2::uuid, 'DQ4-LIVE-KALSHI', $3, $3, '{"trade_id":"dq4-kalshi-trade","ticker":"DQ4-LIVE-KALSHI"}'::jsonb)
                       RETURNING raw_event_id""",
                    SOURCE_CHANNEL,
                    market_rows["kalshi"],
                    started_at + timedelta(seconds=3),
                )
                for raw_id, venue, market_id, trade_id in (
                    (poly_raw, "polymarket", market_rows["polymarket"], "dq4-poly-trade"),
                    (kalshi_raw, "kalshi", market_rows["kalshi"], "dq4-kalshi-trade"),
                ):
                    await conn.execute(
                        """INSERT INTO normalized_trades
                             (raw_event_id, raw_event_received_at, venue_code, venue_trade_id,
                              market_id, outcome_key, aggressor_side, directional_side,
                              side_confidence, price, contracts, capital_at_risk_usd,
                              payout_notional_usd, exchange_ts, received_at,
                              normalization_version, source_payload)
                           VALUES
                             ($1, $2, $3, $4, $5::uuid, 'yes', 'buy', 'yes',
                              'high', 0.50, 10, 5, 10, $2, $2,
                              'dq4.test', '{}'::jsonb)""",
                        raw_id,
                        started_at + timedelta(seconds=4),
                        venue,
                        trade_id,
                        market_id,
                    )

            measurements = await collect_dq4_window_measurements(
                pool,
                started_at,
                ended_at,
                required_venues=["kalshi", "polymarket"],
                dead_letter_rate_threshold=0.05,
                min_per_venue=1,
                source_channel=SOURCE_CHANNEL,
                operational_health=GOOD_HEALTH,
                status_map=GOOD_STATUS,
                no_secrets_in_fixtures_logs_or_evidence=True,
            )
            invariants = evaluate_dq4_pass_invariants(measurements)

            assert measurements["raw_event_count"] == 3
            assert measurements["coverage"]["counts"]["normalized"] == 2
            assert measurements["coverage"]["counts"]["skipped_non_trade"] == 1
            assert measurements["per_venue_counts"] == {"kalshi": 1, "polymarket": 2}
            assert all(invariants.values()), invariants
        finally:
            await cleanup_dq4_offline_rows(pool, SOURCE_CHANNEL)
            async with pool.acquire() as conn:
                leftovers = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1",
                    SOURCE_CHANNEL,
                )
            await pool.close()
            assert int(leftovers or 0) == 0

    asyncio.run(_run())


@pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)
def test_dq4_db_measurements_fire_planted_sql_red_controls(
    _dq4_live_trial_scratch_database,
) -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq4_live import (
        cleanup_dq4_offline_rows,
        collect_dq4_window_measurements,
        evaluate_dq4_pass_invariants,
    )

    async def _run() -> None:
        pool = await create_pool(_scratch_dsn())
        try:
            await cleanup_dq4_offline_rows(pool, SOURCE_CHANNEL)
            async with pool.acquire() as conn:
                started_at = datetime.now(timezone.utc).replace(microsecond=0)
                ended_at = started_at + timedelta(seconds=30)
                market_id = await conn.fetchval(
                    """INSERT INTO markets
                         (venue_code, venue_market_id, title, status, watched)
                       VALUES ('polymarket', 'DQ4-LIVE-RED', 'DQ4 red controls', 'active', false)
                       ON CONFLICT (venue_code, venue_market_id)
                       DO UPDATE SET title = EXCLUDED.title
                       RETURNING market_id"""
                )
                unaccounted_raw = await _insert_raw_event(
                    conn,
                    venue="polymarket",
                    market_id=market_id,
                    market="DQ4-LIVE-RED",
                    event_id="dq4-red-unaccounted",
                    event_type="last_trade_price",
                    received_at=started_at + timedelta(seconds=1),
                )
                dup_raw_1 = await _insert_raw_event(
                    conn,
                    venue="polymarket",
                    market_id=market_id,
                    market="DQ4-LIVE-RED",
                    event_id="dq4-red-dup-1",
                    event_type="last_trade_price",
                    received_at=started_at + timedelta(seconds=2),
                )
                dup_raw_2 = await _insert_raw_event(
                    conn,
                    venue="polymarket",
                    market_id=market_id,
                    market="DQ4-LIVE-RED",
                    event_id="dq4-red-dup-2",
                    event_type="last_trade_price",
                    received_at=started_at + timedelta(seconds=3),
                )
                for raw_id in (dup_raw_1, dup_raw_2):
                    await _insert_normalized_trade(
                        conn,
                        raw_id=raw_id,
                        venue="polymarket",
                        market_id=market_id,
                        trade_id="dq4-red-duplicate-trade",
                        received_at=started_at + timedelta(seconds=4),
                    )
                for idx in range(3):
                    raw_id = await _insert_raw_event(
                        conn,
                        venue="polymarket",
                        market_id=market_id,
                        market="DQ4-LIVE-RED",
                        event_id=f"dq4-red-dead-{idx}",
                        event_type="last_trade_price",
                        received_at=started_at + timedelta(seconds=5 + idx),
                    )
                    await conn.execute(
                        """INSERT INTO dead_letters
                             (venue_code, raw_event_id, source_channel, failure_stage,
                              error_class, error_message, payload)
                           VALUES
                             ('polymarket', $1, $2, 'normalization',
                              'dq4_red_control', 'dq4 planted red control', '{}'::jsonb)""",
                        raw_id,
                        SOURCE_CHANNEL,
                    )

            measurements = await collect_dq4_window_measurements(
                pool,
                started_at,
                ended_at,
                required_venues=["polymarket"],
                dead_letter_rate_threshold=0.25,
                min_per_venue=1,
                source_channel=SOURCE_CHANNEL,
                operational_health=GOOD_HEALTH,
                status_map={"polymarket": {"circuit_open": False}},
                no_secrets_in_fixtures_logs_or_evidence=True,
            )
            invariants = evaluate_dq4_pass_invariants(measurements)

            assert unaccounted_raw is not None
            assert measurements["coverage"]["counts"]["unaccounted"] == 1
            assert measurements["duplicate_canonical_facts"] == 1
            assert measurements["dead_letter_count"] == 3
            assert measurements["dead_letter_rate"] == pytest.approx(0.5)
            assert invariants["no_silent_loss_every_raw_event_accounted"] is False
            assert invariants["no_duplicate_canonical_facts_in_window"] is False
            assert invariants["dead_letter_rate_within_threshold"] is False
        finally:
            await cleanup_dq4_offline_rows(pool, SOURCE_CHANNEL)
            async with pool.acquire() as conn:
                leftovers = await conn.fetchval(
                    "SELECT COUNT(*) FROM raw_events WHERE source_channel = $1",
                    SOURCE_CHANNEL,
                )
            await pool.close()
            assert int(leftovers or 0) == 0

    asyncio.run(_run())


@pytest.mark.skipif(
    not (
        os.environ.get("PMFI_DB_URL")
        and os.environ.get("PMFI_ENABLE_LIVE") == "1"
    ),
    reason="Requires PMFI_DB_URL and explicit PMFI_ENABLE_LIVE=1 for bounded live capture",
)
def test_dq4_live_trial_double_gated_bounded_read_only() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq4_live import run_dq4_live_trial

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            evidence = await run_dq4_live_trial(
                pool,
                MANIFEST,
                max_seconds=int(os.environ.get("PMFI_DQ4_MAX_SECONDS", "60")),
                max_events=int(os.environ.get("PMFI_DQ4_MAX_EVENTS", "250")),
                db_url=_dsn(),
            )
            assert evidence["scenario_id"] == "DQ-4"
            assert evidence["outcome"] == "PASS", evidence
            assert "BOUNDED_LIVE" in evidence["evidence"]["actual_facets"]
            liveness = evidence["evidence"]["dual_venue_liveness"]
            if liveness["status"] == "OBSERVED":
                assert "DUAL_VENUE" in evidence["evidence"]["actual_facets"]
            else:
                assert liveness["status"] == "INCONCLUSIVE_BOUNDED"
                assert "DUAL_VENUE" not in evidence["evidence"]["actual_facets"]
            assert "LONG_HORIZON_SOAK" in evidence["evidence"]["deferred_facets"]
            assert "KNOWN_ANSWER_NOT_APPLICABLE_LIVE" in evidence["evidence"]["deferred_facets"]
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
        finally:
            await pool.close()

    asyncio.run(_run())
