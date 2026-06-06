from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class _TradeEntry:
    ts: datetime
    directional_side: str
    capital_at_risk_usd: Decimal
    price: Decimal


@dataclass
class ClusterResult:
    dominant_side: str
    trade_count: int
    net_capital_usd: Decimal
    price_impact_cents: Decimal
    window_seconds: int


class DirectionalAccumulator:
    """In-memory rolling window accumulator for directional cluster detection.

    Keyed by (venue_code, venue_market_id). Entries older than window_seconds
    are pruned on each call. Thread-safety is not required for single-threaded
    asyncio pipelines.
    """

    def __init__(self, window_seconds: int = 300) -> None:
        self._window_seconds = window_seconds
        self._buffers: dict[str, deque[_TradeEntry]] = {}

    def _key(self, venue_code: str, venue_market_id: str) -> str:
        return f"{venue_code}:{venue_market_id}"

    def _prune(self, buf: deque[_TradeEntry], now: datetime) -> None:
        cutoff = now.timestamp() - self._window_seconds
        while buf and buf[0].ts.timestamp() < cutoff:
            buf.popleft()

    def add(self, venue_code: str, venue_market_id: str, directional_side: str,
            capital_at_risk_usd: Decimal, price: Decimal,
            event_ts: datetime | None = None) -> None:
        key = self._key(venue_code, venue_market_id)
        if key not in self._buffers:
            self._buffers[key] = deque()
        buf = self._buffers[key]
        now = event_ts if event_ts is not None else _utcnow()
        self._prune(buf, now)
        buf.append(_TradeEntry(
            ts=now,
            directional_side=directional_side,
            capital_at_risk_usd=capital_at_risk_usd,
            price=price,
        ))

    def check_cluster(
        self,
        venue_code: str,
        venue_market_id: str,
        *,
        min_trade_count: int = 3,
        min_net_capital_usd: Decimal = Decimal("15000"),
        min_price_impact_cents: Decimal = Decimal("2"),
        now: datetime | None = None,
    ) -> ClusterResult | None:
        key = self._key(venue_code, venue_market_id)
        buf = self._buffers.get(key)
        if not buf:
            return None
        now = now if now is not None else _utcnow()
        self._prune(buf, now)

        if len(buf) < min_trade_count:
            return None

        # tally by direction
        yes_cap = Decimal(0)
        no_cap = Decimal(0)
        yes_prices: list[Decimal] = []
        no_prices: list[Decimal] = []
        for entry in buf:
            if entry.directional_side == "yes":
                yes_cap += entry.capital_at_risk_usd
                yes_prices.append(entry.price)
            elif entry.directional_side == "no":
                no_cap += entry.capital_at_risk_usd
                no_prices.append(entry.price)

        if yes_cap >= no_cap:
            dominant = "yes"
            net_cap = yes_cap
            prices = yes_prices
            count = len(yes_prices)
        else:
            dominant = "no"
            net_cap = no_cap
            prices = no_prices
            count = len(no_prices)

        if count < min_trade_count:
            return None
        if net_cap < min_net_capital_usd:
            return None

        if len(prices) >= 2:
            price_impact = (max(prices) - min(prices)) * Decimal("100")
        else:
            price_impact = Decimal("0")

        if price_impact < min_price_impact_cents:
            return None

        return ClusterResult(
            dominant_side=dominant,
            trade_count=count,
            net_capital_usd=net_cap,
            price_impact_cents=price_impact,
            window_seconds=self._window_seconds,
        )
