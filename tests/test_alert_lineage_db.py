"""DB-gated alert lineage round-trip test (Target 6).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.
Inserts a synthetic alert with raw_event_id + trade_id, reads it back, verifies
columns exist and values survive the round-trip, then cleans up.
"""
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


_SYNTHETIC_DEDUPE = "test-lineage-dedupe-" + uuid.uuid4().hex[:12]


def test_insert_alert_lineage_round_trip():
    """insert_alert with raw_event_id + trade_id stores and retrieves both columns."""
    import asyncpg
    from pmfi.domain import AlertDecision
    from pmfi.db.repos.alerts import insert_alert

    decision = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="test.v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.75"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"test": "lineage"},
        data_quality="verified",
    )

    synthetic_raw_event_id = 999999999  # large int unlikely to collide
    synthetic_trade_id = str(uuid.uuid4())
    # market_id=None avoids the pre-existing alerts->markets FK; the lineage
    # columns (raw_event_id, trade_id) are what this test exercises.
    synthetic_market_id = None

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        inserted_alert_id = None
        try:
            alert_id = await insert_alert(
                conn,
                decision,
                event_ts=datetime.now(timezone.utc),
                title="Lineage test alert",
                summary="round-trip check",
                venue_code="polymarket",
                market_id=synthetic_market_id,
                outcome_key="yes",
                raw_event_id=synthetic_raw_event_id,
                trade_id=synthetic_trade_id,
            )
            assert alert_id is not None, "insert_alert returned None — dedupe collision or emit_alert=False"
            inserted_alert_id = alert_id

            # Read back and verify lineage columns
            row = await conn.fetchrow(
                "SELECT raw_event_id, trade_id::text FROM alerts WHERE alert_id = $1::uuid",
                alert_id,
            )
            assert row is not None, f"alert_id={alert_id} not found after insert"
            assert row["raw_event_id"] == synthetic_raw_event_id, (
                f"raw_event_id mismatch: got {row['raw_event_id']!r}, expected {synthetic_raw_event_id}"
            )
            assert row["trade_id"] == synthetic_trade_id, (
                f"trade_id mismatch: got {row['trade_id']!r}, expected {synthetic_trade_id}"
            )
        finally:
            # Clean up synthetic row
            if inserted_alert_id:
                await conn.execute(
                    "DELETE FROM alerts WHERE alert_id = $1::uuid", inserted_alert_id
                )
            await conn.close()

    asyncio.run(_run())


def test_insert_alert_lineage_nullable():
    """insert_alert without raw_event_id/trade_id still works (backward compat)."""
    import asyncpg
    from pmfi.domain import AlertDecision
    from pmfi.db.repos.alerts import insert_alert

    decision = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="test.v1",
        severity="low",
        confidence="low",
        score=Decimal("0.1"),
        reason_codes=(),
        evidence={},
        data_quality="unverified",
    )
    synthetic_market_id = None  # avoid alerts->markets FK

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        inserted_alert_id = None
        try:
            alert_id = await insert_alert(
                conn,
                decision,
                event_ts=datetime.now(timezone.utc),
                title="Lineage null test alert",
                summary="null lineage check",
                venue_code="kalshi",
                market_id=synthetic_market_id,
                outcome_key="yes",
                # raw_event_id and trade_id intentionally omitted
            )
            assert alert_id is not None, "insert_alert returned None without lineage params"
            inserted_alert_id = alert_id

            row = await conn.fetchrow(
                "SELECT raw_event_id, trade_id FROM alerts WHERE alert_id = $1::uuid",
                alert_id,
            )
            assert row is not None
            assert row["raw_event_id"] is None, f"Expected NULL raw_event_id, got {row['raw_event_id']}"
            assert row["trade_id"] is None, f"Expected NULL trade_id, got {row['trade_id']}"
        finally:
            if inserted_alert_id:
                await conn.execute(
                    "DELETE FROM alerts WHERE alert_id = $1::uuid", inserted_alert_id
                )
            await conn.close()

    asyncio.run(_run())


def test_alert_lineage_integrity_detects_synthetic_orphan_reference():
    """Lineage integrity check reports dangling informational alert references."""
    import asyncpg
    from pmfi.domain import AlertDecision
    from pmfi.db.repos.alerts import get_alert_lineage_integrity, insert_alert

    decision = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="test.v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.75"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"test": "lineage-orphan"},
        data_quality="verified",
    )

    synthetic_raw_event_id = 888888888
    synthetic_trade_id = str(uuid.uuid4())
    since = datetime.now(timezone.utc)

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        inserted_alert_id = None
        try:
            alert_id = await insert_alert(
                conn,
                decision,
                event_ts=datetime.now(timezone.utc),
                title="Lineage orphan test alert",
                summary="orphan check",
                venue_code="polymarket",
                market_id=None,
                outcome_key="yes",
                raw_event_id=synthetic_raw_event_id,
                trade_id=synthetic_trade_id,
            )
            assert alert_id is not None
            inserted_alert_id = alert_id

            check = await get_alert_lineage_integrity(conn, since=since, limit=100)
            orphan_rows = [
                row for row in check["rows"]
                if row["alert_id"] == inserted_alert_id
            ]
            assert orphan_rows, f"synthetic alert {inserted_alert_id} missing from lineage check"
            row = orphan_rows[0]
            assert row["raw_event_missing"] is True
            assert row["trade_missing"] is True
            assert check["totals"]["alerts_with_orphans"] >= 1
        finally:
            if inserted_alert_id:
                await conn.execute(
                    "DELETE FROM alerts WHERE alert_id = $1::uuid",
                    inserted_alert_id,
                )
            await conn.close()

    asyncio.run(_run())
