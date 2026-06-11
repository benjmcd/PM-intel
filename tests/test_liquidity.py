"""Unit tests for the liquidity wall/vacuum assessor (pure, offline)."""
from __future__ import annotations

from decimal import Decimal

from pmfi.pipeline.liquidity import assess_liquidity, build_liquidity_decision


def _lvl(price, size):
    return {"price": Decimal(str(price)), "size": Decimal(str(size))}


def test_wall_fires_when_top_depth_exceeds_threshold():
    # bid side: 0.50 * 60000 = 30000 USD on the top level alone (>= 25000)
    bids = [_lvl("0.50", "60000"), _lvl("0.49", "1000")]
    asks = [_lvl("0.52", "1000")]
    finding = assess_liquidity(bids, asks, min_wall_usd=Decimal("25000"))
    assert finding is not None
    assert finding["kind"] == "wall"
    assert finding["wall_side"] == "bid"
    assert finding["wall_usd"] >= Decimal("25000")


def test_normal_book_does_not_fire():
    bids = [_lvl("0.50", "100"), _lvl("0.49", "100")]
    asks = [_lvl("0.52", "100"), _lvl("0.53", "100")]
    assert assess_liquidity(bids, asks, min_wall_usd=Decimal("25000")) is None


def test_vacuum_fires_on_wide_spread_when_enabled():
    bids = [_lvl("0.30", "10")]
    asks = [_lvl("0.70", "10")]  # spread 0.40
    finding = assess_liquidity(bids, asks, min_wall_usd=Decimal("999999"), min_spread=Decimal("0.10"))
    assert finding is not None
    assert finding["kind"] == "vacuum"


def test_vacuum_not_checked_when_min_spread_none():
    bids = [_lvl("0.30", "10")]
    asks = [_lvl("0.70", "10")]
    assert assess_liquidity(bids, asks, min_wall_usd=Decimal("999999")) is None


def test_empty_book_returns_none():
    assert assess_liquidity([], [], min_wall_usd=Decimal("1")) is None


def test_build_decision_has_expected_shape():
    bids = [_lvl("0.50", "60000")]
    asks = [_lvl("0.52", "1000")]
    finding = assess_liquidity(bids, asks, min_wall_usd=Decimal("25000"))
    decision = build_liquidity_decision(finding, outcome_key="yes")
    assert decision.emit_alert is True
    assert decision.rule_id == "liquidity_wall_v1"
    assert decision.severity == "high"
    assert decision.evidence["wall_side"] == "bid"
    assert decision.evidence["outcome_key"] == "yes"
