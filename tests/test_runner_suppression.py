"""Tests for alert suppression logic in the pipeline runner."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest


# ---------------------------------------------------------------------------
# Suppression cache key and timing logic (no asyncpg dependency)
# The cache is dict[tuple[str, str, str, str], datetime]; 4-tuple includes outcome_key.
# ---------------------------------------------------------------------------

def test_suppression_key_is_venue_market_rule_outcome_tuple():
    cache: dict = {}
    key = ("polymarket", "market-abc", "large_trade_absolute_v1", "yes")
    now = datetime.now(timezone.utc)
    cache[key] = now
    assert cache[key] is now


def test_suppression_within_window_is_detected():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a", "yes")
    now = datetime.now(timezone.utc)
    cache[key] = now
    later = now + timedelta(seconds=100)
    assert (later - cache[key]).total_seconds() < 300


def test_suppression_outside_window_is_not_suppressed():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a", "yes")
    now = datetime.now(timezone.utc)
    cache[key] = now
    later = now + timedelta(seconds=400)
    assert (later - cache[key]).total_seconds() >= 300


def test_different_rules_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a", "yes")] = now
    assert ("polymarket", "market-1", "rule-b", "yes") not in cache


def test_different_markets_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a", "yes")] = now
    assert ("polymarket", "market-2", "rule-a", "yes") not in cache


def test_different_venues_have_independent_suppression():
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a", "yes")] = now
    assert ("kalshi", "market-1", "rule-a", "yes") not in cache


def test_different_outcome_keys_have_independent_suppression():
    """YES and NO alerts for the same rule/market are not suppressed by each other."""
    cache: dict = {}
    now = datetime.now(timezone.utc)
    cache[("polymarket", "market-1", "rule-a", "yes")] = now
    assert ("polymarket", "market-1", "rule-a", "no") not in cache


def test_suppression_timestamp_updated_on_refill():
    cache: dict = {}
    key = ("polymarket", "market-1", "rule-a", "yes")
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=400)
    cache[key] = t1
    cache[key] = t2  # refill after window expired
    assert cache[key] == t2


def test_none_outcome_key_uses_empty_string_sentinel():
    """None outcome_key is normalised to '' so live and hydrated keys match."""
    cache: dict = {}
    now = datetime.now(timezone.utc)
    # Simulate what runner.py stores: outcome_key or ""
    key = ("polymarket", "market-1", "rule-a", None or "")
    cache[key] = now
    assert ("polymarket", "market-1", "rule-a", "") in cache
    assert ("polymarket", "market-1", "rule-a", None) not in cache


# ---------------------------------------------------------------------------
# _months_ahead helper (migrations.py — no asyncpg dependency)
# ---------------------------------------------------------------------------

def test_alert_outcome_key_prefers_directional_dominant_side():
    """Directional alert rows attach to the detected side, not a contrary trigger trade."""
    from unittest.mock import MagicMock
    from pmfi.domain import AlertDecision
    from pmfi.pipeline.runner import _alert_outcome_key

    trade = MagicMock()
    trade.outcome_key = "yes"
    decision = AlertDecision(
        emit_alert=True,
        rule_id="directional_cluster_v1",
        rule_version="v1",
        severity="high",
        confidence="medium",
        score=Decimal("0.75"),
        reason_codes=("directional_cluster_detected",),
        evidence={"outcome_key": "yes", "directional_side": "yes", "dominant_side": "no"},
        data_quality="in_window",
    )

    assert _alert_outcome_key(decision, trade) == "no"


def test_alert_outcome_key_falls_back_to_trade_outcome_for_non_directional_rules():
    from unittest.mock import MagicMock
    from pmfi.domain import AlertDecision
    from pmfi.pipeline.runner import _alert_outcome_key

    trade = MagicMock()
    trade.outcome_key = "yes"
    decision = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="v1",
        severity="medium",
        confidence="medium",
        score=Decimal("0.6"),
        reason_codes=("payout_notional_threshold",),
        evidence={},
        data_quality="verified",
    )

    assert _alert_outcome_key(decision, trade) == "yes"


def test_months_ahead_basic():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 1, 3) == [(2025, 1), (2025, 2), (2025, 3), (2025, 4)]


def test_months_ahead_year_rollover():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 11, 3) == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_months_ahead_december():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 12, 2) == [(2025, 12), (2026, 1), (2026, 2)]


def test_months_ahead_zero_returns_only_current():
    from pmfi.db.migrations import _months_ahead
    assert _months_ahead(2025, 6, 0) == [(2025, 6)]


def test_months_ahead_length_is_count_plus_one():
    from pmfi.db.migrations import _months_ahead
    for n in range(5):
        assert len(_months_ahead(2025, 3, n)) == n + 1


# ---------------------------------------------------------------------------
# process_event suppression end-to-end (requires asyncpg; skipped if absent)
# ---------------------------------------------------------------------------

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg not installed in test env")


def test_process_event_suppresses_second_alert_within_window():
    """insert_alert called once; second identical alert within window is suppressed.

    Suppression uses event-time (trade.exchange_ts or received_at).  Both calls
    share the same event_ts so the second fires within the 300-second window.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="ev-001",
        venue_market_id="test-market-suppression",
        exchange_ts=_event_ts,
        payload={"price": "0.65", "size": "50000", "side": "buy", "outcome": "yes"},
    )
    alert = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="v1",
        severity="high",
        confidence="high",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        data_quality="unverified",
        evidence={},
    )
    mock_trade = MagicMock()
    mock_trade.venue_code = "polymarket"
    mock_trade.venue_market_id = "test-market-suppression"
    mock_trade.outcome_key = "yes"
    mock_trade.capital_at_risk_usd = Decimal("50000")
    # Provide real datetime values so event_now = trade.exchange_ts or received_at
    # resolves to a datetime and suppression arithmetic works correctly.
    mock_trade.exchange_ts = _event_ts
    mock_trade.received_at = _event_ts

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = [alert]
    mock_handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-1", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-1")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-1")) as mock_insert,
    ):
        cache: dict = {}
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1
        assert mock_handler.call_count == 1

        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1, "suppressed within window"
        assert mock_handler.call_count == 1, "alert handler suppressed too"


def test_process_event_no_suppression_when_none():
    """suppression=None fires every time (replay / backtest mode)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent, AlertDecision
    from pmfi.pipeline.runner import process_event

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id="ev-002",
        venue_market_id="test-market-nosup",
        exchange_ts=None,
        payload={},
    )
    alert = AlertDecision(
        emit_alert=True, rule_id="rule-x", rule_version="v1", severity="low",
        confidence="low", score=Decimal("0.5"), reason_codes=(), data_quality="unverified", evidence={},
    )
    mock_trade = MagicMock()
    mock_trade.venue_code = "polymarket"
    mock_trade.venue_market_id = "test-market-nosup"
    mock_trade.outcome_key = "yes"
    mock_trade.capital_at_risk_usd = Decimal("1000")

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = [alert]
    mock_handler = AsyncMock()

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-2", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=mock_trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-2")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock()),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-2")) as mock_insert,
    ):
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler, suppression=None))
        asyncio.run(process_event(raw, mock_pool, mock_engine, mock_handler, suppression=None))
        assert mock_insert.call_count == 2, "replay mode: both calls insert"


def _make_process_event_mocks(outcome_key: str, event_id: str, _event_ts: object):
    """Return (raw, mock_trade, mock_pool, mock_engine, mock_handler) for process_event tests."""
    from unittest.mock import AsyncMock, MagicMock
    from pmfi.domain import RawEvent, AlertDecision

    raw = RawEvent(
        venue_code="polymarket",
        source_channel="ws_clob",
        source_event_type="trade",
        source_event_id=event_id,
        venue_market_id="binary-market",
        exchange_ts=_event_ts,
        payload={"price": "0.6", "size": "10000", "side": "buy", "outcome": outcome_key},
    )
    alert = AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.9"),
        reason_codes=("capital_at_risk_threshold",),
        data_quality="unverified",
        evidence={},
    )
    mock_trade = MagicMock()
    mock_trade.venue_code = "polymarket"
    mock_trade.venue_market_id = "binary-market"
    mock_trade.outcome_key = outcome_key
    mock_trade.capital_at_risk_usd = Decimal("10000")
    mock_trade.exchange_ts = _event_ts
    mock_trade.received_at = _event_ts

    mock_conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.evaluate.return_value = [alert]
    mock_handler = AsyncMock()

    return raw, mock_trade, mock_pool, mock_engine, mock_handler


def test_process_event_different_outcome_keys_both_emit():
    """YES and NO alerts for the same rule+market BOTH fire within the suppression window."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw_yes, trade_yes, pool_yes, engine_yes, handler_yes = _make_process_event_mocks("yes", "ev-yes", _event_ts)
    raw_no, trade_no, pool_no, engine_no, handler_no = _make_process_event_mocks("no", "ev-no", _event_ts)

    cache: dict = {}

    # Shared insert_alert mock across both calls
    mock_insert = AsyncMock(return_value="al-x")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-y", False))),
        patch("pmfi.pipeline.runner.normalize_event") as mock_norm,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-bin")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert),
    ):
        mock_norm.return_value = trade_yes
        asyncio.run(process_event(raw_yes, pool_yes, engine_yes, handler_yes,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1, "YES alert should fire"

        mock_norm.return_value = trade_no
        asyncio.run(process_event(raw_no, pool_no, engine_no, handler_no,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 2, "NO alert should also fire (different outcome_key)"


def test_process_event_same_outcome_key_suppresses_second():
    """Same outcome_key on the same rule+market within the window fires only once."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw1, trade1, pool1, engine1, handler1 = _make_process_event_mocks("yes", "ev-s1", _event_ts)
    raw2, trade2, pool2, engine2, handler2 = _make_process_event_mocks("yes", "ev-s2", _event_ts)

    cache: dict = {}
    mock_insert = AsyncMock(return_value="al-z")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-s", False))),
        patch("pmfi.pipeline.runner.normalize_event") as mock_norm,
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-s")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert),
    ):
        mock_norm.return_value = trade1
        asyncio.run(process_event(raw1, pool1, engine1, handler1,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1

        mock_norm.return_value = trade2
        asyncio.run(process_event(raw2, pool2, engine2, handler2,
                                  suppression=cache, suppression_window_seconds=300))
        assert mock_insert.call_count == 1, "same outcome_key should be suppressed"


def test_process_event_does_not_deliver_when_alert_insert_returns_none():
    """If DB insert is deduped/fails closed with None, no external alert is delivered."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw, trade, pool, engine, handler = _make_process_event_mocks("yes", "ev-insert-none", _event_ts)
    mock_insert = AsyncMock(return_value=None)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-none", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-none")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid-none")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert),
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    assert mock_insert.call_count == 1
    assert handler.call_count == 0


def test_process_event_delivers_when_alert_insert_returns_id():
    """External delivery runs after the alert is queryable in the DB."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw, trade, pool, engine, handler = _make_process_event_mocks("yes", "ev-insert-id", _event_ts)
    mock_insert = AsyncMock(return_value="al-deliver")

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-id", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-id")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid-id")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert),
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    assert mock_insert.call_count == 1
    assert handler.call_count == 1


def test_process_event_delivers_alert_after_pool_connection_released():
    """Alert delivery is external IO and must not hold the pooled DB connection."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.pipeline.runner import process_event

    class _TrackedAcquire:
        def __init__(self, pool):
            self.pool = pool

        async def __aenter__(self):
            self.pool.active_connections += 1
            return self.pool.conn

        async def __aexit__(self, exc_type, exc, tb):
            self.pool.active_connections -= 1
            return False

    class _TrackedPool:
        def __init__(self):
            self.conn = AsyncMock()
            self.active_connections = 0

        def acquire(self):
            return _TrackedAcquire(self)

    _event_ts = datetime.now(timezone.utc)
    raw, trade, _pool, engine, _handler = _make_process_event_mocks("yes", "ev-conn-release", _event_ts)
    pool = _TrackedPool()
    observed_active_connections: list[int] = []

    async def handler(decision, alert_id, raw_event=None):
        observed_active_connections.append(pool.active_connections)

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-release", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-release")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid-release")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=AsyncMock(return_value="al-release")),
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    assert observed_active_connections == [0]


def test_process_event_drains_persisted_alert_delivery_when_later_db_write_fails():
    """Already persisted alerts are delivered even if a later alert write fails."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.domain import AlertDecision
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw, trade, pool, engine, handler = _make_process_event_mocks(
        "yes",
        "ev-delivery-drain",
        _event_ts,
    )
    first_alert = engine.evaluate.return_value[0]
    second_alert = AlertDecision(
        emit_alert=True,
        rule_id="market_relative_large_trade_v1",
        rule_version="v1",
        severity="medium",
        confidence="high",
        score=Decimal("0.8"),
        reason_codes=("relative_to_baseline",),
        data_quality="unverified",
        evidence={},
    )
    engine.evaluate.return_value = [first_alert, second_alert]
    mock_insert_alert = AsyncMock(side_effect=["al-drained", RuntimeError("alert insert failed")])

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-drain", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-drain")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid-drain")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert_alert),
        patch("pmfi.pipeline.runner.insert_dead_letter", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(RuntimeError, match="alert insert failed"):
            asyncio.run(process_event(raw, pool, engine, handler, suppression=None))

    assert mock_insert_alert.call_count == 2
    assert handler.call_count == 1
    assert handler.await_args.args[0] is first_alert


def test_run_adapter_pipeline_invokes_rules_reloader_before_each_event():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from pmfi.domain import RawEvent
    from pmfi.pipeline.runner import run_adapter_pipeline

    event_ts = datetime.now(timezone.utc)
    raw_events = [
        RawEvent(
            venue_code="polymarket",
            source_channel="ws_clob",
            source_event_type="trade",
            source_event_id=f"reload-{idx}",
            venue_market_id="reload-market",
            exchange_ts=event_ts,
            payload={"price": "0.51", "size": "10", "side": "buy", "outcome": "yes"},
        )
        for idx in range(2)
    ]

    async def _events():
        for raw in raw_events:
            yield raw

    class _Acquire:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _Acquire()

    reload_calls: list[str] = []

    def _rules_reloader() -> bool:
        reload_calls.append("checked")
        return False

    async def _handler(*args, **kwargs):
        return None

    process_mock = AsyncMock()
    with (
        patch("pmfi.db.repos.alerts.load_suppression_cache", new=AsyncMock(return_value={})),
        patch("pmfi.pipeline.runner.process_event", new=process_mock),
    ):
        processed = asyncio.run(
            run_adapter_pipeline(
                _events(),
                _Pool(),
                MagicMock(),
                _handler,
                rules_reloader=_rules_reloader,
            )
        )

    assert processed == 2
    assert reload_calls == ["checked", "checked"]
    assert process_mock.call_count == 2


def test_process_event_inserts_directional_alert_with_dominant_side_outcome():
    """Directional cluster persistence should use dominant_side for DB rows and suppression."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    from pmfi.domain import AlertDecision
    from pmfi.pipeline.runner import process_event

    _event_ts = datetime.now(timezone.utc)
    raw, trade, pool, engine, handler = _make_process_event_mocks("yes", "ev-directional-side", _event_ts)
    directional = AlertDecision(
        emit_alert=True,
        rule_id="directional_cluster_v1",
        rule_version="v1",
        severity="high",
        confidence="medium",
        score=Decimal("0.75"),
        reason_codes=("directional_cluster_detected",),
        evidence={"outcome_key": "yes", "directional_side": "yes", "dominant_side": "no"},
        data_quality="in_window",
    )
    engine.evaluate.return_value = [directional]
    mock_insert = AsyncMock(return_value="al-directional")
    cache: dict = {}

    with (
        patch("pmfi.pipeline.runner.insert_raw_event", new=AsyncMock(return_value=("raw-id", False))),
        patch("pmfi.pipeline.runner.normalize_event", return_value=trade),
        patch("pmfi.pipeline.runner.upsert_market", new=AsyncMock(return_value="mkt-id")),
        patch("pmfi.pipeline.runner.insert_trade", new=AsyncMock(return_value="tid-id")),
        patch("pmfi.pipeline.runner.upsert_metric_window", new=AsyncMock()),
        patch("pmfi.pipeline.runner.insert_alert", new=mock_insert),
    ):
        asyncio.run(process_event(raw, pool, engine, handler, suppression=cache))

    assert mock_insert.call_count == 1
    assert mock_insert.call_args.kwargs["outcome_key"] == "no"
    assert ("polymarket", "mkt-id", "directional_cluster_v1", "no") in cache
    assert ("polymarket", "mkt-id", "directional_cluster_v1", "yes") not in cache


# ---------------------------------------------------------------------------
# load_suppression_cache DB seeding (no asyncpg dependency)
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, query, *args):
        return self._rows


def test_load_suppression_cache_returns_dict():
    """load_suppression_cache maps (venue_code, market_id, rule_id, outcome_key) -> datetime."""
    import asyncio
    from pmfi.db.repos.alerts import load_suppression_cache
    now = datetime.now(timezone.utc)
    fake_rows = [
        {"venue_code": "polymarket", "market_id": "42", "rule_key": "large_trade_absolute_v1", "outcome_key": "yes", "last_fired_at": now - timedelta(seconds=60)},
        {"venue_code": "kalshi", "market_id": "7", "rule_key": "directional_cluster_v1", "outcome_key": "", "last_fired_at": now - timedelta(seconds=120)},
    ]
    conn = _FakeConn(fake_rows)
    result = asyncio.run(load_suppression_cache(conn, window_seconds=300))
    assert ("polymarket", "42", "large_trade_absolute_v1", "yes") in result
    assert ("kalshi", "7", "directional_cluster_v1", "") in result
    assert len(result) == 2


def test_load_suppression_cache_empty():
    """Empty alerts table returns empty dict."""
    import asyncio
    from pmfi.db.repos.alerts import load_suppression_cache
    conn = _FakeConn([])
    result = asyncio.run(load_suppression_cache(conn, window_seconds=300))
    assert result == {}


def test_load_suppression_cache_key_shape_matches_live_key():
    """Hydrated key shape (4-tuple) is identical to what runner.py writes live."""
    import asyncio
    from pmfi.db.repos.alerts import load_suppression_cache
    now = datetime.now(timezone.utc)
    # Simulate a NULL outcome_key in DB: COALESCE yields ''
    fake_rows = [
        {"venue_code": "polymarket", "market_id": "99", "rule_key": "rule-z", "outcome_key": "", "last_fired_at": now},
    ]
    conn = _FakeConn(fake_rows)
    result = asyncio.run(load_suppression_cache(conn, window_seconds=300))
    # Live key: (venue_code, str(market_id), rule_id, trade.outcome_key or "")
    live_key = ("polymarket", "99", "rule-z", "")
    assert live_key in result


def test_load_suppression_cache_different_outcomes_are_separate_entries():
    """YES and NO for the same rule/market produce two independent cache entries."""
    import asyncio
    from pmfi.db.repos.alerts import load_suppression_cache
    now = datetime.now(timezone.utc)
    fake_rows = [
        {"venue_code": "polymarket", "market_id": "5", "rule_key": "rule-r", "outcome_key": "yes", "last_fired_at": now},
        {"venue_code": "polymarket", "market_id": "5", "rule_key": "rule-r", "outcome_key": "no", "last_fired_at": now},
    ]
    conn = _FakeConn(fake_rows)
    result = asyncio.run(load_suppression_cache(conn, window_seconds=300))
    assert len(result) == 2
    assert ("polymarket", "5", "rule-r", "yes") in result
    assert ("polymarket", "5", "rule-r", "no") in result
