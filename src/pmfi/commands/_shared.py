"""Shared helpers used by multiple command modules.

These functions are pure (no I/O) and import only from pmfi.* — never from
pmfi.cli — to avoid circular imports.  cli.py also re-imports them so that
existing test patches on pmfi.cli.* still resolve.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


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


async def _safe_recompute_baselines(pool, *, window_days: int, min_samples: int) -> "int | None":
    """Call compute_and_store_baselines and return the number of entries written.

    Any exception is caught, a non-fatal message is printed, and None is returned
    so the calling loop continues uninterrupted.
    """
    from pmfi.baseline import compute_and_store_baselines
    try:
        result = await compute_and_store_baselines(pool, window_days=window_days, min_samples=min_samples)
        return len(result)
    except Exception as exc:
        print(f"[ingest] baseline recompute failed (non-fatal): {exc}")
        return None


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
