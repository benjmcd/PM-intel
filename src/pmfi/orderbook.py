"""Orderbook capture: fetch current best bid/ask at trade time."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_POLY_BOOK_URL = "https://clob.polymarket.com/book"

# Simple in-process rate limit: don't re-fetch the same token within N seconds
_last_fetch: dict[str, datetime] = {}
_RATE_LIMIT_SECONDS = 10


def _extract_token_id(payload: dict[str, Any]) -> str | None:
    """Extract the outcome token ID from a raw Polymarket trade payload."""
    return payload.get("asset_id") or payload.get("token_id")


async def fetch_polymarket_book(token_id: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
    """Fetch the current orderbook for a Polymarket outcome token. Returns None on failure."""
    import aiohttp

    now = datetime.now(timezone.utc)
    last = _last_fetch.get(token_id)
    if last is not None and (now - last).total_seconds() < _RATE_LIMIT_SECONDS:
        return None  # rate-limited

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _POLY_BOOK_URL,
                params={"token_id": token_id},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    logger.debug("orderbook fetch got status %d for token %s", resp.status, token_id[:16])
                    return None
                data = await resp.json()
        _last_fetch[token_id] = datetime.now(timezone.utc)
        return data
    except Exception as exc:
        logger.debug("orderbook fetch failed for token %s: %s", token_id[:16], exc)
        return None


def parse_book_levels(raw_book: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Parse bids and asks from a Polymarket /book response into sorted level lists."""
    def _parse_levels(raw_levels: list[dict]) -> list[dict]:
        levels = []
        for entry in (raw_levels or []):
            try:
                levels.append({
                    "price": Decimal(str(entry.get("price", "0"))),
                    "size": Decimal(str(entry.get("size", "0"))),
                })
            except (InvalidOperation, TypeError):
                continue
        return levels

    bids = sorted(_parse_levels(raw_book.get("bids", [])), key=lambda x: x["price"], reverse=True)
    asks = sorted(_parse_levels(raw_book.get("asks", [])), key=lambda x: x["price"])
    return bids, asks


def compute_book_summary(bids: list[dict], asks: list[dict]) -> dict[str, Decimal | None]:
    """Compute best_bid, best_ask, spread, top_depth_usd from sorted level lists."""
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (best_ask - best_bid) if (best_bid and best_ask) else None
    top_bid_usd = sum(b["price"] * b["size"] for b in bids[:3]) if bids else Decimal("0")
    top_ask_usd = sum(a["price"] * a["size"] for a in asks[:3]) if asks else Decimal("0")
    top_depth_usd = top_bid_usd + top_ask_usd
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "top_depth_usd": top_depth_usd,
    }


def parse_kalshi_orderbook(
    raw_book: dict[str, Any],
) -> dict[str, tuple[list[dict], list[dict]]]:
    """Parse Kalshi YES/NO bid ladders into outcome-specific bid/ask books.

    Kalshi exposes bids for each side. In a binary market, a NO bid at price X is
    an implied YES ask at 1-X, and a YES bid at price Y is an implied NO ask at
    1-Y.
    """
    book = raw_book.get("orderbook_fp") or raw_book.get("orderbook") or {}

    def _parse_ladder(raw_levels: list) -> list[dict]:
        levels = []
        for entry in raw_levels or []:
            try:
                price = Decimal(str(entry[0]))
                size = Decimal(str(entry[1]))
            except (IndexError, InvalidOperation, TypeError, ValueError):
                continue
            if price < 0 or price > 1 or size < 0:
                continue
            levels.append({"price": price, "size": size})
        return sorted(levels, key=lambda x: x["price"], reverse=True)

    yes_bids = _parse_ladder(book.get("yes_dollars", []))
    no_bids = _parse_ladder(book.get("no_dollars", []))

    yes_asks = sorted(
        [{"price": Decimal("1") - level["price"], "size": level["size"]} for level in no_bids],
        key=lambda x: x["price"],
    )
    no_asks = sorted(
        [{"price": Decimal("1") - level["price"], "size": level["size"]} for level in yes_bids],
        key=lambda x: x["price"],
    )
    return {
        "yes": (yes_bids, yes_asks),
        "no": (no_bids, no_asks),
    }


@dataclass(frozen=True)
class OrderbookPollResult:
    attempted: int = 0
    fetched: int = 0
    snapshots: int = 0
    alerts: int = 0
    skipped: int = 0


async def poll_polymarket_orderbooks(
    pool: Any,
    *,
    token_ids: list[str] | tuple[str, ...],
    asset_id_map: dict[str, dict],
    engine: Any,
    alert_handler: Any | None = None,
    fetch_book: Any | None = None,
    insert_snapshot: Any | None = None,
    insert_alert_func: Any | None = None,
) -> OrderbookPollResult:
    """Poll current Polymarket books for watched token IDs and store snapshots.

    This removes the liquidity monitor's trade-coupled blind spot without
    adding new durable stores. Callers pass the watched token list and the
    canonical token-to-market map already loaded from ``market_outcomes``.
    """
    if not token_ids:
        return OrderbookPollResult()

    fetch_book = fetch_book or fetch_polymarket_book
    if insert_snapshot is None:
        from pmfi.db.repos.orderbook import insert_orderbook_snapshot as insert_snapshot
    from pmfi.pipeline.liquidity import assess_liquidity, build_liquidity_decision

    rules = getattr(engine, "_rules", {}).get("rules", {})
    liq_cfg = rules.get("liquidity_wall_v1", {})
    liq_enabled = bool(liq_cfg.get("enabled", True))
    if liq_enabled and insert_alert_func is None:
        from pmfi.db.repos.alerts import insert_alert as insert_alert_func
    min_wall_usd = Decimal(str(liq_cfg.get("min_wall_usd", 25000)))
    min_spread_raw = liq_cfg.get("min_spread")
    min_spread = Decimal(str(min_spread_raw)) if min_spread_raw is not None else None
    levels = int(liq_cfg.get("levels", 3))

    attempted = fetched = snapshots = alerts = skipped = 0
    seen: set[str] = set()

    async with pool.acquire() as conn:
        for token_id in token_ids:
            if token_id in seen:
                continue
            seen.add(token_id)
            info = asset_id_map.get(token_id) or {}
            if info.get("venue_code") != "polymarket" or not info.get("market_id"):
                skipped += 1
                continue

            attempted += 1
            try:
                raw_book = await fetch_book(token_id)
            except Exception as exc:
                logger.debug("periodic orderbook poll failed for token %s: %s", token_id[:16], exc)
                continue
            if raw_book is None:
                continue

            try:
                fetched += 1
                bids, asks = parse_book_levels(raw_book)
                summary = compute_book_summary(bids, asks)
                market_id = str(info["market_id"])
                outcome_key = str(info.get("outcome_key") or "unknown")
                venue_market_id = str(info.get("venue_market_id") or market_id)
                await insert_snapshot(
                    conn,
                    venue_code="polymarket",
                    market_id=market_id,
                    bids=bids,
                    asks=asks,
                    outcome_key=outcome_key,
                    is_reconstructed=False,
                    payload=raw_book,
                    **summary,
                )
                snapshots += 1

                if not liq_enabled:
                    continue
                finding = assess_liquidity(
                    bids,
                    asks,
                    min_wall_usd=min_wall_usd,
                    min_spread=min_spread,
                    levels=levels,
                )
                if finding is None:
                    continue
                decision = build_liquidity_decision(
                    finding,
                    outcome_key=outcome_key,
                    note="periodic Polymarket orderbook snapshot; see ADR-0009",
                )
                alert_id = await insert_alert_func(
                    conn,
                    decision,
                    title=f"liquidity_{finding['kind']} on {venue_market_id}",
                    summary=f"{decision.severity}: {finding['wall_side']} wall {finding['wall_usd']} USD",
                    venue_code="polymarket",
                    market_id=market_id,
                    outcome_key=outcome_key,
                    dedupe_context=f"orderbook_poll:{token_id}",
                )
                if alert_id:
                    alerts += 1
                    if alert_handler is not None:
                        try:
                            await alert_handler(decision, "polymarket", market_id)
                        except Exception as exc:
                            logger.debug("periodic orderbook alert delivery failed for token %s: %s", token_id[:16], exc)
            except Exception as exc:
                logger.debug("periodic orderbook processing failed for token %s: %s", token_id[:16], exc)
                continue

    return OrderbookPollResult(
        attempted=attempted,
        fetched=fetched,
        snapshots=snapshots,
        alerts=alerts,
        skipped=skipped,
    )


async def poll_kalshi_orderbooks(
    pool: Any,
    *,
    tickers: list[str] | tuple[str, ...],
    engine: Any,
    alert_handler: Any | None = None,
    fetch_book: Any | None = None,
    insert_snapshot: Any | None = None,
    insert_alert_func: Any | None = None,
) -> OrderbookPollResult:
    """Poll Kalshi REST orderbooks for watched tickers and store snapshots."""
    if not tickers:
        return OrderbookPollResult()

    if fetch_book is None:
        from pmfi.markets import fetch_kalshi_orderbook as fetch_book
    if insert_snapshot is None:
        from pmfi.db.repos.orderbook import insert_orderbook_snapshot as insert_snapshot

    from pmfi.pipeline.liquidity import assess_liquidity, build_liquidity_decision

    rules = getattr(engine, "_rules", {}).get("rules", {})
    liq_cfg = rules.get("liquidity_wall_v1", {})
    liq_enabled = bool(liq_cfg.get("enabled", True))
    if liq_enabled and insert_alert_func is None:
        from pmfi.db.repos.alerts import insert_alert as insert_alert_func
    min_wall_usd = Decimal(str(liq_cfg.get("min_wall_usd", 25000)))
    min_spread_raw = liq_cfg.get("min_spread")
    min_spread = Decimal(str(min_spread_raw)) if min_spread_raw is not None else None
    levels = int(liq_cfg.get("levels", 3))

    unique_tickers = list(dict.fromkeys(tickers))
    attempted = fetched = snapshots = alerts = skipped = 0

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT market_id::text, venue_market_id
               FROM markets
               WHERE venue_code = 'kalshi'
                 AND venue_market_id = ANY($1::text[])""",
            unique_tickers,
        )
        market_ids = {row["venue_market_id"]: str(row["market_id"]) for row in rows}

        for ticker in unique_tickers:
            market_id = market_ids.get(ticker)
            if not market_id:
                skipped += 1
                continue

            attempted += 1
            try:
                raw_book = await fetch_book(ticker)
            except Exception as exc:
                logger.debug("periodic Kalshi orderbook poll failed for ticker %s: %s", ticker, exc)
                continue
            if raw_book is None:
                continue

            fetched += 1
            try:
                outcome_books = parse_kalshi_orderbook(raw_book)
            except Exception as exc:
                logger.debug("periodic Kalshi orderbook parse failed for ticker %s: %s", ticker, exc)
                continue

            for outcome_key, (bids, asks) in outcome_books.items():
                try:
                    summary = compute_book_summary(bids, asks)
                    await insert_snapshot(
                        conn,
                        venue_code="kalshi",
                        market_id=market_id,
                        bids=bids,
                        asks=asks,
                        outcome_key=outcome_key,
                        is_reconstructed=True,
                        payload=raw_book,
                        **summary,
                    )
                    snapshots += 1

                    if not liq_enabled:
                        continue
                    finding = assess_liquidity(
                        bids,
                        asks,
                        min_wall_usd=min_wall_usd,
                        min_spread=min_spread,
                        levels=levels,
                    )
                    if finding is None:
                        continue
                    decision = build_liquidity_decision(
                        finding,
                        outcome_key=outcome_key,
                        note=(
                            "periodic Kalshi orderbook snapshot with implied asks "
                            "from complementary bids; see ADR-0009"
                        ),
                    )
                    alert_id = await insert_alert_func(
                        conn,
                        decision,
                        title=f"liquidity_{finding['kind']} on {ticker}",
                        summary=f"{decision.severity}: {finding['wall_side']} wall {finding['wall_usd']} USD",
                        venue_code="kalshi",
                        market_id=market_id,
                        outcome_key=outcome_key,
                        dedupe_context=f"kalshi_orderbook_poll:{ticker}:{outcome_key}",
                    )
                    if alert_id:
                        alerts += 1
                        if alert_handler is not None:
                            try:
                                await alert_handler(decision, "kalshi", market_id)
                            except Exception as exc:
                                logger.debug("periodic Kalshi orderbook alert delivery failed for ticker %s: %s", ticker, exc)
                except Exception as exc:
                    logger.debug(
                        "periodic Kalshi orderbook processing failed for ticker %s outcome %s: %s",
                        ticker,
                        outcome_key,
                        exc,
                    )
                    continue

    return OrderbookPollResult(
        attempted=attempted,
        fetched=fetched,
        snapshots=snapshots,
        alerts=alerts,
        skipped=skipped,
    )
