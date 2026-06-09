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
) -> None:
    """Write a heartbeat JSON file atomically (write temp + replace).

    Creates parent directories as needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": now.isoformat(),
        "events_total": events_total,
        "alerts_total": alerts_total,
        "started_at": started_at.isoformat(),
        "pid": os.getpid(),
    }
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
