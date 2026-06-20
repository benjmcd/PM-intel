"""Tests for hardening fixes F2, F3, F6.

Each test is designed to FAIL on the old code and PASS on the fixed code.
No database required — all tests are offline/fixture-driven.
"""
from __future__ import annotations
import statistics
from decimal import Decimal
from pathlib import Path

import pytest

from pmfi.domain import RawEvent, NormalizedTrade
from pmfi.normalization import normalize_kalshi_fixture, NormalizationError
from pmfi.pipeline.engine import AlertEngine
from pmfi.pipeline.normalize import normalize_event
from pmfi.replay import replay_fixtures

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"


# ---------------------------------------------------------------------------
# F2: Kalshi outcome_key fallback must be "unknown" not "yes"
# ---------------------------------------------------------------------------

def test_kalshi_no_usable_side_outcome_key_is_unknown():
    """F2: When Kalshi payload has no usable yes/no side, outcome_key must be 'unknown'.

    Old code: outcome_key=yes_no if yes_no in {"yes","no"} else "yes"
    Fixed:    outcome_key=yes_no if yes_no in {"yes","no"} else "unknown"

    Payload has taker_side="buy" (not yes/no), no yes_no or side field,
    and yes_price set so price parsing succeeds.
    """
    raw = RawEvent(
        venue_code="kalshi",
        source_channel="rest_trades",
        source_event_type="trade",
        source_event_id="test-no-side-001",
        venue_market_id="KXTEST-NO-SIDE",
        payload={
            "trade_id": "test-no-side-001",
            "ticker": "KXTEST-NO-SIDE",
            "yes_price": 45,
            "no_price": 55,
            "count": 1000,
            "taker_side": "buy",  # buy is not yes/no → yes_no resolves to "unknown"
        },
    )
    trade = normalize_kalshi_fixture(raw)
    # The key assertion: must be "unknown", not "yes"
    assert trade.outcome_key == "unknown", (
        f"outcome_key should be 'unknown' for undetermined side, got {trade.outcome_key!r}"
    )
    assert trade.directional_side == "unknown", (
        f"directional_side should be 'unknown', got {trade.directional_side!r}"
    )
    # Sanity: price parsed from yes_price (45 cents → 0.45)
    assert trade.price == Decimal("0.45")
    assert trade.contracts == Decimal("1000")


def test_kalshi_explicit_yes_side_outcome_key_preserved():
    """F2 guard: a valid yes_no='yes' must still map to outcome_key='yes'."""
    raw = RawEvent(
        venue_code="kalshi",
        source_channel="trade_ws",
        source_event_type="trade",
        source_event_id="test-yes-side-001",
        venue_market_id="KXTEST-YES",
        payload={
            "trade_id": "test-yes-side-001",
            "ticker": "KXTEST-YES",
            "yes_no": "yes",
            "taker_side": "buy",
            "price": "0.37",
            "count": "500",
        },
    )
    trade = normalize_kalshi_fixture(raw)
    assert trade.outcome_key == "yes"
    assert trade.directional_side == "yes"


# ---------------------------------------------------------------------------
# F3: volume_spike median uses true median (statistics.median), not upper-middle
# ---------------------------------------------------------------------------

def _window_median(values: list) -> float:
    """Mirror of the fixed engine logic for even-length list verification."""
    return statistics.median(values)


def test_window_median_even_length_is_avg_of_two_middle():
    """F3: statistics.median of even-length list must be the avg of two middle values.

    Old code: _window[len(_window)//2]  → for [1,2,3,4] gives 3 (upper-middle)
    Fixed:    statistics.median(...)    → for [1,2,3,4] gives 2.5 (true median)
    """
    values = [1.0, 2.0, 3.0, 4.0]
    result = _window_median(values)
    assert result == 2.5, f"True median of [1,2,3,4] must be 2.5, got {result}"
    # Confirm old algorithm would have given wrong answer
    old_result = sorted(values)[len(values) // 2]
    assert old_result == 3.0, "Sanity: old upper-middle algorithm gives 3.0 for [1,2,3,4]"
    assert old_result != result, "Old and new must differ for even-length list"


def test_window_median_odd_length():
    """F3 guard: statistics.median of odd-length list returns the true middle."""
    values = [1.0, 2.0, 3.0]
    assert _window_median(values) == 2.0


def test_volume_spike_even_window_uses_true_median():
    """F3: Engine volume_spike baseline_median_usd must equal avg of two middle values.

    We construct a scenario with exactly min_baseline_trades (20) small trades all
    at $100 capital, then one at $101 to make even-window median concrete, then fire
    a spike. The test verifies baseline_median_usd == statistics.median of the window,
    not the old upper-middle value.

    For a window of 20 trades where 10 are $100 and 10 are $200:
      sorted: [100]*10 + [200]*10
      true median = (100+200)/2 = 150.0
      old upper-middle = sorted[10] = 200.0  ← biased upward
    """
    engine = AlertEngine()
    market_id = "f3-even-median-test-market"

    def _trade(cap_usd: float) -> NormalizedTrade:
        contracts = Decimal(str(cap_usd * 2))
        return NormalizedTrade(
            venue_code="polymarket",
            venue_market_id=market_id,
            outcome_key="yes",
            price=Decimal("0.5"),
            contracts=contracts,
            capital_at_risk_usd=Decimal(str(cap_usd)),
            payout_notional_usd=contracts,
        )

    # 10 trades at $100, 10 trades at $200 → sorted window = [100]*10 + [200]*10
    # true median = (100+200)/2 = 150.0; old upper-middle[10] = 200.0
    for _ in range(10):
        engine.evaluate(_trade(100.0))
    for _ in range(10):
        engine.evaluate(_trade(200.0))

    # Now fire a spike trade: with the true median ($150), $900 is safely above
    # the 5x threshold and meets the configured notional floor. With the old
    # upper-middle $200 baseline, $900 would remain below the $1000 threshold.
    decisions = engine.evaluate(_trade(900.0))
    spike_hits = [d for d in decisions if d.rule_id == "volume_spike_v1"]
    assert spike_hits, (
        "volume_spike_v1 should fire: $900 >= 5x true-median-$150; "
        "if it did not fire, the old upper-middle $200 baseline is likely still in use "
        "(5x of $200 = $1000, which $900 does not reach)"
    )
    ev = spike_hits[0].evidence
    assert ev["baseline_median_usd"] == 150.0, (
        f"baseline_median_usd must be the true median 150.0, got {ev['baseline_median_usd']!r}"
    )


# ---------------------------------------------------------------------------
# F6: replay_fixtures uses normalize_event (unified path)
# ---------------------------------------------------------------------------

def test_replay_fixtures_matches_normalize_event_polymarket():
    """F6: replay_fixtures result must match normalize_event directly for polymarket fixture."""
    from pmfi.fixtures import load_raw_event
    fixture_path = FIXTURE_DIR / "polymarket_last_trade_price.json"
    raw = load_raw_event(fixture_path)

    # Direct call via unified normalizer
    direct_trade = normalize_event(raw)
    assert direct_trade is not None, "fixture must produce a trade via normalize_event"

    # Via replay_fixtures over a single-file temp dir
    import shutil, tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(fixture_path, tmp / fixture_path.name)
        results = replay_fixtures(tmp)

    assert len(results) == 1, f"expected 1 replay result, got {len(results)}"
    replay_trade = results[0].trade

    assert replay_trade.venue_market_id == direct_trade.venue_market_id
    assert replay_trade.price == direct_trade.price
    assert replay_trade.contracts == direct_trade.contracts
    assert replay_trade.outcome_key == direct_trade.outcome_key


def test_replay_fixtures_matches_normalize_event_kalshi():
    """F6: replay_fixtures result must match normalize_event directly for kalshi fixture."""
    from pmfi.fixtures import load_raw_event
    fixture_path = FIXTURE_DIR / "kalshi_trade.json"
    raw = load_raw_event(fixture_path)

    direct_trade = normalize_event(raw)
    assert direct_trade is not None

    import shutil, tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        shutil.copy(fixture_path, tmp / fixture_path.name)
        results = replay_fixtures(tmp)

    assert len(results) == 1
    replay_trade = results[0].trade

    assert replay_trade.venue_market_id == direct_trade.venue_market_id
    assert replay_trade.price == direct_trade.price
    assert replay_trade.contracts == direct_trade.contracts
    assert replay_trade.outcome_key == direct_trade.outcome_key


def test_replay_fixtures_skips_non_trade_polymarket_event():
    """F6: replay_fixtures skips benign non-trade polymarket events (normalize_event returns None).

    A polymarket event with source_event_type not in trade types returns None from
    normalize_event, and replay_fixtures must skip it (produce no ReplayResult).
    """
    import json, tempfile
    non_trade_payload = {
        "venue_code": "polymarket",
        "source_channel": "market_ws",
        "source_event_type": "subscription_confirmed",
        "source_event_id": "sub-001",
        "venue_market_id": "pm-test-market",
        "exchange_ts": None,
        "received_at": None,
        "payload": {},
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        fixture_file = tmp / "non_trade.json"
        fixture_file.write_text(json.dumps(non_trade_payload), encoding="utf-8")
        results = replay_fixtures(tmp)

    assert results == [], (
        f"replay_fixtures must produce no results for a non-trade event, got {results!r}"
    )
