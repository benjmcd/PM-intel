from __future__ import annotations
import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import AsyncIterator

import aiohttp

from pmfi.domain import RawEvent
from pmfi.markets import fetch_kalshi_trades, kalshi_trade_to_raw_event

logger = logging.getLogger(__name__)


def _kalshi_unix_seconds(ts: str) -> int | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class KalshiRestPollingAdapter:
    """Continuous Kalshi ingest via public REST polling.

    Polls /trade-api/v2/markets/trades for each ticker on a configurable interval.
    No API key required — the endpoint is public.

    Note: KalshiAdapter (kalshi.py) is the WebSocket path but requires RSA auth;
    this REST polling adapter is the current supported continuous ingest path.
    """

    venue_code = "kalshi"

    def __init__(
        self,
        *,
        tickers: list[str],
        poll_interval_seconds: float = 5.0,
        limit: int = 200,
        max_pages: int = 1,
        all_market_poll: bool = False,
        timeout_seconds: int = 10,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        reconnect_jitter: bool = True,
    ) -> None:
        if limit <= 0:
            raise ValueError("Kalshi REST poll limit must be positive")
        if max_pages <= 0:
            raise ValueError("Kalshi REST poll max_pages must be positive")
        self._tickers = tickers
        self._poll_interval_seconds = poll_interval_seconds
        self._limit = limit
        self._max_pages = max_pages
        self._all_market_poll = all_market_poll
        self._timeout_seconds = timeout_seconds
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._reconnect_jitter = reconnect_jitter
        self._running = False

    async def connect(self) -> None:
        self._running = True

    async def disconnect(self) -> None:
        self._running = False

    async def __aenter__(self) -> "KalshiRestPollingAdapter":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()

    async def events(self) -> AsyncIterator[RawEvent]:  # type: ignore[override]
        backoff = self._initial_backoff
        # Per-ticker: seen trade_ids from the PREVIOUS cycle (for cross-cycle dedup opt).
        prev_seen: dict[str, set[str]] = {t: set() for t in self._tickers}
        # Per-ticker: max created_time seen, for gap detection.
        prev_max_ts: dict[str, str] = {}
        while self._running:
            # Per-ticker seen set for THIS cycle only.
            cycle_seen: dict[str, set[str]] = {t: set() for t in self._tickers}
            try:
                if self._all_market_poll:
                    min_ts = None
                    prior_seconds = [
                        parsed
                        for ts in prev_max_ts.values()
                        if (parsed := _kalshi_unix_seconds(ts)) is not None
                    ]
                    if prior_seconds:
                        min_ts = max(0, min(prior_seconds) - 1)
                    trades = await fetch_kalshi_trades(
                        None, limit=self._limit, max_pages=self._max_pages,
                        min_ts=min_ts, timeout=self._timeout_seconds,
                    )
                    watched = set(self._tickers)
                    grouped: dict[str, list[dict]] = {t: [] for t in self._tickers}
                    for trade in trades:
                        ticker = str(trade.get("ticker") or trade.get("market_ticker") or "")
                        if ticker in watched:
                            grouped.setdefault(ticker, []).append(trade)
                    iterator = grouped.items()
                else:
                    iterator = []
                    for ticker in self._tickers:
                        if not self._running:
                            return
                        min_ts = None
                        if ticker in prev_max_ts:
                            parsed = _kalshi_unix_seconds(prev_max_ts[ticker])
                            if parsed is not None:
                                min_ts = max(0, parsed - 1)
                        trades = await fetch_kalshi_trades(
                            ticker, limit=self._limit, max_pages=self._max_pages,
                            min_ts=min_ts, timeout=self._timeout_seconds,
                        )
                        iterator.append((ticker, trades))

                for ticker, trades in iterator:
                    # Gap detection: check if oldest trade in this page is newer than
                    # the previous cycle's max, which would indicate the poll window
                    # may have overflowed (missed trades between cycles).
                    if trades and ticker in prev_max_ts:
                        oldest_ts = trades[-1].get("created_time", "")
                        prev_ts = prev_max_ts[ticker]
                        if oldest_ts and prev_ts and oldest_ts > prev_ts:
                            logger.warning(
                                "Kalshi REST poll window may have overflowed for ticker=%s "
                                "(oldest trade in page %r is newer than prev cycle max %r). "
                                "Consider lowering poll_interval or raising "
                                "kalshi_trade_poll_limit/kalshi_trade_poll_max_pages "
                                "(current limit=%d max_pages=%d).",
                                ticker,
                                oldest_ts,
                                prev_ts,
                                self._limit,
                                self._max_pages,
                            )

                    newest_ts: str | None = None
                    for tr in trades:
                        tid = tr.get("trade_id")
                        if tid:
                            tid_str = str(tid)
                            # Record the id in this cycle's seen set (even if we skip
                            # yielding) so it carries into the next cycle's prev_seen.
                            # Per-cycle sets are bounded by the page size (limit), so
                            # they cannot grow unbounded across a long-running daemon.
                            already_seen = (
                                tid_str in cycle_seen[ticker]
                                or tid_str in prev_seen.get(ticker, set())
                            )
                            cycle_seen[ticker].add(tid_str)
                            if already_seen:
                                continue
                        else:
                            logger.warning(
                                "Kalshi trade for ticker=%s has no trade_id; "
                                "relying on payload-hash storage dedup", ticker,
                            )
                        raw = kalshi_trade_to_raw_event(tr, ticker)
                        yield raw

                        # Track max created_time for gap detection.
                        ct = tr.get("created_time", "")
                        if ct and (newest_ts is None or ct > newest_ts):
                            newest_ts = ct

                    if newest_ts:
                        prev_max_ts[ticker] = newest_ts

                    if not self._all_market_poll:
                        await asyncio.sleep(0.1)

                # Promote this cycle's seen set to prev for next cycle.
                prev_seen = cycle_seen
                backoff = self._initial_backoff

            except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as exc:
                if isinstance(exc, aiohttp.ClientResponseError) and exc.status in (429, 503):
                    logger.warning("Kalshi REST rate-limited (status %d): %s", exc.status, exc)
                elif isinstance(exc, (aiohttp.ClientError, asyncio.TimeoutError)):
                    logger.warning("Kalshi REST poll error: %s", exc)
                else:
                    logger.error("Kalshi REST poll error (unexpected): %s", exc)
                sleep_time = backoff * (0.5 + random.random() / 2) if self._reconnect_jitter else backoff
                await asyncio.sleep(sleep_time)
                backoff = min(backoff * 2, self._max_backoff)
                continue

            if not self._running:
                return
            await asyncio.sleep(self._poll_interval_seconds)
