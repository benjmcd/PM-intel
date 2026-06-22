from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pmfi.qualification.capacity import DEFAULT_MANIFEST, run_capacity_measurement


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_capacity_text(evidence: dict[str, Any]) -> str:
    measurements = evidence["measurements"]
    recommended = evidence["recommended_thresholds"]["recommended"]
    lines = [
        f"capacity-measure outcome={evidence['outcome']}",
        f"  pool_acquire_p95_ms={measurements['pool_acquire_p95_ms']} samples={measurements['sample_count']}",
        f"  db_size_bytes={measurements['db_size_bytes']} growth_bytes={measurements['db_growth_bytes']}",
        f"  est_bytes_per_event={measurements['est_bytes_per_event']} runway={measurements['projected_runway_events_or_days']}",
        f"  rto_restart_seconds={measurements['rto_restart_seconds']}",
        f"  rto_restore_seconds={measurements['rto_restore_seconds']}",
        "  recommendations=RECOMMEND_ONLY",
        f"    pool_acquire_wait_p95_alarm_ms={recommended['pool_acquire_wait_p95_alarm_ms']}",
        f"    disk_headroom_min_bytes={recommended['disk_headroom_min_bytes']}",
        f"    disk_headroom_min_fraction={recommended['disk_headroom_min_fraction']}",
        f"    rto_restart_seconds={recommended['rto_restart_seconds']}",
        f"    rto_restore_seconds={recommended['rto_restore_seconds']}",
    ]
    if evidence.get("blocker_or_inconclusive_reason"):
        lines.append(f"  inconclusive_reason={evidence['blocker_or_inconclusive_reason']}")
    return "\n".join(lines)


def cmd_capacity_measure(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool

    manifest = Path(getattr(args, "manifest", None) or DEFAULT_MANIFEST)

    async def _run() -> tuple[dict[str, Any] | None, str | None]:
        cfg = load_config()
        pool = None
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
            evidence = await run_capacity_measurement(
                pool,
                manifest,
                db_url=cfg.database.url,
            )
            return evidence, None
        except Exception as exc:
            return None, str(exc)
        finally:
            if pool is not None:
                await pool.close()

    evidence, error = asyncio.run(_run())
    if error:
        print(f"[capacity-measure] measurement unavailable: {error}")
        print("[capacity-measure] no measurements were emitted; run after local Postgres/Docker is available.")
        return 1
    assert evidence is not None
    if getattr(args, "format", "json") == "text":
        print(format_capacity_text(evidence))
    else:
        print(json.dumps(evidence, indent=2, sort_keys=True, default=_json_default))
    return 0 if evidence["outcome"] == "PASS" else 1
