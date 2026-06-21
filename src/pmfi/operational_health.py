from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_STATUS_RANK = {"OK": 0, "DEGRADED": 1, "HALTED": 2}


class OperationalHealthState:
    """Mutable local daemon operational-health state."""

    def __init__(self) -> None:
        self._reasons: dict[str, dict[str, Any]] = {}

    def set_reason(
        self,
        reason: str,
        *,
        status: str,
        message: str,
        blocks_intake: bool,
        observed: dict[str, Any] | None = None,
        threshold: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        status = status.upper()
        if status not in _STATUS_RANK or status == "OK":
            raise ValueError(f"operational reason status must be DEGRADED or HALTED, got {status!r}")
        if now is None:
            now = datetime.now(timezone.utc)
        self._reasons[reason] = {
            "reason": reason,
            "status": status,
            "message": message,
            "blocks_intake": bool(blocks_intake),
            "observed": observed or {},
            "threshold": threshold or {},
            "updated_at": now.isoformat(),
        }
        return self.snapshot()

    def clear_reason(self, reason: str) -> dict[str, Any]:
        self._reasons.pop(reason, None)
        return self.snapshot()

    @property
    def intake_allowed(self) -> bool:
        return not any(bool(item.get("blocks_intake")) for item in self._reasons.values())

    def snapshot(self) -> dict[str, Any]:
        reasons = sorted(self._reasons.values(), key=lambda item: str(item.get("reason")))
        status = "OK"
        for item in reasons:
            candidate = str(item.get("status", "OK")).upper()
            if _STATUS_RANK.get(candidate, 0) > _STATUS_RANK[status]:
                status = candidate
        return {
            "status": status,
            "intake_allowed": self.intake_allowed,
            "reasons": [dict(item) for item in reasons],
        }


class DiskHeadroomGuard:
    """Evaluate local repo/runtime volume free space against configured thresholds."""

    reason = "disk_low"

    def __init__(
        self,
        *,
        path: Path,
        min_bytes: int,
        min_fraction: float,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    ) -> None:
        self.path = Path(path)
        self.min_bytes = max(0, int(min_bytes))
        self.min_fraction = max(0.0, float(min_fraction))
        self._disk_usage = disk_usage

    def evaluate(self, state: OperationalHealthState) -> dict[str, Any]:
        try:
            usage = self._disk_usage(self.path)
        except OSError as exc:
            return state.set_reason(
                self.reason,
                status="DEGRADED",
                message=f"disk headroom check failed for {self.path}: {exc}",
                blocks_intake=True,
                observed={"path": str(self.path), "error": str(exc)},
                threshold={
                    "min_bytes": self.min_bytes,
                    "min_fraction": self.min_fraction,
                },
            )
        total = int(usage.total)
        free = int(usage.free)
        threshold_free = max(self.min_bytes, int(total * self.min_fraction))
        payload_threshold = {
            "free_bytes": threshold_free,
            "min_bytes": self.min_bytes,
            "min_fraction": self.min_fraction,
        }
        payload_observed = {
            "path": str(self.path),
            "total_bytes": total,
            "free_bytes": free,
        }
        if free < threshold_free:
            return state.set_reason(
                self.reason,
                status="DEGRADED",
                message=(
                    f"disk free bytes below threshold on {self.path}: "
                    f"free={free} threshold={threshold_free}"
                ),
                blocks_intake=True,
                observed=payload_observed,
                threshold=payload_threshold,
            )
        return state.clear_reason(self.reason)


class DeadLetterRateGuard:
    """Evaluate the recent dead-letter ratio from durable DB counts."""

    reason = "dead_letter_rate_high"

    def __init__(self, *, threshold_fraction: float, lookback_seconds: int = 3600) -> None:
        self.threshold_fraction = max(0.0, float(threshold_fraction))
        self.lookback_seconds = max(1, int(lookback_seconds))

    async def evaluate(self, pool: Any, state: OperationalHealthState) -> dict[str, Any]:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  (
                    SELECT COUNT(*)::bigint
                    FROM raw_events
                    WHERE received_at >= now() - make_interval(secs => $1::double precision)
                  ) AS raw_events,
                  (
                    SELECT COUNT(*)::bigint
                    FROM dead_letters
                    WHERE created_at >= now() - make_interval(secs => $1::double precision)
                  ) AS dead_letters
                """,
                float(self.lookback_seconds),
            )
        raw_events = int(row["raw_events"] or 0)
        dead_letters = int(row["dead_letters"] or 0)
        if raw_events <= 0:
            if dead_letters <= 0:
                return state.clear_reason(self.reason)
            rate: float | None = None
            breached = True
        else:
            rate = dead_letters / raw_events
            breached = rate > self.threshold_fraction
        observed: dict[str, Any] = {
            "raw_events_1h": raw_events,
            "dead_letters_1h": dead_letters,
            "rate": rate,
            "lookback_seconds": self.lookback_seconds,
        }
        threshold = {"max_fraction": self.threshold_fraction}
        if breached:
            return state.set_reason(
                self.reason,
                status="DEGRADED",
                message=(
                    "dead-letter rate exceeded threshold: "
                    f"dead_letters={dead_letters} raw_events={raw_events} "
                    f"threshold={self.threshold_fraction}"
                ),
                blocks_intake=False,
                observed=observed,
                threshold=threshold,
            )
        return state.clear_reason(self.reason)


class UnresolvedDeadLetterHaltGuard:
    """Halt new intake when unresolved dead letters exceed the configured cap."""

    reason = "unresolved_dead_letters_over_cap"

    def __init__(self, *, max_unresolved: int) -> None:
        self.max_unresolved = max(0, int(max_unresolved))

    async def evaluate(self, pool: Any, state: OperationalHealthState) -> dict[str, Any]:
        async with pool.acquire() as conn:
            unresolved = int(
                await conn.fetchval(
                    "SELECT COUNT(*)::bigint FROM dead_letters WHERE resolved = false"
                )
                or 0
            )
        observed = {"unresolved_dead_letters": unresolved}
        threshold = {"max_unresolved": self.max_unresolved}
        if unresolved > self.max_unresolved:
            return state.set_reason(
                self.reason,
                status="HALTED",
                message=(
                    "unresolved dead-letter count exceeded threshold: "
                    f"unresolved={unresolved} threshold={self.max_unresolved}"
                ),
                blocks_intake=True,
                observed=observed,
                threshold=threshold,
            )
        return state.clear_reason(self.reason)


async def guarded_source(
    source: AsyncIterator[Any],
    *,
    state: OperationalHealthState,
    intake_guards: Iterable[Any],
    shutdown: asyncio.Event,
    sleep_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[Any]:
    """Yield source items only while operational guards allow new intake."""

    iterator = source.__aiter__()
    while not shutdown.is_set():
        while not shutdown.is_set():
            for guard in intake_guards:
                guard.evaluate(state)
            if state.intake_allowed:
                break
            await sleep(max(0.0, float(sleep_seconds)))
        if shutdown.is_set():
            break
        try:
            yield await iterator.__anext__()
        except StopAsyncIteration:
            break
