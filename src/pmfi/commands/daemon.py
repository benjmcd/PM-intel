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
from typing import Any, Awaitable, Callable, Iterable, Optional

import yaml

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]


def _enabled_fp_review_floors(rules: dict) -> dict[str, int]:
    """Return enabled per-rule review floors from alert_rules.yaml config."""
    rule_map = rules.get("rules") if isinstance(rules, dict) else None
    if not isinstance(rule_map, dict):
        return {}
    floors: dict[str, int] = {}
    for rule_key, cfg in rule_map.items():
        if not isinstance(cfg, dict):
            continue
        if not bool(cfg.get("enabled", True)):
            continue
        if "acceptable_fp_rate_percent" not in cfg:
            continue
        raw_floor = cfg.get("min_reviewed_for_fp_rate_breach")
        if raw_floor is None:
            continue
        try:
            floor = int(raw_floor)
        except (TypeError, ValueError):
            logger.warning(
                "[ingest] invalid min_reviewed_for_fp_rate_breach for rule=%s: %r",
                rule_key,
                raw_floor,
            )
            continue
        if floor > 0:
            floors[str(rule_key)] = floor
    return floors


async def warn_below_fp_review_floors(
    pool: Any,
    rules: dict,
    *,
    context: str = "ingest",
) -> list[dict[str, int | str]]:
    """Warn when enabled alert rules have too few reviewed alerts for FP governance.

    This is deliberately warn-only: the release profile has not authorized runtime
    alert suppression based on review cohort size, but unattended operators should
    see when a rule's FP-rate breach threshold is not yet meaningful.
    """
    floors = _enabled_fp_review_floors(rules)
    if not floors:
        return []
    query = (
        "WITH latest_reviews AS ("
        "SELECT DISTINCT ON (ar.alert_id) ar.alert_id "
        "FROM alert_reviews ar "
        "ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC"
        ") "
        "SELECT a.rule_key, COUNT(*)::int AS reviewed "
        "FROM alerts a "
        "JOIN latest_reviews lr ON lr.alert_id = a.alert_id "
        "WHERE a.rule_key = ANY($1::text[]) "
        "GROUP BY a.rule_key"
    )
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, list(floors))
    except Exception as exc:
        logger.warning("[%s] review-floor check failed (continuing warn-only): %s", context, exc)
        return []
    reviewed_by_rule = {str(row["rule_key"]): int(row["reviewed"]) for row in rows}
    below: list[dict[str, int | str]] = []
    for rule_key, floor in sorted(floors.items()):
        reviewed = reviewed_by_rule.get(rule_key, 0)
        if reviewed >= floor:
            continue
        below.append({"rule_key": rule_key, "reviewed": reviewed, "min_reviewed": floor})
        logger.warning(
            "[%s] rule below FP-rate review floor: rule=%s reviewed=%d "
            "min_reviewed_for_fp_rate_breach=%d; alerts remain enabled (warn-only)",
            context,
            rule_key,
            reviewed,
            floor,
        )
    return below


class RulesFileReloader:
    """Poll alert_rules.yaml and hot-reload an AlertEngine after file changes."""

    def __init__(self, engine: Any, *, rules_path: Path | None = None) -> None:
        self.engine = engine
        self.rules_path = rules_path or ROOT / "config" / "alert_rules.yaml"
        self._last_mtime_ns = self._mtime_ns()

    def _mtime_ns(self) -> int | None:
        try:
            return self.rules_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def check(self) -> bool:
        try:
            current_mtime_ns = self._mtime_ns()
        except OSError as exc:
            logger.warning("[ingest] rules reload stat failed for %s: %s", self.rules_path, exc)
            return False
        if current_mtime_ns == self._last_mtime_ns:
            return False
        self._last_mtime_ns = current_mtime_ns
        if current_mtime_ns is None:
            logger.warning("[ingest] rules file missing; keeping existing in-memory rules")
            return False
        try:
            new_rules = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("[ingest] rules reload read failed for %s: %s", self.rules_path, exc)
            return False
        try:
            reloaded = bool(self.engine.reload_rules(new_rules))
        except Exception as exc:
            logger.warning("[ingest] rules reload failed; keeping existing in-memory rules: %s", exc)
            return False
        if not reloaded:
            logger.warning("[ingest] rules reload rejected invalid config; keeping existing in-memory rules")
            return False
        logger.info("[ingest] rules reloaded from %s", self.rules_path)
        return True


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
    # partition maintenance
    partition_maint_cycles: int,
    ensure_partitions: Callable[..., Awaitable[None]],
    find_old_partitions: Callable[..., Awaitable[list]],
    raw_retention_days: int,
    drop_old_partitions: Optional[Callable[..., Awaitable[list]]] = None,
    retention_enabled: bool = False,
    retention_operator_acknowledged: bool = False,
    partition_state: Optional[dict] = None,
    operational_health_state: Optional[Any] = None,
    operational_health_monitors: Optional[Iterable[Any]] = None,
    operational_health_provider: Optional[Callable[[], dict]] = None,
    current_targets_by_venue: Optional[dict[str, list]] = None,
    current_poly_ids: Optional[list] = None,
    current_kalshi_tickers: Optional[list] = None,
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

    from pmfi.commands._shared import _is_maintenance_cycle

    if partition_state is None:
        partition_state = {}
    retention_active = bool(retention_enabled and retention_operator_acknowledged)
    partition_state.update({
        "retention_enabled": bool(retention_enabled),
        "retention_operator_acknowledged": bool(retention_operator_acknowledged),
        "retention_active": retention_active,
        "raw_retention_days": raw_retention_days,
    })
    partition_state.setdefault("last_checked_at", None)
    partition_state.setdefault("last_ensure_ok", None)
    partition_state.setdefault("last_ensure_error", None)
    partition_state.setdefault("last_retention_check_error", None)
    partition_state.setdefault("old_partitions", [])
    partition_state.setdefault("dropped_partitions", [])
    partition_state.setdefault("last_drop_error", None)

    # US-08/SL-3: partition creation and opt-in retention on cycle 1 and every
    # partition_maint_cycles.  Retention remains no-delete by default: the
    # daemon drops only when both config flags are true.
    if _is_maintenance_cycle(cycle, partition_maint_cycles):
        partition_state["last_checked_at"] = now_utc().isoformat()
        partition_state["dropped_partitions"] = []
        try:
            await ensure_partitions(pool)
            partition_state["last_ensure_ok"] = True
            partition_state["last_ensure_error"] = None
            logger.info("[ingest] partition maintenance: current partitions verified")
        except Exception as _pm_exc:
            partition_state["last_ensure_ok"] = False
            partition_state["last_ensure_error"] = str(_pm_exc)
            logger.warning("[ingest] partition maintenance failed (non-fatal): %s", _pm_exc)

        try:
            old = list(await find_old_partitions(pool, before_days=raw_retention_days))
            partition_state["old_partitions"] = old
            partition_state["last_retention_check_error"] = None
            partition_state["last_drop_error"] = None

            if old and not retention_active:
                if retention_enabled:
                    retention_note = "retention is enabled but not operator-acknowledged"
                else:
                    retention_note = "retention is disabled"
                logger.warning(
                    "[ingest] WARNING: %d partition(s) older than %d days: %s. "
                    "%s; no daemon pruning will run.",
                    len(old), raw_retention_days, ", ".join(old), retention_note,
                )

            if retention_active:
                if drop_old_partitions is None:
                    partition_state["last_drop_error"] = "drop_old_partitions helper unavailable"
                    logger.warning(
                        "[ingest] retention prune skipped: drop_old_partitions helper unavailable"
                    )
                else:
                    try:
                        dropped = list(
                            await drop_old_partitions(pool, before_days=raw_retention_days)
                        )
                        partition_state["dropped_partitions"] = dropped
                        partition_state["old_partitions"] = []
                        partition_state["last_drop_error"] = None
                        if dropped:
                            logger.warning(
                                "[ingest] retention prune dropped %d old partition(s): %s",
                                len(dropped), ", ".join(dropped),
                            )
                        else:
                            logger.info(
                                "[ingest] retention prune: no partitions older than %d days found",
                                raw_retention_days,
                            )
                    except Exception as _drop_exc:
                        partition_state["dropped_partitions"] = []
                        partition_state["last_drop_error"] = str(_drop_exc)
                        logger.warning(
                            "[ingest] retention prune failed (non-fatal): %s",
                            _drop_exc,
                        )
        except Exception as _rw_exc:
            partition_state["old_partitions"] = []
            partition_state["last_retention_check_error"] = str(_rw_exc)
            logger.warning("[ingest] retention check failed (non-fatal): %s", _rw_exc)

    partition_payload = dict(partition_state)
    for _key in ("old_partitions", "dropped_partitions"):
        if _key in partition_payload:
            partition_payload[_key] = list(partition_payload[_key])
    operational_health = None
    if operational_health_state is not None and operational_health_monitors is not None:
        for monitor in operational_health_monitors:
            try:
                await monitor.evaluate(pool, operational_health_state)
            except Exception as _oh_eval_exc:
                logger.warning(
                    "[ingest] operational-health monitor failed (non-fatal): %s",
                    _oh_eval_exc,
                )
    if operational_health_provider is not None:
        try:
            operational_health = operational_health_provider()
        except Exception as _oh_exc:
            logger.warning("[ingest] operational-health snapshot failed (non-fatal): %s", _oh_exc)

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
            partition_maintenance=partition_payload,
            operational_health=operational_health,
        )
    except Exception as _hb_exc:
        logger.warning("[ingest] heartbeat write failed (non-fatal): %s", _hb_exc)

    # Periodic baseline recompute (config-gated, non-fatal)
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
            _new_targets = await refresh_subscriptions(pool, asset_id_map)
            if isinstance(_new_targets, tuple):
                # Legacy test/support path for callers that still provide the
                # old two-list contract; live daemon paths pass the registry
                # keyed target map below.
                _new_targets = {
                    "polymarket": list(_new_targets[0]),
                    "kalshi": list(_new_targets[1]),
                }
            if current_targets_by_venue is None:
                current_targets_by_venue = {}
                if current_poly_ids is not None:
                    current_targets_by_venue["polymarket"] = current_poly_ids
                if current_kalshi_tickers is not None:
                    current_targets_by_venue["kalshi"] = current_kalshi_tickers
            from pmfi.pipeline.venue_dispatch import update_subscription_target_lists

            update_subscription_target_lists(current_targets_by_venue, _new_targets)
            _summary = " ".join(
                f"{venue}_subscriptions={len(targets)}"
                for venue, targets in sorted(current_targets_by_venue.items())
            )
            logger.info("[ingest] subscription map refreshed: %s", _summary)
        except Exception as _map_exc:
            logger.warning("[ingest] subscription map refresh failed (non-fatal): %s", _map_exc)
