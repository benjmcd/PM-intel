"""Alert review (false-positive feedback) command handlers.

Operator workflow: label an alert (false_positive / true_positive / needs_review),
list recent reviews, and see the false-positive rate per rule. By design there is
NO automatic suppression from these labels — a single mislabel must never silence a
real signal. The labels are operator insight, surfaced via 'pmfi alerts fp-rate'.
"""
from __future__ import annotations

import argparse
import asyncio


def _parse_since(since_raw: str | None):
    """Accept a relative window ('1h','24h','7d') or an ISO datetime; else None."""
    if not since_raw:
        return None
    import re
    from datetime import datetime, timezone, timedelta
    m = re.match(r"^(\d+)([hdm])$", since_raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
        return datetime.now(timezone.utc) - timedelta(seconds=delta)
    try:
        return datetime.fromisoformat(since_raw)
    except ValueError:
        return None


async def _make_pool():
    from pmfi.config import load_config
    import asyncpg
    cfg = load_config()
    return await asyncpg.create_pool(
        cfg.database.url, min_size=1, max_size=1,
        server_settings={"search_path": "pmfi,public"},
    )


def cmd_alerts_review(args: argparse.Namespace) -> int:
    from pmfi.db.repos.alert_reviews import record_review

    alert_id = args.alert_id
    label = args.label
    category = getattr(args, "category", None)
    notes = getattr(args, "notes", None)
    reviewed_by = getattr(args, "by", None)

    async def _run():
        try:
            pool = await _make_pool()
        except Exception as exc:
            print(f"[alerts review] DB connection failed: {exc}")
            return 1
        try:
            async with pool.acquire() as conn:
                try:
                    review_id = await record_review(
                        conn, alert_id=alert_id, label=label,
                        false_positive_category=category, notes=notes,
                        reviewed_by=reviewed_by,
                    )
                except ValueError as ve:
                    print(f"[alerts review] {ve}")
                    return 1
            print(f"recorded review {review_id} (label={label}) for alert {alert_id}")
            return 0
        finally:
            await pool.close()

    return asyncio.run(_run())


def cmd_alerts_reviews(args: argparse.Namespace) -> int:
    from pmfi.db.repos.alert_reviews import list_reviews
    from rich.console import Console
    from rich.table import Table

    limit = getattr(args, "limit", None) or 50
    label = getattr(args, "label", None)

    async def _run():
        try:
            pool = await _make_pool()
        except Exception as exc:
            print(f"[alerts reviews] DB connection failed: {exc}")
            return 1
        try:
            async with pool.acquire() as conn:
                rows = await list_reviews(conn, limit=limit, label=label)
        finally:
            await pool.close()
        console = Console(width=160)
        if not rows:
            console.print("No alert reviews recorded.")
            return 0
        table = Table(title=f"Alert reviews ({len(rows)})")
        for col in ("Reviewed", "Label", "Rule", "Category", "Alert", "By", "Notes"):
            table.add_column(col)
        for r in rows:
            ts = r["reviewed_at"].strftime("%m-%d %H:%M") if r.get("reviewed_at") else ""
            table.add_row(
                ts, str(r.get("label", "")), str(r.get("rule_key", "")),
                str(r.get("false_positive_category") or ""),
                str(r.get("alert_id", ""))[:8], str(r.get("reviewed_by") or ""),
                str(r.get("notes") or "")[:40],
            )
        console.print(table)
        return 0

    return asyncio.run(_run())


def cmd_alerts_fp_rate(args: argparse.Namespace) -> int:
    from pmfi.db.repos.alert_reviews import false_positive_rate_by_rule
    from rich.console import Console
    from rich.table import Table

    since = _parse_since(getattr(args, "since", None))

    async def _run():
        try:
            pool = await _make_pool()
        except Exception as exc:
            print(f"[alerts fp-rate] DB connection failed: {exc}")
            return 1
        try:
            async with pool.acquire() as conn:
                rows = await false_positive_rate_by_rule(conn, since=since)
        finally:
            await pool.close()
        console = Console(width=120)
        if not rows:
            console.print("No alerts in the window.")
            return 0
        table = Table(title="False-positive rate by rule")
        for col in ("Rule", "Alerts", "False positives", "FP rate %"):
            table.add_column(col)
        for r in rows:
            rate = r.get("false_positive_rate_pct")
            table.add_row(
                str(r.get("rule_key", "")), str(r.get("total_alerts", 0)),
                str(r.get("false_positive_count", 0)),
                "n/a" if rate is None else str(rate),
            )
        console.print(table)
        return 0

    return asyncio.run(_run())
