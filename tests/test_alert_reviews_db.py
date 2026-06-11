"""DB-gated tests for the alert_reviews repo layer (false-positive feedback).

Gated on PMFI_DB_URL like the other *_db tests. Each test seeds a synthetic
market + alert, exercises the repo, and cleans up every row it created in
FK-safe order (alert_reviews cascades on alert delete).
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_VMID = "TEST-FP-REVIEW-001"


async def _make_pool():
    import asyncpg
    return await asyncpg.create_pool(
        os.environ["PMFI_DB_URL"], min_size=1, max_size=1,
        server_settings={"search_path": "pmfi,public"},
    )


async def _seed_alert(conn):
    from pmfi.db.repos.markets import upsert_market
    from pmfi.db.repos.alerts import insert_alert
    from pmfi.domain import AlertDecision

    market_id = await upsert_market(
        conn, venue_code="polymarket", venue_market_id=_VMID, title="FP review test market",
    )
    # Pre-clean any leftover alert for this market so insert is not hour-deduped.
    await conn.execute("DELETE FROM alerts WHERE market_id = $1", market_id)
    decision = AlertDecision(
        emit_alert=True, rule_id="large_trade_absolute_v1", rule_version="alert_rules.v1",
        severity="high", confidence="high", score=Decimal("0.9"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"venue_code": "polymarket"}, data_quality="verified",
    )
    alert_id = await insert_alert(
        conn, decision, title="fp-test", summary="fp-test",
        venue_code="polymarket", market_id=market_id, outcome_key="yes",
    )
    return market_id, alert_id


async def _cleanup(conn, market_id):
    await conn.execute("DELETE FROM alerts WHERE market_id = $1", market_id)  # cascades alert_reviews
    await conn.execute("DELETE FROM markets WHERE market_id = $1", market_id)


def test_record_and_query_review():
    from pmfi.db.repos.alert_reviews import (
        record_review, list_reviews, false_positive_rate_by_rule,
    )

    async def _run():
        pool = await _make_pool()
        try:
            async with pool.acquire() as conn:
                market_id, alert_id = await _seed_alert(conn)
                try:
                    assert alert_id is not None
                    review_id = await record_review(
                        conn, alert_id=alert_id, label="false_positive",
                        false_positive_category="market_making", notes="test", reviewed_by="tester",
                    )
                    assert review_id
                    rows = await list_reviews(conn, limit=20, label="false_positive")
                    assert any(r["alert_id"] == alert_id for r in rows)
                    rates = await false_positive_rate_by_rule(conn)
                    hit = [r for r in rates if r["rule_key"] == "large_trade_absolute_v1"]
                    assert hit and hit[0]["false_positive_count"] >= 1
                finally:
                    await _cleanup(conn, market_id)
        finally:
            await pool.close()

    asyncio.run(_run())


def test_record_review_rejects_bad_label():
    from pmfi.db.repos.alert_reviews import record_review

    async def _run():
        pool = await _make_pool()
        try:
            async with pool.acquire() as conn:
                market_id, alert_id = await _seed_alert(conn)
                try:
                    with pytest.raises(ValueError):
                        await record_review(conn, alert_id=alert_id, label="bogus")
                finally:
                    await _cleanup(conn, market_id)
        finally:
            await pool.close()

    asyncio.run(_run())


def test_record_review_rejects_missing_alert():
    from pmfi.db.repos.alert_reviews import record_review

    async def _run():
        pool = await _make_pool()
        try:
            async with pool.acquire() as conn:
                with pytest.raises(ValueError):
                    await record_review(
                        conn, alert_id="00000000-0000-0000-0000-000000000000",
                        label="false_positive",
                    )
        finally:
            await pool.close()

    asyncio.run(_run())
