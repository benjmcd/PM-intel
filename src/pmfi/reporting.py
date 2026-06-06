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


def build_report(results: list[ReplayResult], *, title: str = "Fixture Replay Report") -> ReportSummary:
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
    )


def write_report(summary: ReportSummary, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = summary.generated_at[:10]
    path = output_dir / f"{date_str}-fixture-report.txt"
    path.write_text("\n".join(summary.lines) + "\n", encoding="utf-8")
    return path
