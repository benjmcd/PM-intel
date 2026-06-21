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
