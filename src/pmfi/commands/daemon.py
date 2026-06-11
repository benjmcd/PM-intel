"""Daemon loop helpers extracted for testability.

The per-cycle body of the telemetry loop is isolated here so it can be
exercised in offline tests without needing to drive the full cmd_ingest
closure.  cli.py keeps the while/wait_for shell and calls _telemetry_tick
on each iteration.

All dependencies are passed explicitly so the function has no hidden closure
state and is trivially mockable.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


async def _telemetry_tick(
    *,
    cycle: int,
    events_total: int,
    alerts_total: int,
    delta: int,
    interval: int,
    # heartbeat
    hb_path: Path,
    write_heartbeat: Callable[..., None],
    started_at: datetime,
    build_venues_payload: Callable[[], dict],
    recompute_state: dict,
    # baseline recompute
    recompute_enabled: bool,
    recompute_cycles: int,
    safe_recompute_baselines: Callable[..., Awaitable[Any]],
    pool: Any,
    window_days: int,
    min_samples: int,
    # baseline refresh
    baseline_refresh_cycles: int,
    load_baselines: Callable[..., Awaitable[dict]],
    engine: Any,
    # subscription map refresh
    map_refresh_cycles: int,
    refresh_subscriptions: Callable[..., Awaitable[Any]],
    asset_id_map: dict,
    current_poly_ids: list,
    current_kalshi_tickers: list,
    # partition maintenance
    partition_maint_cycles: int,
    ensure_partitions: Callable[..., Awaitable[None]],
    find_old_partitions: Callable[..., Awaitable[list]],
    raw_retention_days: int,
    # data-quality monitoring
    data_quality_enabled: bool = True,
    venue_stale_seconds: int = 600,
    dead_letter_spike_min: int = 5,
    dead_letter_spike_ratio: float = 3.0,
    data_quality_monitor_cycles: int = 10,
    # time helpers (injectable for tests)
    now_utc: Optional[Callable[[], datetime]] = None,
) -> None:
    """Execute one telemetry cycle.

    This function is NEVER expected to raise — all inner helpers are wrapped
    in non-fatal try/except blocks that log warnings and continue.  If any
    block does propagate an exception it is a bug; the test suite explicitly
    covers that promise.

    *now_utc* defaults to ``datetime.now(timezone.utc)`` and is injectable
    for deterministic tests.
    """
    from datetime import timezone

    if now_utc is None:
        def now_utc() -> datetime:  # type: ignore[misc]
            return datetime.now(timezone.utc)

    logger.info(
        "[ingest] events_total=%d (+%d/%ds) alerts_total=%d",
        events_total, delta, interval, alerts_total,
    )

    # US-09: write heartbeat every cycle (non-fatal)
    try:
        write_heartbeat(
            hb_path,
            events_total=events_total,
            alerts_total=alerts_total,
            started_at=started_at,
            now=now_utc(),
            venues=build_venues_payload(),
            last_recompute_at=recompute_state["last_recompute_at"],
            last_recompute_ok=recompute_state["last_recompute_ok"],
            last_recompute_error=recompute_state["last_recompute_error"],
        )
    except Exception as _hb_exc:
        logger.warning("[ingest] heartbeat write failed (non-fatal): %s", _hb_exc)

    # Periodic baseline recompute (config-gated, non-fatal)
    from pmfi.commands._shared import _is_maintenance_cycle
    if recompute_enabled and _is_maintenance_cycle(cycle, recompute_cycles):
        try:
            _n, _rerr = await safe_recompute_baselines(
                pool,
                window_days=window_days,
                min_samples=min_samples,
            )
        except Exception as _r_exc:
            # safe_recompute_baselines is designed not to raise, but the tick
            # must never kill the daemon (FIRST_EXCEPTION) on a helper bug.
            _n, _rerr = None, str(_r_exc)
            logger.warning("[ingest] baseline recompute failed (non-fatal): %s", _r_exc)
        recompute_state["last_recompute_at"] = now_utc().isoformat()
        recompute_state["last_recompute_ok"] = _rerr is None
        recompute_state["last_recompute_error"] = _rerr
        if _n is not None:
            logger.info("[ingest] baseline recompute: %d market(s) updated", _n)

    # Baseline refresh every N cycles
    if cycle % baseline_refresh_cycles == 0:
        try:
            fresh = await load_baselines(pool)
            engine.update_baselines(fresh)
            logger.info("[ingest] baselines refreshed (%d market(s))", len(fresh))
        except Exception as _bl_exc:
            logger.warning("[ingest] baseline refresh failed (non-fatal): %s", _bl_exc)

    # Subscription map refresh every N cycles
    if cycle % map_refresh_cycles == 0:
        try:
            _new_poly, _new_kalshi = await refresh_subscriptions(pool, asset_id_map)
            current_poly_ids[:] = _new_poly
            current_kalshi_tickers[:] = _new_kalshi
            logger.info(
                "[ingest] subscription map refreshed: poly_tokens=%d kalshi_tickers=%d",
                len(current_poly_ids), len(current_kalshi_tickers),
            )
        except Exception as _map_exc:
            logger.warning("[ingest] subscription map refresh failed (non-fatal): %s", _map_exc)

    # US-08: partition maintenance on cycle 1 and every partition_maint_cycles
    if _is_maintenance_cycle(cycle, partition_maint_cycles):
        try:
            await ensure_partitions(pool)
            logger.info("[ingest] partition maintenance: current partitions verified")
        except Exception as _pm_exc:
            logger.warning("[ingest] partition maintenance failed (non-fatal): %s", _pm_exc)
        # US-08: retention WARNING (read-only, never auto-drops)
        try:
            old = await find_old_partitions(pool, before_days=raw_retention_days)
            if old:
                logger.warning(
                    "[ingest] WARNING: %d partition(s) older than %d days: %s. "
                    "Run 'pmfi db-maintenance --prune-old-partitions' to reclaim space.",
                    len(old), raw_retention_days, ", ".join(old),
                )
        except Exception as _rw_exc:
            logger.warning("[ingest] retention check failed (non-fatal): %s", _rw_exc)

    # Data-quality monitor: runs every data_quality_monitor_cycles (non-fatal)
    if data_quality_enabled and _is_maintenance_cycle(cycle, data_quality_monitor_cycles):
        try:
            from pmfi.monitoring import run_monitors
            await run_monitors(
                pool,
                now=now_utc(),
                venue_stale_seconds=venue_stale_seconds,
                dead_letter_spike_min=dead_letter_spike_min,
                dead_letter_spike_ratio=dead_letter_spike_ratio,
            )
        except Exception as _dq_exc:
            logger.warning("[ingest] data_quality monitor failed (non-fatal): %s", _dq_exc)
