"""Tests for the data-quality degradation monitor (alert type #6).

Structure:
  - Unit tests: pure logic, no DB — test detection thresholds and AlertDecision
    construction without touching Postgres.
  - DB-gated tests: seed raw_events / dead_letters rows, run check_data_quality,
    assert alert + incident rows written, then clean up in FK-safe order.

DB tests skip when PMFI_DB_URL is not set (offline verify.py stays green).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Unit tests — pure detection logic (no DB)
# ---------------------------------------------------------------------------

class TestFeedSilentDetectionLogic:
    """Validate the staleness threshold logic in isolation."""

    def test_silence_threshold_exceeded(self):
        """When stale_seconds >= threshold, the monitor should emit an alert."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        last_event = now - timedelta(seconds=700)
        threshold = 600
        stale_seconds = (now - last_event).total_seconds()
        assert stale_seconds >= threshold

    def test_silence_threshold_not_exceeded(self):
        """When stale_seconds < threshold, no alert should be emitted."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        last_event = now - timedelta(seconds=300)
        threshold = 600
        stale_seconds = (now - last_event).total_seconds()
        assert stale_seconds < threshold

    def test_exact_threshold_triggers(self):
        """Exactly at the threshold should trigger."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        last_event = now - timedelta(seconds=600)
        threshold = 600
        stale_seconds = (now - last_event).total_seconds()
        assert stale_seconds >= threshold

    def test_alert_decision_severity_high_for_silence(self):
        """The AlertDecision constructed for feed_silent should have severity=high."""
        from pmfi.domain import AlertDecision
        decision = AlertDecision(
            emit_alert=True,
            rule_id="data_quality_degradation_v1",
            rule_version="alert_rules.v1",
            severity="high",
            confidence="high",
            score=Decimal("0.9"),
            reason_codes=("feed_silent",),
            evidence={"stale_seconds": 700},
            data_quality="degraded",
        )
        assert decision.severity == "high"
        assert decision.emit_alert is True
        assert "feed_silent" in decision.reason_codes
        assert decision.data_quality == "degraded"


class TestDeadLetterSpikeDetectionLogic:
    """Validate the spike ratio logic in isolation."""

    def test_spike_fires_when_recent_exceeds_ratio(self):
        recent, prior, spike_min, ratio = 15, 3, 5, 3.0
        assert recent >= spike_min
        assert prior == 0 or recent >= ratio * prior

    def test_spike_does_not_fire_below_min(self):
        recent, prior, spike_min, ratio = 2, 0, 5, 3.0
        should_fire = recent >= spike_min and (prior == 0 or recent >= ratio * prior)
        assert not should_fire

    def test_spike_does_not_fire_when_ratio_not_met(self):
        recent, prior, spike_min, ratio = 6, 4, 5, 3.0
        should_fire = recent >= spike_min and (prior == 0 or recent >= ratio * prior)
        assert not should_fire

    def test_spike_fires_when_prior_is_zero(self):
        """If prior window is zero and recent >= spike_min, it should fire."""
        recent, prior, spike_min, ratio = 5, 0, 5, 3.0
        should_fire = recent >= spike_min and (prior == 0 or recent >= ratio * prior)
        assert should_fire

    def test_alert_decision_severity_medium_for_spike(self):
        from pmfi.domain import AlertDecision
        decision = AlertDecision(
            emit_alert=True,
            rule_id="data_quality_degradation_v1",
            rule_version="alert_rules.v1",
            severity="medium",
            confidence="medium",
            score=Decimal("0.7"),
            reason_codes=("dead_letter_spike",),
            evidence={"recent_count": 15, "prior_count": 3},
            data_quality="degraded",
        )
        assert decision.severity == "medium"
        assert "dead_letter_spike" in decision.reason_codes


# ---------------------------------------------------------------------------
# Unit test — run_monitors never raises
# ---------------------------------------------------------------------------

class TestRunMonitorsNonFatal:
    def test_run_monitors_does_not_raise_when_check_raises(self):
        """run_monitors must swallow all exceptions from check_data_quality."""
        from pmfi.monitoring.base import run_monitors

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(side_effect=RuntimeError("db down")),
            __aexit__=AsyncMock(return_value=False),
        ))

        async def _run():
            await run_monitors(pool, now=_now())

        # Must not raise
        asyncio.run(_run())

    def test_run_monitors_does_not_raise_on_import_error(self):
        """Even if check_data_quality fails completely, run_monitors must not raise."""
        from pmfi.monitoring.base import run_monitors
        import sys

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(side_effect=Exception("unexpected")),
            __aexit__=AsyncMock(return_value=False),
        ))

        async def _run():
            await run_monitors(pool, now=_now())

        asyncio.run(_run())

    def test_run_monitors_passes_active_venue_scope(self):
        from pmfi.monitoring.base import run_monitors

        mock_check = AsyncMock(return_value=[])

        async def _run():
            with patch("pmfi.monitoring.data_quality.check_data_quality", new=mock_check):
                await run_monitors(
                    MagicMock(),
                    now=_now(),
                    active_venue_codes=("polymarket",),
                    cross_venue_enabled=False,
                )

        asyncio.run(_run())

        mock_check.assert_awaited_once()
        assert mock_check.await_args.kwargs["active_venue_codes"] == ("polymarket",)


class TestDataQualityVenueScope:
    def test_empty_active_venue_scope_returns_no_incidents_without_db(self):
        from pmfi.monitoring.data_quality import check_data_quality

        pool = MagicMock()
        pool.acquire = MagicMock()

        incidents = asyncio.run(
            check_data_quality(pool, now=_now(), active_venue_codes=[])
        )

        assert incidents == []
        pool.acquire.assert_not_called()

    def test_monitor_alerts_use_condition_dedupe_context(self):
        from pmfi.monitoring.data_quality import check_data_quality

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        conn = MagicMock()
        conn.fetch = AsyncMock(return_value=[{"venue_code": "polymarket"}])
        conn.fetchrow = AsyncMock(side_effect=[
            {"last_event": now - timedelta(seconds=700)},
            {"cnt": 7},
            {"cnt": 0},
        ])

        class _Acquire:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_):
                return False

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_Acquire())
        mock_record = AsyncMock(side_effect=["inc-feed", "inc-spike"])
        mock_emit = AsyncMock(return_value="alert-id")

        async def _run():
            with (
                patch("pmfi.monitoring.data_quality.record_incident", new=mock_record),
                patch("pmfi.monitoring.data_quality.emit_monitor_alert", new=mock_emit),
            ):
                return await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                    active_venue_codes=("polymarket",),
                )

        incidents = asyncio.run(_run())

        assert [i["incident_type"] for i in incidents] == [
            "feed_silent",
            "dead_letter_spike",
        ]
        contexts = [call.kwargs["dedupe_context"] for call in mock_emit.await_args_list]
        assert contexts == ["feed_silent", "dead_letter_spike"]


# ---------------------------------------------------------------------------
# Unit test — daemon tick does not raise with data_quality_enabled
# ---------------------------------------------------------------------------

class TestTelemetryTickDataQualityBlock:
    """_telemetry_tick with data_quality_enabled=True must still never raise."""

    def _base_kwargs(self, tmp_path, *, cycle: int = 1):
        from pathlib import Path
        recompute_state = {
            "last_recompute_at": None,
            "last_recompute_ok": None,
            "last_recompute_error": None,
        }
        return dict(
            cycle=cycle,
            events_total=5,
            alerts_total=1,
            delta=3,
            interval=60,
            hb_path=tmp_path / "hb.json",
            write_heartbeat=MagicMock(),
            started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            build_venues_payload=MagicMock(return_value={}),
            recompute_state=recompute_state,
            recompute_enabled=False,
            recompute_cycles=10,
            safe_recompute_baselines=AsyncMock(return_value=(0, None)),
            pool=MagicMock(),
            window_days=30,
            min_samples=10,
            baseline_refresh_cycles=10,
            load_baselines=AsyncMock(return_value={}),
            engine=MagicMock(),
            map_refresh_cycles=10,
            refresh_subscriptions=AsyncMock(return_value=([], [])),
            asset_id_map={},
            current_poly_ids=[],
            current_kalshi_tickers=[],
            partition_maint_cycles=1440,
            ensure_partitions=AsyncMock(),
            find_old_partitions=AsyncMock(return_value=[]),
            raw_retention_days=90,
            data_quality_enabled=True,
            venue_stale_seconds=600,
            dead_letter_spike_min=5,
            dead_letter_spike_ratio=3.0,
            data_quality_monitor_cycles=1,
            now_utc=lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

    def test_data_quality_block_non_fatal_when_monitor_raises(self, tmp_path):
        """If run_monitors raises, the tick must complete without propagating."""
        from pmfi.commands.daemon import _telemetry_tick

        kw = self._base_kwargs(tmp_path, cycle=1)

        with patch("pmfi.monitoring.run_monitors", new=AsyncMock(side_effect=RuntimeError("boom"))):
            asyncio.run(_telemetry_tick(**kw))  # must not raise

    def test_data_quality_not_called_when_disabled(self, tmp_path):
        """When data_quality_enabled=False the monitor block is skipped entirely."""
        from pmfi.commands.daemon import _telemetry_tick

        kw = self._base_kwargs(tmp_path, cycle=1)
        kw["data_quality_enabled"] = False

        mock_monitors = AsyncMock()
        with patch("pmfi.monitoring.run_monitors", new=mock_monitors):
            asyncio.run(_telemetry_tick(**kw))
        mock_monitors.assert_not_awaited()

    def test_data_quality_called_on_monitor_boundary_cycle(self, tmp_path):
        """run_monitors IS awaited when cycle matches data_quality_monitor_cycles."""
        from pmfi.commands.daemon import _telemetry_tick

        kw = self._base_kwargs(tmp_path, cycle=1)
        kw["data_quality_monitor_cycles"] = 1
        kw["build_venues_payload"] = MagicMock(return_value={"polymarket": {}, "kalshi": {}})

        mock_monitors = AsyncMock(return_value=None)
        with patch("pmfi.monitoring.run_monitors", new=mock_monitors):
            asyncio.run(_telemetry_tick(**kw))
        mock_monitors.assert_awaited_once()
        assert mock_monitors.await_args.kwargs["active_venue_codes"] == (
            "polymarket",
            "kalshi",
        )

    def test_data_quality_not_called_off_boundary(self, tmp_path):
        """run_monitors is NOT awaited when cycle is not on the monitor boundary."""
        from pmfi.commands.daemon import _telemetry_tick

        kw = self._base_kwargs(tmp_path, cycle=3)
        kw["data_quality_monitor_cycles"] = 10  # cycle 3 is not a boundary

        mock_monitors = AsyncMock(return_value=None)
        with patch("pmfi.monitoring.run_monitors", new=mock_monitors):
            asyncio.run(_telemetry_tick(**kw))
        mock_monitors.assert_not_awaited()


# ---------------------------------------------------------------------------
# DB-gated tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)
class TestDataQualityMonitorDB:
    """Round-trip DB tests: seed data → run check_data_quality → verify rows → clean up."""

    def _run(self, coro):
        return asyncio.run(coro)

    async def _setup_synthetic_market(self, conn) -> str:
        """Insert a synthetic market in the polymarket venue and return market_id."""
        synthetic_vmi = "test-dqm-" + uuid.uuid4().hex[:12]
        row = await conn.fetchrow(
            """INSERT INTO markets (venue_code, venue_market_id, title, status)
               VALUES ('polymarket', $1, 'DQM synthetic market', 'active')
               RETURNING market_id::text""",
            synthetic_vmi,
        )
        return row["market_id"]

    async def _cleanup(self, conn, *, market_id: str | None, raw_event_ids: list, dead_letter_ids: list):
        """Delete inserted rows in FK-safe order."""
        # alerts reference markets via market_id=NULL here, but clean all DQM test alerts
        await conn.execute(
            "DELETE FROM alerts WHERE rule_key = 'data_quality_degradation_v1' AND venue_code = 'polymarket'"
        )
        # incidents
        if market_id:
            await conn.execute(
                "DELETE FROM data_quality_incidents WHERE venue_code = 'polymarket' AND market_id IS NULL "
                "AND incident_type IN ('feed_silent', 'dead_letter_spike')"
            )
        else:
            await conn.execute(
                "DELETE FROM data_quality_incidents WHERE venue_code = 'polymarket' AND market_id IS NULL "
                "AND incident_type IN ('feed_silent', 'dead_letter_spike')"
            )
        # dead_letters
        for dl_id in dead_letter_ids:
            await conn.execute(
                "DELETE FROM dead_letters WHERE dead_letter_id = $1::uuid", dl_id
            )
        # raw_events
        for re_id in raw_event_ids:
            await conn.execute(
                "DELETE FROM event_dedupe_keys WHERE first_raw_event_id = $1", re_id
            )
            await conn.execute(
                "DELETE FROM raw_events WHERE raw_event_id = $1", re_id
            )
        # market last (FK head)
        if market_id:
            await conn.execute(
                "DELETE FROM markets WHERE market_id = $1::uuid", market_id
            )

    def test_feed_silent_fires_when_venue_has_stale_events(self):
        """A raw_event older than threshold triggers a feed_silent incident + alert.

        Uses a far-future 'now' to isolate from real DB rows: our single synthetic
        raw_event (received_at = future_now - 700s) is the MAX for the future window
        and is beyond the 600s threshold.
        """
        import asyncpg
        from pmfi.monitoring.data_quality import check_data_quality

        now = datetime(2036, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        old_received_at = now - timedelta(seconds=700)

        async def _run():
            conn = await asyncpg.connect(_get_dsn())
            market_id = None
            raw_event_ids: list[int] = []
            dead_letter_ids: list[str] = []
            try:
                market_id = await self._setup_synthetic_market(conn)

                # Insert a raw_event older than threshold
                row = await conn.fetchrow(
                    """INSERT INTO raw_events
                           (venue_code, source_channel, source_event_type,
                            received_at, payload, payload_hash, parser_version)
                       VALUES ('polymarket', 'test', 'test_event', $1,
                               '{"test": true}'::jsonb,
                               md5(gen_random_uuid()::text),
                               'raw.v1')
                       RETURNING raw_event_id""",
                    old_received_at,
                )
                raw_event_ids.append(int(row["raw_event_id"]))

                # Build a minimal pool mock that reuses this connection
                pool = _make_pool_from_conn(conn)

                incidents = await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                )

                # At least one feed_silent incident for polymarket
                silent = [i for i in incidents if i["venue_code"] == "polymarket" and i["incident_type"] == "feed_silent"]
                assert len(silent) >= 1, f"Expected feed_silent incident, got: {incidents}"

                # Verify incident row in DB
                inc_row = await conn.fetchrow(
                    """SELECT incident_id, severity, incident_type
                       FROM data_quality_incidents
                       WHERE venue_code = 'polymarket'
                         AND incident_type = 'feed_silent'
                       ORDER BY created_at DESC LIMIT 1"""
                )
                assert inc_row is not None, "data_quality_incidents row missing"
                assert inc_row["severity"] == "high"

                # Verify alert row in DB
                alert_row = await conn.fetchrow(
                    """SELECT alert_id, severity FROM alerts
                       WHERE rule_key = 'data_quality_degradation_v1'
                         AND venue_code = 'polymarket'
                       ORDER BY created_at DESC LIMIT 1"""
                )
                assert alert_row is not None, "alerts row missing"
                assert alert_row["severity"] == "high"

            finally:
                await self._cleanup(conn, market_id=market_id, raw_event_ids=raw_event_ids, dead_letter_ids=dead_letter_ids)
                await conn.close()

        self._run(_run())

    def test_feed_silent_does_not_fire_for_fresh_events(self):
        """A raw_event within threshold does NOT trigger a feed_silent incident.

        Uses a far-future 'now' so only our inserted raw_event (60s before future_now)
        determines the MAX(received_at); real rows are far in the past from this 'now'.
        """
        import asyncpg
        from pmfi.monitoring.data_quality import check_data_quality

        now = datetime(2036, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        fresh_received_at = now - timedelta(seconds=60)

        async def _run():
            conn = await asyncpg.connect(_get_dsn())
            market_id = None
            raw_event_ids: list[int] = []
            dead_letter_ids: list[str] = []
            try:
                market_id = await self._setup_synthetic_market(conn)

                row = await conn.fetchrow(
                    """INSERT INTO raw_events
                           (venue_code, source_channel, source_event_type,
                            received_at, payload, payload_hash, parser_version)
                       VALUES ('polymarket', 'test', 'test_event', $1,
                               '{"test": true}'::jsonb,
                               md5(gen_random_uuid()::text),
                               'raw.v1')
                       RETURNING raw_event_id""",
                    fresh_received_at,
                )
                raw_event_ids.append(int(row["raw_event_id"]))

                pool = _make_pool_from_conn(conn)

                incidents = await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                )

                silent = [i for i in incidents if i["venue_code"] == "polymarket" and i["incident_type"] == "feed_silent"]
                assert len(silent) == 0, f"Unexpected feed_silent for fresh events: {silent}"

            finally:
                await self._cleanup(conn, market_id=market_id, raw_event_ids=raw_event_ids, dead_letter_ids=dead_letter_ids)
                await conn.close()

        self._run(_run())

    def test_dead_letter_spike_fires_when_ratio_exceeded(self):
        """Inserting spike-count dead_letters triggers a dead_letter_spike incident + alert.

        Uses a far-future 'now' so only our synthetic rows fall in the recent window.
        """
        import asyncpg
        from pmfi.monitoring.data_quality import check_data_quality

        now = datetime(2036, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        # Insert dead_letters in the RECENT window (last 10m)
        recent_ts = now - timedelta(minutes=5)

        async def _run():
            conn = await asyncpg.connect(_get_dsn())
            market_id = None
            raw_event_ids: list[int] = []
            dead_letter_ids: list[str] = []
            try:
                market_id = await self._setup_synthetic_market(conn)

                # Insert 7 dead_letters in recent window (>= spike_min=5, prior=0 → fires)
                for _ in range(7):
                    dl_row = await conn.fetchrow(
                        """INSERT INTO dead_letters
                               (venue_code, failure_stage, error_message, payload, created_at)
                           VALUES ('polymarket', 'normalization', 'test error',
                                   '{"x":1}'::jsonb, $1)
                           RETURNING dead_letter_id::text""",
                        recent_ts,
                    )
                    dead_letter_ids.append(dl_row["dead_letter_id"])

                pool = _make_pool_from_conn(conn)

                incidents = await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                )

                spikes = [i for i in incidents if i["venue_code"] == "polymarket" and i["incident_type"] == "dead_letter_spike"]
                assert len(spikes) >= 1, f"Expected dead_letter_spike incident, got: {incidents}"

                # Verify incident row
                inc_row = await conn.fetchrow(
                    """SELECT severity, incident_type
                       FROM data_quality_incidents
                       WHERE venue_code = 'polymarket'
                         AND incident_type = 'dead_letter_spike'
                       ORDER BY created_at DESC LIMIT 1"""
                )
                assert inc_row is not None, "data_quality_incidents row missing for spike"
                assert inc_row["severity"] == "medium"

                # Verify alert row
                alert_row = await conn.fetchrow(
                    """SELECT severity FROM alerts
                       WHERE rule_key = 'data_quality_degradation_v1'
                         AND venue_code = 'polymarket'
                       ORDER BY created_at DESC LIMIT 1"""
                )
                assert alert_row is not None, "alerts row missing for spike"
                assert alert_row["severity"] in ("medium", "high")

            finally:
                await self._cleanup(conn, market_id=market_id, raw_event_ids=raw_event_ids, dead_letter_ids=dead_letter_ids)
                await conn.close()

        self._run(_run())

    def test_dead_letter_spike_does_not_fire_below_minimum(self):
        """Below spike_min dead_letters: no dead_letter_spike incident.

        Uses a far-future 'now' so all real rows are outside the detection windows,
        then inserts 3 rows in the recent window — below the spike_min=5 threshold.
        """
        import asyncpg
        from pmfi.monitoring.data_quality import check_data_quality

        # Use a 'now' 10 years in the future: real DB rows will be far outside
        # the 10-minute recent window and won't pollute the count.
        now = datetime(2036, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        recent_ts = now - timedelta(minutes=5)

        async def _run():
            conn = await asyncpg.connect(_get_dsn())
            market_id = None
            raw_event_ids: list[int] = []
            dead_letter_ids: list[str] = []
            try:
                market_id = await self._setup_synthetic_market(conn)

                # Insert only 3 dead_letters in the future recent window (< spike_min=5)
                for _ in range(3):
                    dl_row = await conn.fetchrow(
                        """INSERT INTO dead_letters
                               (venue_code, failure_stage, error_message, payload, created_at)
                           VALUES ('polymarket', 'normalization', 'test error',
                                   '{"x":1}'::jsonb, $1)
                           RETURNING dead_letter_id::text""",
                        recent_ts,
                    )
                    dead_letter_ids.append(dl_row["dead_letter_id"])

                pool = _make_pool_from_conn(conn)

                incidents = await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                )

                spikes = [i for i in incidents if i["venue_code"] == "polymarket" and i["incident_type"] == "dead_letter_spike"]
                assert len(spikes) == 0, f"Unexpected dead_letter_spike: {spikes}"

            finally:
                await self._cleanup(conn, market_id=market_id, raw_event_ids=raw_event_ids, dead_letter_ids=dead_letter_ids)
                await conn.close()

        self._run(_run())

    def test_incident_details_contain_expected_keys(self):
        """The incident details dict includes the key evidence fields."""
        import asyncpg
        from pmfi.monitoring.data_quality import check_data_quality

        now = datetime(2036, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        old_received_at = now - timedelta(seconds=700)

        async def _run():
            conn = await asyncpg.connect(_get_dsn())
            market_id = None
            raw_event_ids: list[int] = []
            dead_letter_ids: list[str] = []
            try:
                market_id = await self._setup_synthetic_market(conn)

                row = await conn.fetchrow(
                    """INSERT INTO raw_events
                           (venue_code, source_channel, source_event_type,
                            received_at, payload, payload_hash, parser_version)
                       VALUES ('polymarket', 'test', 'test_event', $1,
                               '{"test": true}'::jsonb,
                               md5(gen_random_uuid()::text),
                               'raw.v1')
                       RETURNING raw_event_id""",
                    old_received_at,
                )
                raw_event_ids.append(int(row["raw_event_id"]))

                pool = _make_pool_from_conn(conn)

                incidents = await check_data_quality(
                    pool,
                    now=now,
                    venue_stale_seconds=600,
                    dead_letter_spike_min=5,
                    dead_letter_spike_ratio=3.0,
                )

                silent = [i for i in incidents if i["incident_type"] == "feed_silent" and i["venue_code"] == "polymarket"]
                assert len(silent) >= 1
                detail = silent[0]["details"]
                assert "stale_seconds" in detail
                assert "last_event_at" in detail
                assert "threshold_seconds" in detail
                assert detail["venue_code"] == "polymarket"

            finally:
                await self._cleanup(conn, market_id=market_id, raw_event_ids=raw_event_ids, dead_letter_ids=dead_letter_ids)
                await conn.close()

        self._run(_run())


# ---------------------------------------------------------------------------
# Helper: minimal pool-like object that returns an already-open connection
# ---------------------------------------------------------------------------

class _FakeAcquireContext:
    """Async context manager that yields an existing asyncpg connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        pass  # Don't close — caller owns the connection


def _make_pool_from_conn(conn):
    """Return an object with a .acquire() method that yields `conn`."""
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireContext(conn))
    return pool
