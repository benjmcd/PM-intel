"""Cross-venue divergence monitor (cross_venue_divergence_v1).

Compares the latest price of operator-curated matched markets (the market_aliases
table) and emits an alert when the cross-venue price spread exceeds a threshold.

Matching is MANUAL/reviewed only — there is no automatic title matching here, by
design (see experiments/03_cross_venue_matching.md and
docs/MANUAL_CROSS_VENUE_MATCHING.md). Populate matches with `pmfi markets link`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from pmfi.domain import AlertDecision
from pmfi.monitoring.base import emit_monitor_alert

logger = logging.getLogger(__name__)


async def _latest_price(conn, market_id: str):
    return await conn.fetchval(
        "SELECT last_price FROM market_snapshots "
        "WHERE market_id = $1::uuid AND last_price IS NOT NULL "
        "ORDER BY captured_at DESC LIMIT 1",
        market_id,
    )


async def check_cross_venue_divergence(
    pool: Any,
    *,
    now: datetime,
    min_spread_cents: Decimal = Decimal("3"),
    min_alias_confidence: Decimal = Decimal("0.7"),
) -> list[dict]:
    """Emit cross_venue_divergence_v1 alerts for active aliases whose latest prices diverge."""
    emitted: list[dict] = []
    async with pool.acquire() as conn:
        aliases = await conn.fetch(
            """
            SELECT a.source_market_id::text AS source_id,
                   a.target_market_id::text AS target_id,
                   a.confidence,
                   sm.venue_code AS source_venue,
                   COALESCE(sm.title, sm.venue_market_id) AS source_title,
                   tm.venue_code AS target_venue,
                   COALESCE(tm.title, tm.venue_market_id) AS target_title
              FROM market_aliases a
              JOIN markets sm ON sm.market_id = a.source_market_id
              JOIN markets tm ON tm.market_id = a.target_market_id
             WHERE a.is_active AND a.confidence >= $1
            """,
            min_alias_confidence,
        )
        for al in aliases:
            src_price = await _latest_price(conn, al["source_id"])
            tgt_price = await _latest_price(conn, al["target_id"])
            if src_price is None or tgt_price is None:
                continue
            spread_cents = abs(Decimal(str(src_price)) - Decimal(str(tgt_price))) * Decimal("100")
            if spread_cents < min_spread_cents:
                continue
            severity = "high" if spread_cents >= min_spread_cents * Decimal("2") else "medium"
            confidence = "high" if Decimal(str(al["confidence"])) >= Decimal("0.9") else "medium"
            decision = AlertDecision(
                emit_alert=True,
                rule_id="cross_venue_divergence_v1",
                rule_version="alert_rules.v1",
                severity=severity,
                confidence=confidence,
                score=Decimal("0.8"),
                reason_codes=("cross_venue_price_divergence",),
                evidence={
                    "source_venue": al["source_venue"],
                    "source_market": al["source_title"],
                    "source_last_price": str(src_price),
                    "target_venue": al["target_venue"],
                    "target_market": al["target_title"],
                    "target_last_price": str(tgt_price),
                    "spread_cents": f"{spread_cents:.2f}",
                    "min_spread_cents": str(min_spread_cents),
                    "alias_confidence": str(al["confidence"]),
                    "source": "market_snapshots(latest)",
                },
                data_quality="verified",
            )
            title = f"cross_venue_divergence on {al['source_title']}"
            summary = (
                f"{severity}: {al['source_venue']} vs {al['target_venue']} "
                f"spread {spread_cents:.1f}c"
            )
            alert_id = await emit_monitor_alert(
                conn, decision, title=title, summary=summary,
                venue_code=al["source_venue"], market_id=al["source_id"],
            )
            if alert_id:
                emitted.append({"alert_id": alert_id, "spread_cents": str(spread_cents)})
    return emitted
