"""Liquidity wall/vacuum assessment from a captured orderbook snapshot.

A "wall" is a large resting order near the top of book; a "vacuum" is an abnormally
wide spread (thin book). This runs only on the opt-in orderbook-capture path
(`pmfi live --orderbook` / capture_orderbook=True), after a snapshot is stored.

CAVEATS (see docs/adr/0009-liquidity-wall-detection-scope.md):
- Snapshots are trade-coupled — a wall that forms during a quiet period (no trades)
  is not observed.
- Polymarket-only (Kalshi has no orderbook capture).
- Truncated to the levels the /book endpoint returns.
So treat these as a prompt to investigate, not a confirmed signal.
"""
from __future__ import annotations

from decimal import Decimal


def assess_liquidity(
    bids: list[dict],
    asks: list[dict],
    *,
    min_wall_usd: Decimal,
    min_spread: Decimal | None = None,
    levels: int = 3,
) -> dict | None:
    """Return a wall/vacuum finding dict, or None when the book looks normal.

    Wall: top-`levels` resting USD on the heavier side >= min_wall_usd.
    Vacuum: spread >= min_spread (only checked when min_spread is provided).
    """
    top_bid_usd = sum((b["price"] * b["size"] for b in bids[:levels]), Decimal("0"))
    top_ask_usd = sum((a["price"] * a["size"] for a in asks[:levels]), Decimal("0"))
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

    wall_side = "bid" if top_bid_usd >= top_ask_usd else "ask"
    wall_usd = max(top_bid_usd, top_ask_usd)

    is_wall = wall_usd >= min_wall_usd
    is_vacuum = min_spread is not None and spread is not None and spread >= min_spread

    if not (is_wall or is_vacuum):
        return None

    return {
        "kind": "wall" if is_wall else "vacuum",
        "wall_side": wall_side,
        "wall_usd": wall_usd,
        "top_bid_usd": top_bid_usd,
        "top_ask_usd": top_ask_usd,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "levels": levels,
    }


def build_liquidity_decision(finding: dict, *, outcome_key: str | None):
    """Build an AlertDecision (liquidity_wall_v1) from an assess_liquidity finding."""
    from pmfi.domain import AlertDecision

    severity = "high" if finding["kind"] == "wall" else "medium"
    return AlertDecision(
        emit_alert=True,
        rule_id="liquidity_wall_v1",
        rule_version="alert_rules.v1",
        severity=severity,
        confidence="medium",
        score=Decimal("0.6"),
        reason_codes=(f"liquidity_{finding['kind']}",),
        evidence={
            "kind": finding["kind"],
            "wall_side": finding["wall_side"],
            "wall_usd": str(finding["wall_usd"]),
            "top_bid_usd": str(finding["top_bid_usd"]),
            "top_ask_usd": str(finding["top_ask_usd"]),
            "best_bid": str(finding["best_bid"]),
            "best_ask": str(finding["best_ask"]),
            "spread": str(finding["spread"]),
            "outcome_key": outcome_key,
            "note": "orderbook snapshot is trade-coupled; Polymarket-only; see ADR-0009",
        },
        data_quality="orderbook_snapshot",
    )
