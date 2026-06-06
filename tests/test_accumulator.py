from __future__ import annotations
from decimal import Decimal
from pmfi.pipeline.accumulator import DirectionalAccumulator


def test_no_cluster_below_min_count():
    acc = DirectionalAccumulator(window_seconds=300)
    acc.add("polymarket", "mkt-1", "yes", Decimal("10000"), Decimal("0.5"))
    acc.add("polymarket", "mkt-1", "yes", Decimal("10000"), Decimal("0.52"))
    # only 2 trades, need 3
    result = acc.check_cluster("polymarket", "mkt-1", min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"))
    assert result is None


def test_cluster_fires_with_sufficient_trades():
    acc = DirectionalAccumulator(window_seconds=300)
    acc.add("polymarket", "mkt-2", "yes", Decimal("8000"), Decimal("0.50"))
    acc.add("polymarket", "mkt-2", "yes", Decimal("8000"), Decimal("0.53"))
    acc.add("polymarket", "mkt-2", "yes", Decimal("8000"), Decimal("0.56"))
    result = acc.check_cluster("polymarket", "mkt-2", min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"))
    assert result is not None
    assert result.dominant_side == "yes"
    assert result.trade_count == 3
    assert result.net_capital_usd == Decimal("24000")
    # price range: 0.56 - 0.50 = 0.06 * 100 = 6 cents
    assert result.price_impact_cents == Decimal("6")


def test_cluster_no_fire_below_capital():
    acc = DirectionalAccumulator(window_seconds=300)
    acc.add("kalshi", "mkt-3", "yes", Decimal("2000"), Decimal("0.40"))
    acc.add("kalshi", "mkt-3", "yes", Decimal("2000"), Decimal("0.43"))
    acc.add("kalshi", "mkt-3", "yes", Decimal("2000"), Decimal("0.46"))
    result = acc.check_cluster("kalshi", "mkt-3", min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"))
    assert result is None


def test_cluster_no_fire_below_price_impact():
    acc = DirectionalAccumulator(window_seconds=300)
    acc.add("polymarket", "mkt-4", "yes", Decimal("8000"), Decimal("0.50"))
    acc.add("polymarket", "mkt-4", "yes", Decimal("8000"), Decimal("0.505"))
    acc.add("polymarket", "mkt-4", "yes", Decimal("8000"), Decimal("0.509"))
    result = acc.check_cluster("polymarket", "mkt-4", min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"))
    assert result is None


def test_mixed_sides_dominant_wins():
    acc = DirectionalAccumulator(window_seconds=300)
    acc.add("polymarket", "mkt-5", "yes", Decimal("10000"), Decimal("0.50"))
    acc.add("polymarket", "mkt-5", "no",  Decimal("3000"),  Decimal("0.50"))
    acc.add("polymarket", "mkt-5", "yes", Decimal("10000"), Decimal("0.54"))
    acc.add("polymarket", "mkt-5", "yes", Decimal("10000"), Decimal("0.58"))
    result = acc.check_cluster("polymarket", "mkt-5", min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"))
    assert result is not None
    assert result.dominant_side == "yes"
    assert result.trade_count == 3


def test_separate_markets_do_not_interfere():
    acc = DirectionalAccumulator(window_seconds=300)
    for _ in range(3):
        acc.add("polymarket", "mkt-A", "yes", Decimal("8000"), Decimal("0.50"))
    for _ in range(3):
        acc.add("polymarket", "mkt-B", "no", Decimal("8000"), Decimal("0.50"))
    # mkt-A cluster won't fire because price spread is 0
    r_a = acc.check_cluster("polymarket", "mkt-A", min_trade_count=3,
                             min_net_capital_usd=Decimal("15000"),
                             min_price_impact_cents=Decimal("2"))
    assert r_a is None
    r_b = acc.check_cluster("polymarket", "mkt-B", min_trade_count=3,
                             min_net_capital_usd=Decimal("15000"),
                             min_price_impact_cents=Decimal("2"))
    assert r_b is None  # same reason: zero spread
