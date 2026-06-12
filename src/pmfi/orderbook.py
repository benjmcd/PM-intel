"""Orderbook capture: fetch current best bid/ask at trade time."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
                    _log = logger.warning if resp.status in (429, 503) else logger.debug
                    _log("orderbook fetch status %d for token %s", resp.status, token_id[:16])
                    return None
                data = await resp.json()
        _last_fetch[token_id] = datetime.now(timezone.utc)
        return data
    except Exception as exc:
        logger.warning("orderbook fetch failed for token %s: %s", token_id[:16], exc)
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
