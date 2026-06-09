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
                "capital_at_risk_usd": 9500.0,
                "p99_threshold_usd": 5000.0,
                "dominant_side": "buy",
                "trade_count": 3,
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

        finally:
            # Self-clean FK-safely: alert first, then market
            if alert_id:
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
