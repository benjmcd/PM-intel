from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "qualification" / "dq4_live_manifest.yaml"
SOURCE_CHANNEL = "dq4_offline_invariant_v1"


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_dq4_invariant_logic_fires_structural_red_controls() -> None:
    from pmfi.qualification.dq4_live import evaluate_dq4_pass_invariants

    good = {
        "coverage": {"counts": {"unaccounted": 0}},
        "duplicate_canonical_facts": 0,
        "dead_letter_rate": 0.0,
        "dead_letter_rate_threshold": 0.05,
        "per_venue_counts": {"kalshi": 1, "polymarket": 2},
        "required_venues": ["kalshi", "polymarket"],
        "min_per_venue": 1,
        "operational_health": {"status": "OK", "intake_allowed": True},
        "status_map": {
            "kalshi": {"circuit_open": False},
            "polymarket": {"circuit_open": False},
        },
        "no_secrets_in_fixtures_logs_or_evidence": True,
    }

    assert all(evaluate_dq4_pass_invariants(good).values())

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
def test_dq4_seeded_window_measurements_are_scoped_and_cleaned() -> None:
    from pmfi.db import create_pool
    from pmfi.qualification.dq4_live import (
        cleanup_dq4_offline_rows,
        collect_dq4_window_measurements,
        evaluate_dq4_pass_invariants,
    )

    async def _run() -> None:
        pool = await create_pool(_dsn())
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
            assert "DUAL_VENUE" in evidence["evidence"]["actual_facets"]
            assert "LONG_HORIZON_SOAK" in evidence["evidence"]["deferred_facets"]
            assert "KNOWN_ANSWER_NOT_APPLICABLE_LIVE" in evidence["evidence"]["deferred_facets"]
            assert all(evidence["pass_invariants"].values()), evidence["pass_invariants"]
        finally:
            await pool.close()

    asyncio.run(_run())
