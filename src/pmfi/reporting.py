from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from pmfi.domain import AlertDecision, NormalizedTrade
from pmfi.replay import ReplayResult


@dataclass
class ReportSummary:
    generated_at: str
    fixture_count: int
    trade_count: int
    alert_count: int
    alerts_by_rule: dict[str, int]
    alerts_by_venue: dict[str, int]
    alerts_by_severity: dict[str, int]
    alerts_by_confidence: dict[str, int]
    cluster_events: list[dict]
    lines: list[str]
    report_kind: str = "fixture"


def build_report(
    results: list[ReplayResult],
    *,
    title: str = "Fixture Replay Report",
    report_kind: str = "fixture",
) -> ReportSummary:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts_by_rule: dict[str, int] = defaultdict(int)
    alerts_by_venue: dict[str, int] = defaultdict(int)
    alerts_by_severity: dict[str, int] = defaultdict(int)
    alerts_by_confidence: dict[str, int] = defaultdict(int)
    cluster_events: list[dict] = []
    total_alerts = 0

    for r in results:
        for d in r.alerts:
            total_alerts += 1
            alerts_by_rule[d.rule_id] += 1
            alerts_by_venue[r.trade.venue_code] += 1
            alerts_by_severity[d.severity] += 1
            alerts_by_confidence[d.confidence] += 1
            if d.rule_id == "directional_cluster_v1":
                cluster_events.append({
                    "venue_code": r.trade.venue_code,
                    "venue_market_id": r.trade.venue_market_id,
                    "dominant_side": d.evidence.get("dominant_side"),
                    "cluster_trade_count": d.evidence.get("cluster_trade_count"),
                    "net_capital_usd": d.evidence.get("net_capital_usd"),
                    "price_impact_cents": d.evidence.get("price_impact_cents"),
                })

    lines = [
        f"# {title}",
        f"Generated: {now}",
        "",
        f"Fixtures processed : {len(results)}",
        f"Trades normalized  : {len(results)}",
        f"Alerts emitted     : {total_alerts}",
        "",
        "## Alerts by rule",
    ]
    for rule, count in sorted(alerts_by_rule.items(), key=lambda x: -x[1]):
        lines.append(f"  {rule:<45} {count:>4}")
    lines += ["", "## Alerts by severity"]
    for sev, count in sorted(alerts_by_severity.items(), key=lambda x: -x[1]):
        lines.append(f"  {sev:<12} {count:>4}")
    lines += ["", "## Alerts by confidence"]
    for conf, count in sorted(alerts_by_confidence.items(), key=lambda x: -x[1]):
        lines.append(f"  {conf:<12} {count:>4}")
    lines += ["", "## Alerts by venue"]
    for venue, count in sorted(alerts_by_venue.items(), key=lambda x: -x[1]):
        lines.append(f"  {venue:<16} {count:>4}")
    if cluster_events:
        lines += ["", "## Cluster events"]
        for ev in cluster_events:
            lines.append(
                f"  {ev['venue_code']}:{ev['venue_market_id']} "
                f"side={ev['dominant_side']} trades={ev['cluster_trade_count']} "
                f"net_cap=${ev['net_capital_usd']} impact={ev['price_impact_cents']}c"
            )
    lines += ["", "---", "End of report"]

    return ReportSummary(
        generated_at=now,
        fixture_count=len(results),
        trade_count=len(results),
        alert_count=total_alerts,
        alerts_by_rule=dict(alerts_by_rule),
        alerts_by_venue=dict(alerts_by_venue),
        alerts_by_severity=dict(alerts_by_severity),
        alerts_by_confidence=dict(alerts_by_confidence),
        cluster_events=cluster_events,
        lines=lines,
        report_kind=report_kind,
    )


def write_report(summary: ReportSummary, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary.generated_at.replace("-", "").replace(":", "").replace("T", "-").removesuffix("Z")
    kind = summary.report_kind.strip().lower().replace("_", "-")
    if kind not in {"fixture", "db"}:
        kind = "replay"
    suffix = f"{kind}-report"
    path = output_dir / f"{stamp}-{suffix}.txt"
    n = 2
    while path.exists():
        path = output_dir / f"{stamp}-{suffix}-{n}.txt"
        n += 1
    path.write_text("\n".join(summary.lines) + "\n", encoding="utf-8")
    return path


def build_backtest_summary(
    decisions: list[dict],
    *,
    sample_per_rule: int = 3,
) -> dict:
    """Aggregate hypothetical alert decisions from a read-only backtest."""
    total = 0
    by_rule: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    by_market: dict[str, int] = defaultdict(int)
    samples_by_rule: dict[str, list[dict]] = defaultdict(list)

    for decision in decisions:
        rule = str(decision.get("rule_id") or "unknown")
        severity = str(decision.get("severity") or "unknown")
        market = str(decision.get("market") or "unknown")
        total += 1
        by_rule[rule] += 1
        by_severity[severity] += 1
        by_market[market] += 1
        if len(samples_by_rule[rule]) < sample_per_rule:
            samples_by_rule[rule].append({
                "rule_id": rule,
                "rule_version": decision.get("rule_version") or "",
                "severity": severity,
                "confidence": decision.get("confidence") or "",
                "score": str(decision.get("score") or ""),
                "market": market,
                "evidence": decision.get("evidence") or {},
            })

    return {
        "total_alerts": total,
        "by_rule": dict(by_rule),
        "by_severity": dict(by_severity),
        "by_market": dict(by_market),
        "samples_by_rule": dict(samples_by_rule),
    }


async def _fetch_db_stats(pool) -> dict:
    async with pool.acquire() as conn:
        raw_count = await conn.fetchval("SELECT COUNT(*) FROM raw_events")
        trade_count = await conn.fetchval("SELECT COUNT(*) FROM normalized_trades")
        dead_count = await conn.fetchval("SELECT COUNT(*) FROM dead_letters")
        metric_count = await conn.fetchval("SELECT COUNT(*) FROM metric_windows")
        alert_count = await conn.fetchval("SELECT COUNT(*) FROM alerts")
        by_rule = await conn.fetch(
            "SELECT rule_key, COUNT(*) AS n FROM alerts GROUP BY rule_key ORDER BY n DESC LIMIT 20"
        )
        by_severity = await conn.fetch(
            "SELECT severity, COUNT(*) AS n FROM alerts GROUP BY severity ORDER BY n DESC"
        )
        by_confidence = await conn.fetch(
            "SELECT confidence, COUNT(*) AS n FROM alerts GROUP BY confidence ORDER BY n DESC"
        )
        by_venue = await conn.fetch(
            "SELECT venue_code, COUNT(*) AS n FROM alerts GROUP BY venue_code ORDER BY n DESC"
        )
    return {
        "raw_count": raw_count,
        "trade_count": trade_count,
        "dead_count": dead_count,
        "metric_count": metric_count,
        "alert_count": alert_count,
        "by_rule": [(r["rule_key"], int(r["n"])) for r in by_rule],
        "by_severity": [(r["severity"], int(r["n"])) for r in by_severity],
        "by_confidence": [(r["confidence"], int(r["n"])) for r in by_confidence],
        "by_venue": [(r["venue_code"], int(r["n"])) for r in by_venue],
    }


def build_db_report(stats: dict, *, title: str = "PMFI DB State Report") -> ReportSummary:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# {title}",
        f"Generated: {now}",
        "",
        f"Raw events stored  : {stats['raw_count']}",
        f"Normalized trades  : {stats['trade_count']}",
        f"Dead letters       : {stats['dead_count']}",
        f"Metric windows     : {stats['metric_count']}",
        f"Alerts emitted     : {stats['alert_count']}",
        "",
        "## Alerts by rule",
    ]
    for rule, count in stats["by_rule"]:
        lines.append(f"  {rule:<45} {count:>4}")
    lines += ["", "## Alerts by severity"]
    for sev, count in stats["by_severity"]:
        lines.append(f"  {sev:<12} {count:>4}")
    lines += ["", "## Alerts by confidence"]
    for conf, count in stats["by_confidence"]:
        lines.append(f"  {conf:<12} {count:>4}")
    lines += ["", "## Alerts by venue"]
    for venue, count in stats["by_venue"]:
        lines.append(f"  {venue:<16} {count:>4}")
    lines += ["", "---", "End of report"]

    return ReportSummary(
        generated_at=now,
        fixture_count=0,
        trade_count=stats["trade_count"],
        alert_count=stats["alert_count"],
        alerts_by_rule=dict(stats["by_rule"]),
        alerts_by_venue=dict(stats["by_venue"]),
        alerts_by_severity=dict(stats["by_severity"]),
        alerts_by_confidence=dict(stats["by_confidence"]),
        cluster_events=[],
        lines=lines,
        report_kind="db",
    )
