from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _make_decision(rule_version: str) -> object:
    from pmfi.domain import AlertDecision

    return AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version=rule_version,
        severity="medium",
        confidence="high",
        score=Decimal("0.75"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"test": "dedupe_window"},
        data_quality="verified",
    )


def _epoch_ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_repeat_within_suppression_window_is_deduped():
    import asyncpg
    from pmfi.db.repos.alerts import insert_alert

    decision = _make_decision(rule_version=f"test.within.{uuid.uuid4().hex[:8]}")
    ts1 = _epoch_ts(1_700_000_150.0)
    ts2 = _epoch_ts(1_700_000_250.0)
    inserted_ids: list[str] = []

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            before_insert = await conn.fetchval("SELECT now()")
            alert_id_1 = await insert_alert(
                conn,
                decision,
                event_ts=ts1,
                title="Dedupe within window test",
                summary="first firing",
                venue_code="polymarket",
                outcome_key="yes",
                suppression_window_seconds=300,
            )
            assert alert_id_1 is not None
            inserted_ids.append(alert_id_1)
            after_insert = await conn.fetchval("SELECT now()")
            row = await conn.fetchrow(
                "SELECT fired_at, evidence FROM alerts WHERE alert_id=$1::uuid",
                alert_id_1,
            )
            assert before_insert <= row["fired_at"] <= after_insert
            evidence = row["evidence"]
            if isinstance(evidence, str):
                evidence = json.loads(evidence)
            else:
                evidence = dict(evidence)
            assert evidence["suppression_event_ts"] == ts1.isoformat()

            alert_id_2 = await insert_alert(
                conn,
                decision,
                event_ts=ts2,
                title="Dedupe within window test",
                summary="second firing should collide",
                venue_code="polymarket",
                outcome_key="yes",
                suppression_window_seconds=300,
            )
            assert alert_id_2 is None
        finally:
            for alert_id in inserted_ids:
                await conn.execute("DELETE FROM alerts WHERE alert_id=$1::uuid", alert_id)
            await conn.close()

    asyncio.run(_run())


def test_repeat_after_suppression_window_both_persist():
    import asyncpg
    from pmfi.db.repos.alerts import insert_alert

    decision = _make_decision(rule_version=f"test.after.{uuid.uuid4().hex[:8]}")
    ts1 = _epoch_ts(1_700_000_150.0)
    ts2 = _epoch_ts(1_700_000_550.0)
    inserted_ids: list[str] = []

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            alert_id_1 = await insert_alert(
                conn,
                decision,
                event_ts=ts1,
                title="After-window test first",
                summary="first firing",
                venue_code="kalshi",
                outcome_key="no",
                suppression_window_seconds=300,
            )
            assert alert_id_1 is not None
            inserted_ids.append(alert_id_1)

            alert_id_2 = await insert_alert(
                conn,
                decision,
                event_ts=ts2,
                title="After-window test second",
                summary="second firing different bucket",
                venue_code="kalshi",
                outcome_key="no",
                suppression_window_seconds=300,
            )
            assert alert_id_2 is not None
            inserted_ids.append(alert_id_2)
            assert alert_id_1 != alert_id_2
        finally:
            for alert_id in inserted_ids:
                await conn.execute("DELETE FROM alerts WHERE alert_id=$1::uuid", alert_id)
            await conn.close()

    asyncio.run(_run())


def test_repeat_across_bucket_boundary_within_window_is_deduped():
    import asyncpg
    from pmfi.db.repos.alerts import insert_alert

    decision = _make_decision(rule_version=f"test.boundary.{uuid.uuid4().hex[:8]}")
    ts1 = _epoch_ts(1_700_000_098.0)
    ts2 = _epoch_ts(1_700_000_102.0)
    assert int(ts1.timestamp() // 300) != int(ts2.timestamp() // 300)
    inserted_ids: list[str] = []

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        try:
            alert_id_1 = await insert_alert(
                conn,
                decision,
                event_ts=ts1,
                title="Dedupe boundary test",
                summary="first firing",
                venue_code="polymarket",
                outcome_key="yes",
                suppression_window_seconds=300,
            )
            assert alert_id_1 is not None
            inserted_ids.append(alert_id_1)

            alert_id_2 = await insert_alert(
                conn,
                decision,
                event_ts=ts2,
                title="Dedupe boundary test",
                summary="second firing should collide despite bucket boundary",
                venue_code="polymarket",
                outcome_key="yes",
                suppression_window_seconds=300,
            )
            assert alert_id_2 is None
        finally:
            for alert_id in inserted_ids:
                await conn.execute("DELETE FROM alerts WHERE alert_id=$1::uuid", alert_id)
            await conn.close()

    asyncio.run(_run())
