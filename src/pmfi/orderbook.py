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
    if insert_alert_func is None:
        from pmfi.db.repos.alerts import insert_alert as insert_alert_func

    from pmfi.pipeline.liquidity import assess_liquidity, build_liquidity_decision

    rules = getattr(engine, "_rules", {}).get("rules", {})
    liq_cfg = rules.get("liquidity_wall_v1", {})
    liq_enabled = bool(liq_cfg.get("enabled", True))
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
                    note="periodic orderbook snapshot; Polymarket-only; see ADR-0009",
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
