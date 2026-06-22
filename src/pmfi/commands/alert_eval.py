from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pmfi.qualification.alert_precision import DEFAULT_MANIFEST, run_alert_precision_measurement


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def format_alert_eval_text(evidence: dict[str, Any]) -> str:
    measurements = evidence["measurements"]
    lines = [
        f"alert-eval outcome={evidence['outcome']}",
        f"  metric={measurements['metric_name']} completeness={evidence['completeness_classifications']['precision']}",
        f"  alerts={measurements['alert_count']} scorable={measurements['scorable_alerts']} insufficient={measurements['insufficient_alerts']}",
        f"  overall_precision_at_proxy={measurements['overall_precision_at_proxy']}",
        "  recommendations=RECOMMEND_ONLY",
    ]
    for row in measurements.get("per_rule_grid", []):
        lines.append(
            "    "
            f"{row['rule_key']} window={row['window_seconds']}s threshold={row['threshold']} "
            f"precision_at_proxy={row['precision_at_proxy']} "
            f"hits={row['proxy_hits']} scorable={row['scorable_alerts']} insufficient={row['insufficient_alerts']}"
        )
    if evidence.get("fail_conditions"):
        lines.append(f"  fail_conditions={','.join(evidence['fail_conditions'])}")
    return "\n".join(lines)


def cmd_alert_eval(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool

    manifest = Path(getattr(args, "manifest", None) or DEFAULT_MANIFEST)
    limit = getattr(args, "limit", None)

    async def _run() -> tuple[dict[str, Any] | None, str | None]:
        cfg = load_config()
        pool = None
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
            evidence = await run_alert_precision_measurement(
                pool,
                manifest,
                limit=limit,
            )
            return evidence, None
        except Exception as exc:
            return None, str(exc)
        finally:
            if pool is not None:
                await pool.close()

    evidence, error = asyncio.run(_run())
    if error:
        print(f"[alert-eval] measurement unavailable: {error}")
        print("[alert-eval] no measurements were emitted; run after local Postgres/Docker is available.")
        return 1
    assert evidence is not None
    if getattr(args, "format", "json") == "text":
        print(format_alert_eval_text(evidence))
    else:
        print(json.dumps(evidence, indent=2, sort_keys=True, default=_json_default))
    return 0 if evidence["outcome"] == "PASS" else 1
