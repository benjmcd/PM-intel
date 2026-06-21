from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from pmfi.commands.soak import parse_soak_timestamp


def _parse_window(label: str, raw: str | None):
    if not raw:
        return None, None
    try:
        return parse_soak_timestamp(raw), None
    except ValueError as exc:
        return None, f"{label}: {exc}"


def _decision_rows(results) -> list[dict[str, Any]]:  # noqa: ANN001
    rows: list[dict[str, Any]] = []
    for result in results:
        for decision in result.alerts:
            rows.append({
                "rule_id": decision.rule_id,
                "rule_version": decision.rule_version,
                "severity": decision.severity,
                "confidence": decision.confidence,
                "score": decision.score,
                "market": result.trade.venue_market_id,
                "evidence": decision.evidence,
            })
    return rows


def _print_text(report: dict[str, Any]) -> None:
    filters = report["filters"]
    summary = report["summary"]
    print("PMFI hypothetical backtest")
    print(
        "scope: "
        f"from={filters['from'] or 'start'} "
        f"to={filters['to'] or 'now'} "
        f"venue={filters['venue'] or 'all'} "
        f"market={filters['market'] or 'all'} "
        f"limit={filters['limit']}"
    )
    print(f"normalized trades replayed: {report['normalized_trades_replayed']}")
    print(f"hypothetical alerts: {summary['total_alerts']}")
    if not summary["by_rule"]:
        return
    print("alerts by rule:")
    for rule, count in sorted(summary["by_rule"].items(), key=lambda item: (-item[1], item[0])):
        print(f"  {rule}: {count}")
    print("alerts by severity:")
    for severity, count in sorted(summary["by_severity"].items(), key=lambda item: (-item[1], item[0])):
        print(f"  {severity}: {count}")


def cmd_backtest(args: argparse.Namespace) -> int:
    start_ts, start_error = _parse_window("--from", getattr(args, "backtest_from", None))
    if start_error:
        print(f"[backtest] {start_error}")
        return 1
    end_ts, end_error = _parse_window("--to", getattr(args, "backtest_to", None))
    if end_error:
        print(f"[backtest] {end_error}")
        return 1
    if start_ts is not None and end_ts is not None and start_ts >= end_ts:
        print("[backtest] --from must be before --to")
        return 1

    async def _run():
        from pmfi.config import load_config
        from pmfi.db import close_pool, create_pool
        from pmfi.replay import replay_from_db

        cfg = load_config()
        pool = await create_pool(cfg.database.url)
        try:
            return await replay_from_db(
                pool,
                limit=getattr(args, "limit", 200),
                start_ts=start_ts,
                end_ts=end_ts,
                venue=getattr(args, "backtest_venue", None),
                market=getattr(args, "backtest_market", None),
                persist=False,
                seed=not getattr(args, "cold_start", False),
                print_summary=False,
                normalized_only=True,
            )
        finally:
            await close_pool(pool)

    try:
        results = asyncio.run(_run())
    except Exception as exc:
        print(f"[backtest] DB unavailable: {exc}")
        return 1

    from pmfi.reporting import build_backtest_summary

    summary = build_backtest_summary(_decision_rows(results))
    report = {
        "schema_version": "backtest_summary.v1",
        "local_only": True,
        "persisted": False,
        "filters": {
            "from": start_ts.isoformat() if start_ts else None,
            "to": end_ts.isoformat() if end_ts else None,
            "limit": getattr(args, "limit", 200),
            "venue": getattr(args, "backtest_venue", None),
            "market": getattr(args, "backtest_market", None),
            "cold_start": bool(getattr(args, "cold_start", False)),
        },
        "normalized_trades_replayed": len(results),
        "summary": summary,
    }

    if getattr(args, "format", "text") == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_text(report)
    return 0
