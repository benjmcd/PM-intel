from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
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
    seq: int


@dataclass
class _PricePoint:
    seq: int
    price: Decimal


@dataclass
class _WindowStats:
    yes_count: int = 0
    no_count: int = 0
    yes_capital: Decimal = Decimal("0")
    no_capital: Decimal = Decimal("0")
    yes_min_prices: deque[_PricePoint] = field(default_factory=deque)
    yes_max_prices: deque[_PricePoint] = field(default_factory=deque)
    no_min_prices: deque[_PricePoint] = field(default_factory=deque)
    no_max_prices: deque[_PricePoint] = field(default_factory=deque)


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
    are pruned on each call. Market buffers are also bounded by LRU count and
    cold-market TTL so unattended runs cannot retain an unbounded market map.
    The tradeoff is deliberate: a market evicted after going cold restarts with
    an empty in-memory directional window when it trades again.
    Thread-safety is not required for single-threaded asyncio pipelines.
    """

    def __init__(
        self,
        window_seconds: int = 300,
        *,
        max_markets: int = 5000,
        market_ttl_seconds: float = 3600.0,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_markets = max(1, int(max_markets))
        self._market_ttl_seconds = float(market_ttl_seconds)
        self._buffers: dict[str, deque[_TradeEntry]] = {}
        self._stats: dict[str, _WindowStats] = {}
        self._last_seen: dict[str, datetime] = {}
        self._next_seq = 0

    def _key(self, venue_code: str, venue_market_id: str) -> str:
        return f"{venue_code}:{venue_market_id}"

    def _prune(self, key: str, buf: deque[_TradeEntry], now: datetime) -> None:
        cutoff = now.timestamp() - self._window_seconds
        stats = self._stats.setdefault(key, _WindowStats())
        while buf and buf[0].ts.timestamp() < cutoff:
            self._remove_from_stats(stats, buf.popleft())

    def _drop_market(self, key: str) -> None:
        self._buffers.pop(key, None)
        self._stats.pop(key, None)
        self._last_seen.pop(key, None)

    def _touch(self, key: str, now: datetime) -> None:
        self._last_seen[key] = now

    def _evict_cold_markets(self, now: datetime) -> None:
        if self._market_ttl_seconds <= 0:
            return
        cutoff = now.timestamp() - self._market_ttl_seconds
        for key, seen_at in list(self._last_seen.items()):
            if seen_at.timestamp() < cutoff:
                self._drop_market(key)

    def _evict_lru_markets(self) -> None:
        while len(self._buffers) > self._max_markets:
            coldest_key = min(
                self._buffers,
                key=lambda k: self._last_seen.get(k, datetime.min.replace(tzinfo=timezone.utc)),
            )
            self._drop_market(coldest_key)

    @staticmethod
    def _push_price(
        min_prices: deque[_PricePoint],
        max_prices: deque[_PricePoint],
        *,
        seq: int,
        price: Decimal,
    ) -> None:
        while min_prices and min_prices[-1].price > price:
            min_prices.pop()
        min_prices.append(_PricePoint(seq=seq, price=price))

        while max_prices and max_prices[-1].price < price:
            max_prices.pop()
        max_prices.append(_PricePoint(seq=seq, price=price))

    @staticmethod
    def _expire_price(prices: deque[_PricePoint], *, through_seq: int) -> None:
        while prices and prices[0].seq <= through_seq:
            prices.popleft()

    def _add_to_stats(self, stats: _WindowStats, entry: _TradeEntry) -> None:
        if entry.directional_side == "yes":
            stats.yes_count += 1
            stats.yes_capital += entry.capital_at_risk_usd
            self._push_price(
                stats.yes_min_prices,
                stats.yes_max_prices,
                seq=entry.seq,
                price=entry.price,
            )
        elif entry.directional_side == "no":
            stats.no_count += 1
            stats.no_capital += entry.capital_at_risk_usd
            self._push_price(
                stats.no_min_prices,
                stats.no_max_prices,
                seq=entry.seq,
                price=entry.price,
            )

    def _remove_from_stats(self, stats: _WindowStats, entry: _TradeEntry) -> None:
        if entry.directional_side == "yes":
            stats.yes_count -= 1
            stats.yes_capital -= entry.capital_at_risk_usd
            self._expire_price(stats.yes_min_prices, through_seq=entry.seq)
            self._expire_price(stats.yes_max_prices, through_seq=entry.seq)
        elif entry.directional_side == "no":
            stats.no_count -= 1
            stats.no_capital -= entry.capital_at_risk_usd
            self._expire_price(stats.no_min_prices, through_seq=entry.seq)
            self._expire_price(stats.no_max_prices, through_seq=entry.seq)

    def add(self, venue_code: str, venue_market_id: str, directional_side: str,
            capital_at_risk_usd: Decimal, price: Decimal,
            event_ts: datetime | None = None) -> None:
        key = self._key(venue_code, venue_market_id)
        now = event_ts if event_ts is not None else _utcnow()
        self._evict_cold_markets(now)
        if key not in self._buffers:
            self._buffers[key] = deque()
            self._stats[key] = _WindowStats()
        buf = self._buffers[key]
        stats = self._stats[key]
        self._prune(key, buf, now)
        seq = self._next_seq
        self._next_seq += 1
        entry = _TradeEntry(
            ts=now,
            directional_side=directional_side,
            capital_at_risk_usd=capital_at_risk_usd,
            price=price,
            seq=seq,
        )
        buf.append(entry)
        self._add_to_stats(stats, entry)
        self._touch(key, now)
        self._evict_lru_markets()

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
        self._evict_cold_markets(now)
        buf = self._buffers.get(key)
        if not buf:
            return None
        self._prune(key, buf, now)
        if not buf:
            self._drop_market(key)
            return None
        self._touch(key, now)
        stats = self._stats.setdefault(key, _WindowStats())

        if len(buf) < min_trade_count:
            return None

        if stats.yes_capital >= stats.no_capital:
            dominant = "yes"
            net_cap = stats.yes_capital
            count = stats.yes_count
            min_prices = stats.yes_min_prices
            max_prices = stats.yes_max_prices
        else:
            dominant = "no"
            net_cap = stats.no_capital
            count = stats.no_count
            min_prices = stats.no_min_prices
            max_prices = stats.no_max_prices

        if count < min_trade_count:
            return None
        if net_cap < min_net_capital_usd:
            return None

        if count >= 2 and min_prices and max_prices:
            price_impact = (max_prices[0].price - min_prices[0].price) * Decimal("100")
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
