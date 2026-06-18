"""DB-gated contract test for the dashboard alerts query layer and explain repo helper.

Skips without PMFI_DB_URL so the default offline verify stays green. Seeds a
synthetic market + one alert under an ISOLATED synthetic venue_market_id (no
collision with the operator's real data), asserts recent_alerts returns it with
the right shape, then SELF-CLEANS all seeded rows FK-safely.

Run with DB:
  $env:PMFI_DB_URL='postgresql://pmfi:pmfi_local_password_change_me@localhost:5433/pmfi'
  python -m pytest tests/test_dashboard_alerts_db.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_recent_alerts_shape_and_cleanup():
    """Seed a synthetic alert, assert recent_alerts returns it, then self-clean."""
    import asyncpg
    from pmfi.dashboard.queries import recent_alerts

    # Isolated synthetic IDs — cannot collide with real data
    synthetic_venue_market_id = f"0xALERTTEST{uuid4().hex[:14]}"
    synthetic_rule_key = f"test_rule_{uuid4().hex[:8]}"
    synthetic_dedupe = f"dedupe_test_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_id = None
        try:
            # Insert a synthetic market
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Alert Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )

            # Insert a synthetic alert referencing that market
            evidence = {
                "capital_at_risk_usd": 450.0,
                "this_trade_usd": 450.0,
                "p99_threshold_usd": 400.0,
                "dominant_side": "buy",
                "trade_count": 3,
                "baseline_state": "baseline_sparse",
                "baseline_trades": 8,
                "spike_multiplier": 5.2,
                "min_spike_multiplier": 5.0,
            }
            alert_id = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'high', 'high', 0.92,
                           'Alert Test Market', 'Synthetic test alert', $4::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe,
                synthetic_rule_key,
                market_id,
                json.dumps(evidence),
            )

            # Query recent_alerts — filter to our synthetic alert by rule_key
            alerts = await recent_alerts(conn, limit=200)
            mine = [a for a in alerts if a["alert_id"] == alert_id]
            assert len(mine) == 1, f"Expected 1 synthetic alert, found {len(mine)}; total={len(alerts)}"
            a = mine[0]

            # Shape assertions
            assert a["rule_key"] == synthetic_rule_key
            assert a["rule_version"] == "1.0.0"
            assert a["severity"] == "high"
            assert a["confidence"] == "high"
            assert abs(float(a["score"]) - 0.92) < 0.001, a["score"]
            assert a["outcome_key"] == "yes"
            assert a["data_quality"] == "ok"
            assert a["market_title"] == "Alert Test Market", a["market_title"]
            assert a["venue_market_id"] == synthetic_venue_market_id
            assert a["fired_at"] is not None
            # Evidence summary must mention capital_at_risk_usd
            assert "capital_at_risk_usd" in a["evidence_summary"], a["evidence_summary"]
            assert "low_notional" in a["triage_flags"]
            assert "thin_baseline" in a["triage_flags"]
            assert "near_threshold" in a["triage_flags"]
            assert a["is_reviewed"] is False
            assert a["review_label"] is None
            assert a["review_category"] is None
            assert a["review_notes"] is None
            assert a["reviewed_at"] is None
            assert a["reviewed_by"] is None

        finally:
            # Self-clean FK-safely: alert first, then market
            if alert_id:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_recent_alerts_includes_latest_review_state():
    """recent_alerts returns the newest review row per alert."""
    import asyncpg
    from pmfi.dashboard.queries import recent_alerts

    synthetic_venue_market_id = f"0xREVIEWTEST{uuid4().hex[:12]}"
    synthetic_rule_key = f"review_rule_{uuid4().hex[:8]}"
    synthetic_dedupe = f"dedupe_review_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_id = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Review Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            alert_id = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'medium', 'medium', 0.67,
                           'Review Test Market', 'Synthetic review test alert', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe,
                synthetic_rule_key,
                market_id,
            )
            await conn.execute(
                """INSERT INTO alert_reviews
                   (alert_id, label, false_positive_category, notes, reviewed_by, reviewed_at)
                   VALUES ($1::uuid, 'fp', 'stale_baseline', 'older note', 'older-op',
                           now() - interval '2 hours')""",
                alert_id,
            )
            await conn.execute(
                """INSERT INTO alert_reviews
                   (alert_id, label, false_positive_category, notes, reviewed_by, reviewed_at)
                   VALUES ($1::uuid, 'tp', NULL, 'newer note', 'newer-op',
                           now() - interval '1 hour')""",
                alert_id,
            )

            alerts = await recent_alerts(conn, limit=200)
            mine = [a for a in alerts if a["alert_id"] == alert_id]
            assert len(mine) == 1, f"Expected 1 synthetic alert, found {len(mine)}; total={len(alerts)}"
            a = mine[0]
            assert a["is_reviewed"] is True
            assert a["review_label"] == "tp"
            assert a["review_category"] is None
            assert a["review_notes"] == "newer note"
            assert a["reviewed_by"] == "newer-op"
            assert a["reviewed_at"] is not None

        finally:
            if alert_id:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_recent_alerts_filters_review_state():
    """reviewed/unreviewed queues should reflect latest review rows and no post-filter clipping."""
    import asyncpg
    from pmfi.dashboard.queries import recent_alerts

    synthetic_venue_market_id = f"0xREVIEWSTATE{uuid4().hex[:12]}"
    market_rule_unreviewed = f"state_rule_u_{uuid4().hex[:8]}"
    market_rule_reviewed = f"state_rule_r_{uuid4().hex[:8]}"
    synthetic_dedupe_u = f"dedupe_reviewed_state_u_{uuid4().hex}"
    synthetic_dedupe_r = f"dedupe_reviewed_state_r_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_u = None
        alert_r = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Review State Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            alert_u = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'high', 'high', 0.75,
                           'review-state-unreviewed', 'No review row', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe_u,
                market_rule_unreviewed,
                market_id,
            )
            alert_r = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'medium', 'medium', 0.63,
                           'review-state-reviewed', 'Has review row', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe_r,
                market_rule_reviewed,
                market_id,
            )
            await conn.execute(
                """INSERT INTO alert_reviews (alert_id, label, notes)
                   VALUES ($1::uuid, 'tp', 'latest review')""",
                alert_r,
            )

            reviewed = await recent_alerts(conn, limit=200, review_state="reviewed")
            reviewed_ids = {r["alert_id"] for r in reviewed}
            unreviewed = await recent_alerts(conn, limit=200, review_state="unreviewed")
            unreviewed_ids = {r["alert_id"] for r in unreviewed}

            assert alert_r in reviewed_ids
            assert alert_u not in reviewed_ids
            assert alert_u in unreviewed_ids
            assert alert_r not in unreviewed_ids

        finally:
            if alert_r:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_r)
            if alert_u:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_u)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_recent_alerts_filters_by_latest_review_label():
    """review_label should match only alerts whose latest review has that label."""
    import asyncpg
    from pmfi.dashboard.queries import recent_alerts

    synthetic_venue_market_id = f"0xREVIEWLABEL{uuid4().hex[:12]}"
    synthetic_rule = f"review_label_rule_{uuid4().hex[:8]}"
    synthetic_dedupe = f"dedupe_review_label_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_id = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Review Label Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            alert_id = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'medium', 'medium', 0.58,
                           'review-label', 'TP should win', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe,
                synthetic_rule,
                market_id,
            )
            await conn.execute(
                """INSERT INTO alert_reviews (alert_id, label, reviewed_at)
                   VALUES ($1::uuid, 'fp', now() - interval '2 hours')""",
                alert_id,
            )
            await conn.execute(
                """INSERT INTO alert_reviews (alert_id, label, reviewed_at)
                   VALUES ($1::uuid, 'tp', now() - interval '1 hour')""",
                alert_id,
            )

            all_labelled = await recent_alerts(conn, limit=200, review_label="tp")
            tp_rows = [row for row in all_labelled if row["alert_id"] == alert_id]
            assert len(tp_rows) == 1
            assert tp_rows[0]["review_label"] == "tp"
            assert tp_rows[0]["review_category"] is None

            noise = await recent_alerts(conn, limit=200, review_label="noise")
            assert all(r["alert_id"] != alert_id for r in noise)

        finally:
            if alert_id:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_recent_alerts_filters_by_triage_flags_and():
    """triage_flag filters must use AND semantics and filter in deterministic flag space."""
    import asyncpg
    from pmfi.dashboard.queries import recent_alerts

    synthetic_venue_market_id = f"0xTRIAGEFLAGS{uuid4().hex[:12]}"
    synthetic_rule = f"triage_flags_rule_{uuid4().hex[:8]}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_ids = []
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Triage Flags Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            evidence_low_and_thin = {
                "capital_at_risk_usd": 10.0,
                "baseline_state": "baseline_sparse",
                "baseline_trades": 100,
                "spike_multiplier": 1.2,
                "min_spike_multiplier": 1.0,
            }
            evidence_low_only = {
                "capital_at_risk_usd": 10.0,
                "baseline_sample_size": 20,
                "baseline_trades": 100,
                "spike_multiplier": 2.0,
                "min_spike_multiplier": 1.0,
            }
            evidence_none = {
                "capital_at_risk_usd": 9000.0,
                "baseline_state": "baseline_dense",
                "spike_multiplier": 5.0,
                "min_spike_multiplier": 1.0,
            }
            for idx, evidence in enumerate([evidence_low_and_thin, evidence_low_only, evidence_none]):
                alert_id = await conn.fetchval(
                    """INSERT INTO alerts
                       (dedupe_key, rule_key, rule_version, venue_code, market_id,
                        outcome_key, severity, confidence, score, title, summary, evidence, data_quality, raw_event_id, trade_id)
                       VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                               'yes', 'low', 'low', 0.50,
                               $4, $5, $6::jsonb, 'ok', $7, $8::uuid)
                       RETURNING alert_id::text""",
                    f"dedupe_flags_{idx}_{uuid4().hex}",
                    f"{synthetic_rule}_{idx}",
                    market_id,
                    f"Triage flags alert {idx}",
                    f"flags test {idx}",
                    json.dumps(evidence),
                    900000 + idx,
                    str(uuid4()),
                )
                alert_ids.append(alert_id)

            and_filtered = await recent_alerts(
                conn,
                limit=50,
                triage_flags_filter=["low_notional", "thin_baseline"],
            )
            and_ids = {r["alert_id"] for r in and_filtered}
            assert alert_ids[0] in and_ids
            assert alert_ids[1] not in and_ids
            assert alert_ids[2] not in and_ids

            repeated_filtered = await recent_alerts(
                conn,
                limit=50,
                triage_flags_filter=["low_notional", "low_notional"],
            )
            repeat_ids = {r["alert_id"] for r in repeated_filtered}
            assert alert_ids[0] in repeat_ids

        finally:
            for alert_id in alert_ids:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_get_alert_by_id_found_and_not_found():
    """get_alert_by_id returns the right row and None for a missing UUID."""
    import asyncpg
    from pmfi.db.repos.alerts import get_alert_by_id

    synthetic_venue_market_id = f"0xEXPLAINTEST{uuid4().hex[:12]}"
    synthetic_rule_key = f"explain_rule_{uuid4().hex[:8]}"
    synthetic_dedupe = f"dedupe_explain_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_id = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Explain Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            alert_id = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '2.0.0', 'polymarket', $3,
                           'no', 'medium', 'medium', 0.75,
                           'Explain Test Market', 'Explain test summary', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe,
                synthetic_rule_key,
                market_id,
            )

            async with conn.transaction():
                row = await get_alert_by_id(conn, alert_id)

            assert row is not None, "Expected row to be found"
            assert row["alert_id"] == alert_id
            assert row["rule_key"] == synthetic_rule_key
            assert row["rule_version"] == "2.0.0"
            assert row["severity"] == "medium"
            assert row["market_title"] == "Explain Test Market"
            assert row["venue_market_id"] == synthetic_venue_market_id

            # Missing UUID returns None
            missing_uuid = "00000000-0000-0000-0000-000000000000"
            none_row = await get_alert_by_id(conn, missing_uuid)
            assert none_row is None, f"Expected None for missing UUID, got {none_row}"

        finally:
            if alert_id:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())


def test_insert_alert_review_appends_review_row_and_returns_metadata():
    """The shared review helper appends one row and returns inserted review metadata."""
    import asyncpg
    from pmfi.db.repos.alerts import insert_alert_review

    synthetic_venue_market_id = f"0xREVIEWINSERT{uuid4().hex[:11]}"
    synthetic_rule_key = f"review_insert_rule_{uuid4().hex[:8]}"
    synthetic_dedupe = f"dedupe_review_insert_{uuid4().hex}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        alert_id = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, 'Review Insert Test Market', 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at = now()
                   RETURNING market_id""",
                synthetic_venue_market_id,
            )
            alert_id = await conn.fetchval(
                """INSERT INTO alerts
                   (dedupe_key, rule_key, rule_version, venue_code, market_id,
                    outcome_key, severity, confidence, score, title, summary, evidence, data_quality)
                   VALUES ($1, $2, '1.0.0', 'polymarket', $3,
                           'yes', 'medium', 'medium', 0.61,
                           'review-insert', 'Has appended review', '{}'::jsonb, 'ok')
                   RETURNING alert_id::text""",
                synthetic_dedupe,
                synthetic_rule_key,
                market_id,
            )

            before_count = await conn.fetchval(
                "SELECT COUNT(*) FROM alert_reviews WHERE alert_id = $1::uuid",
                alert_id,
            )
            result = await insert_alert_review(
                conn,
                alert_id[:8],
                label="tp",
                category="confirmed_flow",
                notes="dashboard route test",
                reviewed_by="db-test",
            )
            after_count = await conn.fetchval(
                "SELECT COUNT(*) FROM alert_reviews WHERE alert_id = $1::uuid",
                alert_id,
            )

            assert int(before_count) == 0
            assert int(after_count) == 1
            assert result is not None
            assert result["alert_id"] == alert_id
            assert result["label"] == "tp"
            assert result["category"] == "confirmed_flow"
            assert result["notes"] == "dashboard route test"
            assert result["reviewed_by"] == "db-test"
            assert result["reviewed_at"] is not None

        finally:
            if alert_id:
                await conn.execute("DELETE FROM alerts WHERE alert_id = $1::uuid", alert_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)
            await conn.close()

    asyncio.run(_run())
