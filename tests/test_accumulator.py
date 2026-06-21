from __future__ import annotations
from decimal import Decimal
import logging
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


def test_add_with_event_ts_in_past_is_pruned():
    from datetime import datetime, timezone, timedelta
    acc = DirectionalAccumulator(window_seconds=60)
    old_ts = datetime.now(tz=timezone.utc) - timedelta(seconds=120)  # older than window
    acc.add("polymarket", "mkt-event", "yes", Decimal("20000"), Decimal("0.5"), event_ts=old_ts)
    # Adding with old event_ts means it's immediately outside the 60s window
    result = acc.check_cluster("polymarket", "mkt-event",
                               min_trade_count=1,
                               min_net_capital_usd=Decimal("1"),
                               min_price_impact_cents=Decimal("0"))
    assert result is None  # pruned because event_ts is 120s ago, window is 60s


def test_add_with_event_ts_preserves_determinism():
    from datetime import datetime, timezone
    acc = DirectionalAccumulator(window_seconds=300)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    acc.add("polymarket", "mkt-det", "yes", Decimal("10000"), Decimal("0.50"), event_ts=ts)
    acc.add("polymarket", "mkt-det", "yes", Decimal("10000"), Decimal("0.53"), event_ts=ts)
    acc.add("polymarket", "mkt-det", "yes", Decimal("10000"), Decimal("0.56"), event_ts=ts)
    result = acc.check_cluster("polymarket", "mkt-det",
                               min_trade_count=3,
                               min_net_capital_usd=Decimal("15000"),
                               min_price_impact_cents=Decimal("2"),
                               now=ts)
    assert result is not None
    assert result.dominant_side == "yes"


def test_pruning_updates_price_extrema_and_capital_totals():
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=60)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    acc.add("kalshi", "mkt-prune", "yes", Decimal("50000"), Decimal("0.10"), event_ts=ts)
    acc.add("kalshi", "mkt-prune", "yes", Decimal("10000"), Decimal("0.50"), event_ts=ts + timedelta(seconds=70))
    acc.add("kalshi", "mkt-prune", "yes", Decimal("10000"), Decimal("0.60"), event_ts=ts + timedelta(seconds=71))
    acc.add("kalshi", "mkt-prune", "yes", Decimal("10000"), Decimal("0.70"), event_ts=ts + timedelta(seconds=72))

    result = acc.check_cluster(
        "kalshi",
        "mkt-prune",
        min_trade_count=3,
        min_net_capital_usd=Decimal("1"),
        min_price_impact_cents=Decimal("1"),
        now=ts + timedelta(seconds=72),
    )

    assert result is not None
    assert result.trade_count == 3
    assert result.net_capital_usd == Decimal("30000")
    assert result.price_impact_cents == Decimal("20")


def test_dominant_side_uses_rolling_aggregate_after_prune():
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=60)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    acc.add("kalshi", "mkt-side", "yes", Decimal("100000"), Decimal("0.10"), event_ts=ts)
    acc.add("kalshi", "mkt-side", "no", Decimal("10000"), Decimal("0.40"), event_ts=ts + timedelta(seconds=70))
    acc.add("kalshi", "mkt-side", "no", Decimal("10000"), Decimal("0.50"), event_ts=ts + timedelta(seconds=71))
    acc.add("kalshi", "mkt-side", "no", Decimal("10000"), Decimal("0.60"), event_ts=ts + timedelta(seconds=72))

    result = acc.check_cluster(
        "kalshi",
        "mkt-side",
        min_trade_count=3,
        min_net_capital_usd=Decimal("1"),
        min_price_impact_cents=Decimal("1"),
        now=ts + timedelta(seconds=72),
    )

    assert result is not None
    assert result.dominant_side == "no"
    assert result.trade_count == 3
    assert result.net_capital_usd == Decimal("30000")


def test_accumulator_evicts_lru_markets_when_cap_exceeded():
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=300, max_markets=2)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    acc.add("polymarket", "mkt-a", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts)
    acc.add("polymarket", "mkt-b", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts + timedelta(seconds=1))
    acc.check_cluster("polymarket", "mkt-a", min_trade_count=1, now=ts + timedelta(seconds=2))
    acc.add("polymarket", "mkt-c", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts + timedelta(seconds=3))

    assert set(acc._buffers) == {"polymarket:mkt-a", "polymarket:mkt-c"}
    assert set(acc._stats) == {"polymarket:mkt-a", "polymarket:mkt-c"}


def test_accumulator_evicts_cold_markets_by_ttl():
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=300, max_markets=10, market_ttl_seconds=30)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    acc.add("kalshi", "old-a", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts)
    acc.add("kalshi", "old-b", "no", Decimal("100"), Decimal("0.50"), event_ts=ts + timedelta(seconds=5))
    acc.add("kalshi", "fresh", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts + timedelta(seconds=40))

    assert set(acc._buffers) == {"kalshi:fresh"}
    assert set(acc._stats) == {"kalshi:fresh"}


def test_accumulator_logs_market_evictions(caplog):
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=300, max_markets=1)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.DEBUG, logger="pmfi.pipeline.accumulator"):
        acc.add("polymarket", "mkt-a", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts)
        acc.add(
            "polymarket",
            "mkt-b",
            "yes",
            Decimal("100"),
            Decimal("0.50"),
            event_ts=ts + timedelta(seconds=1),
        )

    assert "evicted market key=polymarket:mkt-a reason=lru" in caplog.text


def test_accumulator_logs_ttl_market_evictions(caplog):
    from datetime import datetime, timezone, timedelta

    acc = DirectionalAccumulator(window_seconds=300, max_markets=10, market_ttl_seconds=30)
    ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    with caplog.at_level(logging.DEBUG, logger="pmfi.pipeline.accumulator"):
        acc.add("kalshi", "old", "yes", Decimal("100"), Decimal("0.50"), event_ts=ts)
        acc.add(
            "kalshi",
            "fresh",
            "yes",
            Decimal("100"),
            Decimal("0.50"),
            event_ts=ts + timedelta(seconds=40),
        )

    assert "evicted market key=kalshi:old reason=ttl" in caplog.text
