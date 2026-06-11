"""Shared helpers used by multiple command modules.

These functions are pure (no I/O) and import only from pmfi.* — never from
pmfi.cli — to avoid circular imports.  cli.py also re-imports them so that
existing test patches on pmfi.cli.* still resolve.
"""
from __future__ import annotations

import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)


def _is_maintenance_cycle(cycle: int, every: int) -> bool:
    """Return True when *cycle* should trigger maintenance.

    Fires on cycle 1 (first interval after startup) and then every *every* cycles.
    """
    return cycle == 1 or (cycle % every == 0)


def _cycles_from_minutes(minutes: int, interval_seconds: int) -> int:
    """Convert a minutes-based interval to a cycle count given a loop interval in seconds.

    Returns at least 1 so the caller never divides by zero or waits forever.
    """
    return max(1, round(minutes * 60 / interval_seconds))


async def _safe_recompute_baselines(
    pool, *, window_days: int, min_samples: int
) -> "tuple[int | None, str | None]":
    """Call compute_and_store_baselines; return (count, error_str).

    On success: (len(result), None).
    On failure: (None, str(exc)) — exception is swallowed so the calling loop
    continues uninterrupted.  A non-fatal WARNING is always logged on failure.
    """
    from pmfi.baseline import compute_and_store_baselines
    try:
        result = await compute_and_store_baselines(pool, window_days=window_days, min_samples=min_samples)
        return len(result), None
    except Exception as exc:
        logger.warning("[ingest] baseline recompute failed (non-fatal): %s", exc)
        return None, str(exc)


def _delivery_banner(mode: str, destination: str) -> str:
    """Return a multi-line startup banner describing the active alert delivery mode.

    Pure helper (no I/O) so it can be unit-tested directly.
    """
    lines = [
        "=" * 60,
        "[ingest] ALERT DELIVERY",
        f"  mode        : {mode}",
        f"  destination : {destination}",
    ]
    if mode == "file":
        lines += [
            "  Alerts are written durably to the path above.",
            "  They are ALSO always stored in the DB (insert_alert).",
        ]
    elif mode == "localhost_http_receiver":
        lines += [
            "  Alerts are POSTed to the local HTTP receiver above.",
            "  They are ALSO always stored in the DB (insert_alert).",
        ]
    else:
        lines += [
            "  WARNING: console mode is EPHEMERAL — alerts printed here",
            "  are lost when this terminal closes.",
            "  Recommend: set alerts.default_delivery: file in app.yaml",
            "  for durable on-disk storage.",
        ]
    lines += [
        "  Alert history is always queryable regardless of delivery mode:",
        "    pmfi alerts list   — recent alerts from DB",
        "    pmfi watch         — live tail from DB",
        "    pmfi dashboard     — browser view (localhost)",
        "=" * 60,
    ]
    return "\n".join(lines)


def _resolve_poly_token_ids(
    watched: list[dict],
    asset_id_map: dict[str, dict],
) -> list[str]:
    """Return Polymarket token IDs for watched markets resolved from market_outcomes.

    Returns an empty list when no outcomes have been synced yet (caller must decide
    whether to error or skip rather than falling back to condition IDs).
    """
    watched_poly_market_ids = {m["market_id"] for m in watched if m["venue_code"] == "polymarket"}
    return [
        token_id for token_id, info in asset_id_map.items()
        if info["venue_code"] == "polymarket" and info["market_id"] in watched_poly_market_ids
    ]


async def _refresh_subscriptions(
    pool,
    asset_id_map: dict,
) -> "tuple[list[str], list[str]]":
    """Re-read watched markets and asset_id_map from the DB; update asset_id_map in-place.

    Returns (poly_ids, kalshi_tickers) derived from the fresh DB state.

    Non-fatal contract: callers should catch Exception and retain previous values on
    failure.  This helper does NOT mutate the inputs on failure — the caller is
    responsible for the try/except guard to preserve the previous subscription state.

    The asset_id_map dict is updated in-place so that any consumer holding a
    reference to the same dict (e.g. a running pipeline) sees the refreshed mapping
    without needing to be restarted.
    """
    from pmfi.db.repos.markets import fetch_watched_markets
    from pmfi.markets import load_asset_id_mapping

    async with pool.acquire() as conn:
        watched = await fetch_watched_markets(conn)

    fresh_map = await load_asset_id_mapping(pool)
    # Update in-place: remove stale keys, add/update current ones.
    stale_keys = set(asset_id_map) - set(fresh_map)
    for k in stale_keys:
        del asset_id_map[k]
    asset_id_map.update(fresh_map)

    poly_ids = _resolve_poly_token_ids(watched, asset_id_map)
    kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]
    return poly_ids, kalshi_tickers


def _select_ingest_venues(
    venues: list[str],
    poly_ids: list[str],
    kalshi_tickers: list[str],
) -> "tuple[list[str], list[str]]":
    """Select enabled venues that have usable subscription targets; drop the rest.

    Pure function — no I/O. Returns (usable_venues, messages). A venue with no
    resolved targets is dropped with an informational message, so an operator
    running both venues but watching only one still ingests the usable venue
    instead of hard-failing. The caller hard-fails only when nothing is usable.
    """
    usable: list[str] = []
    messages: list[str] = []
    for v in venues:
        if v == "polymarket" and not poly_ids:
            messages.append(
                "Polymarket enabled but no token IDs resolved for watched markets; "
                "skipping it. Run 'pmfi markets discover --venue polymarket' then "
                "'pmfi markets watch <market_id>'."
            )
        elif v == "kalshi" and not kalshi_tickers:
            messages.append(
                "Kalshi enabled but no tickers among watched markets; skipping it. "
                "Run 'pmfi markets discover --venue kalshi' then "
                "'pmfi markets watch <market_id> --venue kalshi'."
            )
        else:
            usable.append(v)
    return usable, messages


def _should_poll_orderbooks(
    *,
    orderbook_enabled: bool,
    live_venues: list[str] | tuple[str, ...],
    venue_code: str = "polymarket",
) -> bool:
    """Return True when ingest may make periodic orderbook calls for a venue."""
    return orderbook_enabled and venue_code in live_venues
