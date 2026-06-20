"""Heartbeat helpers for pmfi daemon health monitoring (US-09).

Pure functions — no DB, no network. Safe to import anywhere.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]

# Default heartbeat path (matches ROOT/reports/health/heartbeat.json)
HEARTBEAT_PATH: Path = ROOT / "reports" / "health" / "heartbeat.json"


def write_heartbeat(
    path: Path,
    *,
    events_total: int,
    alerts_total: int,
    started_at: datetime,
    now: datetime,
    venues: Optional[dict] = None,
    last_recompute_at: Optional[str] = None,
    last_recompute_ok: Optional[bool] = None,
    last_recompute_error: Optional[str] = None,
    partition_maintenance: Optional[dict] = None,
) -> None:
    """Write a heartbeat JSON file atomically (write temp + replace).

    Creates parent directories as needed.

    Optional per-venue map serialized as::

        "venues": {
            "polymarket": {
                "events_total": int,
                "last_event_at": ISO str | null,
                "consecutive_failures": int,
                "last_error": str | null,
                "circuit_open": bool
            },
            ...
        }

    Optional recompute fields (top-level keys):
        last_recompute_at, last_recompute_ok, last_recompute_error

    Optional partition_maintenance carries daemon partition creation/retention
    state for `pmfi health`; it is status-only unless retention was explicitly
    enabled and acknowledged in config.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "ts": now.isoformat(),
        "events_total": events_total,
        "alerts_total": alerts_total,
        "started_at": started_at.isoformat(),
        "pid": os.getpid(),
    }
    if venues is not None:
        payload["venues"] = venues
    if last_recompute_at is not None:
        payload["last_recompute_at"] = last_recompute_at
    if last_recompute_ok is not None:
        payload["last_recompute_ok"] = last_recompute_ok
    if last_recompute_error is not None:
        payload["last_recompute_error"] = last_recompute_error
    if partition_maintenance is not None:
        payload["partition_maintenance"] = partition_maintenance
    # Atomic-ish: write to a sibling temp file then replace.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hb_tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        Path(tmp).replace(path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_heartbeat(path: Path) -> Optional[dict]:
    """Read heartbeat JSON from *path*. Returns None if missing or unparseable."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_heartbeat_ex(path: Path) -> "tuple[Optional[dict], str]":
    """Read heartbeat JSON; distinguish not-found from parse/permission errors.

    Returns (payload, error_kind) where error_kind is one of:
      - "ok"         — file read and parsed successfully
      - "not_found"  — file does not exist
      - "unreadable" — file exists but could not be read or parsed (includes
                       the exception message in a second pass so caller can
                       format a helpful message)

    The payload is None when error_kind != "ok".
    """
    path = Path(path)
    if not path.exists():
        return None, "not_found"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data, "ok"
    except Exception as exc:
        return None, f"unreadable:{exc}"


def heartbeat_age_seconds(hb: dict, now: datetime) -> Optional[float]:
    """Return age in seconds between heartbeat ts and *now*. None if ts is absent/invalid."""
    ts_raw = hb.get("ts")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
        # Ensure both are offset-aware for subtraction.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds()
    except Exception:
        return None


def is_stale(hb: Optional[dict], now: datetime, threshold_seconds: float) -> bool:
    """Return True when the heartbeat is missing or older than *threshold_seconds*."""
    if hb is None:
        return True
    age = heartbeat_age_seconds(hb, now)
    if age is None:
        return True
    return age > threshold_seconds
