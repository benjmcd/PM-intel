"""Alert command handlers: alerts list and alerts serve.

Note: cmd_alerts_explain stays in pmfi.cli because tests patch pmfi.cli.asyncio.run
when testing it.  cmd_alerts also stays in pmfi.cli because it dispatches to
cmd_alerts_explain which must resolve in cli.py's namespace.
"""
from __future__ import annotations

import argparse
import asyncio


_LOW_NOTIONAL_USD = 5000.0
_THIN_BASELINE_SAMPLE_SIZE = 10
_THIN_BASELINE_TRADES = 20
_NEAR_THRESHOLD_RATIO = 1.25
_NORMAL_DATA_QUALITY = {
    "ok",
    "live",
    "verified",
    "baseline_available",
    "baseline_sufficient",
    "in_window",
    "oi_present",
}


def _parse_evidence(evidence) -> dict:
    if isinstance(evidence, dict):
        return evidence
    if isinstance(evidence, str):
        import json
        try:
            parsed = json.loads(evidence)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_degraded_reasons(evidence: dict) -> bool:
    reasons = evidence.get("degraded_reasons") or evidence.get("data_quality_reasons")
    if reasons in (None, "", [], ()):
        return False
    if isinstance(reasons, str):
        return reasons.strip() not in {"", "[]", "()"}
    return bool(reasons)


def _is_near_threshold(evidence: dict) -> bool:
    spike = _as_float(evidence.get("spike_multiplier"))
    min_spike = _as_float(evidence.get("min_spike_multiplier"))
    if spike is not None and min_spike and spike >= min_spike:
        return spike / min_spike <= _NEAR_THRESHOLD_RATIO

    capital = _as_float(evidence.get("capital_at_risk_usd"))
    if capital is None:
        capital = _as_float(evidence.get("this_trade_usd"))
    if capital is None:
        return False

    for key in (
        "min_capital_threshold_usd",
        "min_capital_at_risk_usd",
        "threshold_usd",
        "p99_threshold_usd",
        "p99_trade_usd",
        "p995_trade_usd",
    ):
        threshold = _as_float(evidence.get(key))
        if threshold and capital >= threshold:
            return capital / threshold <= _NEAR_THRESHOLD_RATIO
    return False


def _triage_flags(row: dict, evidence: dict) -> list[str]:
    flags: list[str] = []

    notional_values = (
        _as_float(evidence.get("this_trade_usd")),
        _as_float(evidence.get("capital_at_risk_usd")),
    )
    if any(value is not None and value < _LOW_NOTIONAL_USD for value in notional_values):
        flags.append("low_notional")

    baseline_sample_size = _as_int(evidence.get("baseline_sample_size"))
    baseline_trades = _as_int(evidence.get("baseline_trades"))
    if (
        evidence.get("baseline_state") == "baseline_sparse"
        or (baseline_sample_size is not None and baseline_sample_size < _THIN_BASELINE_SAMPLE_SIZE)
        or (baseline_trades is not None and baseline_trades <= _THIN_BASELINE_TRADES)
    ):
        flags.append("thin_baseline")

    if _is_near_threshold(evidence):
        flags.append("near_threshold")

    data_quality = str(row.get("data_quality") or "").lower()
    if (
        _has_degraded_reasons(evidence)
        or (data_quality and data_quality not in _NORMAL_DATA_QUALITY)
    ):
        flags.append("degraded_data_quality")

    if "raw_event_id" in row and "trade_id" in row and (
        not row.get("raw_event_id") or not row.get("trade_id")
    ):
        flags.append("missing_lineage")

    return flags


def cmd_alerts_list(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    import asyncpg

    limit = getattr(args, "limit", None) or 20
    show_evidence = getattr(args, "evidence", False)
    rule_filter = getattr(args, "rule", None)
    venue_filter = getattr(args, "venue", None)
    severity_filter = getattr(args, "severity", None)
    market_filter = getattr(args, "market", None)
    unreviewed_filter = getattr(args, "unreviewed", False)
    reviewed_filter = getattr(args, "reviewed", False)
    review_label_filter = getattr(args, "review_label", None)
    fmt = getattr(args, "format", "table")

    if unreviewed_filter and (reviewed_filter or review_label_filter):
        print("[alerts list] --unreviewed cannot be combined with --reviewed or --review-label.")
        return 1
    if review_label_filter and review_label_filter not in {"tp", "fp", "noise"}:
        print("[alerts list] --review-label must be one of: tp, fp, noise.")
        return 1

    # Parse --since: accepts relative ("1h", "24h", "7d") or ISO datetime string
    since_dt = None
    since_raw = getattr(args, "since", None)
    if since_raw:
        import re
        _m = re.match(r"^(\d+)([hdm])$", since_raw)
        if _m:
            n, unit = int(_m.group(1)), _m.group(2)
            delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
            from datetime import datetime, timezone, timedelta
            since_dt = datetime.now(timezone.utc) - timedelta(seconds=delta)
        else:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since_raw)
            except ValueError:
                print(f"[alerts list] Invalid --since value: {since_raw!r}")
                return 1

    async def _query():
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            ev_col = ", a.evidence, a.raw_event_id, a.trade_id::text AS trade_id" if show_evidence else ""
            conditions: list[str] = []
            params: list = []
            idx = 1
            if rule_filter:
                conditions.append(f"a.rule_key = ${idx}")
                params.append(rule_filter); idx += 1
            if venue_filter:
                conditions.append(f"a.venue_code = ${idx}")
                params.append(venue_filter); idx += 1
            if severity_filter:
                conditions.append(f"a.severity = ${idx}")
                params.append(severity_filter); idx += 1
            if market_filter:
                conditions.append(
                    f"(m.title ILIKE ${idx} OR m.venue_market_id ILIKE ${idx} OR a.market_id::text ILIKE ${idx})"
                )
                params.append(f"%{market_filter}%"); idx += 1
            if since_dt is not None:
                conditions.append(f"a.fired_at >= ${idx}")
                params.append(since_dt); idx += 1
            if unreviewed_filter:
                conditions.append("lr.alert_id IS NULL")
            if reviewed_filter:
                conditions.append("lr.alert_id IS NOT NULL")
            if review_label_filter:
                conditions.append(f"lr.review_label = ${idx}")
                params.append(review_label_filter); idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            rows = await pool.fetch(
                f"WITH latest_reviews AS ("
                f"SELECT DISTINCT ON (ar.alert_id) ar.alert_id, ar.label AS review_label "
                f"FROM alert_reviews ar "
                f"ORDER BY ar.alert_id, ar.reviewed_at DESC, ar.review_id DESC"
                f") "
                f"SELECT a.alert_id, a.fired_at, a.rule_key, a.rule_version, a.severity, a.confidence, a.score, "
                f"a.venue_code, a.outcome_key, a.data_quality, LEFT(m.title, 60) AS market_title, "
                f"mo.outcome_label, "
                f"lr.review_label AS review_label"
                f"{ev_col} "
                f"FROM alerts a "
                f"LEFT JOIN markets m ON m.market_id = a.market_id "
                f"LEFT JOIN market_outcomes mo ON mo.market_id = a.market_id AND mo.outcome_key = a.outcome_key "
                f"LEFT JOIN latest_reviews lr ON lr.alert_id = a.alert_id "
                f"{where} ORDER BY a.fired_at DESC LIMIT ${idx}",
                *params,
            )
            return rows, None
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    if not rows:
        print("No alerts in DB. Run 'pmfi replay --persist' to populate.")
        return 0

    # JSON output mode
    if fmt == "json":
        import json as _json
        from pmfi.dashboard.queries import _summarize_evidence
        def _serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)
        payload = []
        for row in rows:
            item = dict(row)
            if show_evidence:
                evidence = _parse_evidence(item.get("evidence"))
                item["evidence_parsed"] = evidence
                item["evidence_summary"] = _summarize_evidence(evidence)
                item["triage_flags"] = _triage_flags(item, evidence)
            payload.append(item)
        print(_json.dumps(payload, indent=2, default=_serial))
        return 0

    count = len(rows)
    try:
        from rich.console import Console
        from rich.table import Table
        # Force 140 cols so rule names and timestamps never wrap/truncate.
        console = Console(width=140)
        table = Table(title=f"Recent Alerts (DB, last {count})", show_lines=show_evidence)
        table.add_column("ID", style="dim", no_wrap=True, min_width=8)
        table.add_column("When", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Rule", style="yellow", min_width=32)
        table.add_column("Ver", min_width=8)
        table.add_column("Sev", style="red", min_width=4)
        table.add_column("Conf", min_width=6)
        table.add_column("DQ", min_width=10)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Outcome", min_width=3)
        table.add_column("Label", min_width=8)
        table.add_column("Score", min_width=6)
        table.add_column("Market", style="dim", min_width=20)
        if show_evidence:
            table.add_column("Evidence")
        for row in rows:
            when = str(row["fired_at"])[5:16]  # "MM-DD HH:MM"
            ev_cell = ""
            if show_evidence:
                import json as _json
                ev = row["evidence"] or {}
                if isinstance(ev, str):
                    try:
                        ev = _json.loads(ev)
                    except Exception:
                        pass
                # Include rule_version in evidence view
                ev_lines = [f"rule_version={row.get('rule_version') or '—'}"]
                ev_lines += [f"{k}={v}" for k, v in ev.items()] if isinstance(ev, dict) else [str(ev)]
                ev_cell = "\n".join(ev_lines)
            title = row["market_title"] or "—"
            cells = [
                str(row["alert_id"])[:8],
                when,
                row["rule_key"],
                row.get("rule_version") or "—",
                row["severity"],
                row["confidence"],
                row.get("data_quality") or "—",
                row["venue_code"],
                row["outcome_key"] or "—",
                row.get("review_label") or "—",
                str(row["score"])[:6],
                title,
            ]
            if show_evidence:
                cells.append(ev_cell)
            table.add_row(*cells)
        console.print(table)
    except ImportError:
        for row in rows:
            print(f"{str(row['alert_id'])[:8]}  {str(row['fired_at'])[5:16]}  {row['rule_key']}  {row['severity']}  {row['venue_code']}  {row['outcome_key']}")
    return 0


def cmd_alerts_serve(args: argparse.Namespace) -> int:
    """Run a local HTTP receiver for alert delivery testing."""
    port = getattr(args, "port", 8765)
    host = getattr(args, "host", "127.0.0.1")
    from pmfi.delivery.server import run_alert_receiver
    try:
        asyncio.run(run_alert_receiver(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[alerts serve] stopped.")
    return 0


def cmd_alerts_review(args: argparse.Namespace) -> int:
    """Write a review record to the alert_reviews table."""
    from pmfi.config import load_config

    alert_id = args.alert_id
    label = args.label
    category = getattr(args, "category", None)
    notes = getattr(args, "notes", None)
    reviewed_by = getattr(args, "reviewed_by", None)
    dry_run = getattr(args, "dry_run", False)

    async def _insert():
        import asyncpg
        from pmfi.db.repos.alerts import get_alert_by_id, resolve_alert_id
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return str(exc)
        try:
            _aid = alert_id
            if not (len(_aid) == 36 and _aid.count('-') == 4):
                async with pool.acquire() as _conn:
                    _aid = await resolve_alert_id(_conn, _aid)
                if not _aid:
                    return f"__fk__{alert_id}"
            if dry_run:
                async with pool.acquire() as _conn:
                    row = await get_alert_by_id(_conn, _aid)
                if not row:
                    return f"__fk__{alert_id}"
                return {"dry_run": True, "alert": row, "alert_id": _aid}
            await pool.execute(
                "INSERT INTO alert_reviews (alert_id, label, false_positive_category, notes, reviewed_by) "
                "VALUES ($1::uuid, $2, $3, $4, $5)",
                _aid, label, category, notes, reviewed_by,
            )
            return None
        except asyncpg.ForeignKeyViolationError:
            return f"__fk__{alert_id}"
        except Exception as exc:
            return str(exc)
        finally:
            await pool.close()

    err = asyncio.run(_insert())
    if isinstance(err, dict) and err.get("dry_run"):
        row = err.get("alert") or {}
        print("[review dry-run] no database write performed.")
        print(f"  alert_id={err.get('alert_id')}")
        print(f"  label={label}")
        if category:
            print(f"  category={category}")
        if notes:
            print(f"  notes={notes}")
        if reviewed_by:
            print(f"  reviewed_by={reviewed_by}")
        print(
            "  target="
            f"{row.get('rule_key', 'unknown')} "
            f"severity={row.get('severity', 'unknown')} "
            f"outcome={row.get('outcome_key') or '-'}"
        )
        title = row.get("market_title") or row.get("venue_market_id")
        if title:
            print(f"  market={title}")
        return 0
    if err is None:
        print(f"[review] alert_id={alert_id} label={label} recorded.")
        return 0
    if isinstance(err, str) and err.startswith("__fk__"):
        aid = err[len("__fk__"):]
        print(f"Alert {aid} not found.")
        return 1
    print(f"DB error: {err}\nRun 'pmfi db-verify' to check connectivity.")
    return 1


def cmd_alerts_fp_rate(args: argparse.Namespace) -> int:
    """Show false-positive statistics from alert_reviews."""
    from pmfi.config import load_config

    since_raw = getattr(args, "since", None)
    rule_filter = getattr(args, "rule", None)

    # Parse --since
    since_dt = None
    if since_raw:
        import re
        _m = re.match(r"^(\d+)([hdm])$", since_raw)
        if _m:
            n, unit = int(_m.group(1)), _m.group(2)
            delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
            from datetime import datetime, timezone, timedelta
            since_dt = datetime.now(timezone.utc) - timedelta(seconds=delta)
        else:
            from datetime import datetime
            try:
                since_dt = datetime.fromisoformat(since_raw)
            except ValueError:
                print(f"[alerts fp-rate] Invalid --since value: {since_raw!r}")
                return 1

    async def _query():
        import asyncpg
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            conditions: list[str] = []
            params: list = []
            idx = 1
            if since_dt is not None:
                conditions.append(f"ar.reviewed_at >= ${idx}")
                params.append(since_dt); idx += 1
            if rule_filter:
                conditions.append(f"a.rule_key = ${idx}")
                params.append(rule_filter); idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = await pool.fetch(
                f"SELECT ar.label, a.rule_key, COUNT(*) AS cnt "
                f"FROM alert_reviews ar "
                f"JOIN alerts a ON a.alert_id = ar.alert_id "
                f"{where} "
                f"GROUP BY ar.label, a.rule_key "
                f"ORDER BY a.rule_key, ar.label",
                *params,
            )
            return rows, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1

    if not rows:
        print("No reviews recorded yet. Use 'pmfi alerts review <alert_id> --label fp|tp|noise' to add one.")
        return 0

    total_reviewed = sum(r["cnt"] for r in rows)
    fp_count = sum(r["cnt"] for r in rows if r["label"] == "fp")
    tp_count = sum(r["cnt"] for r in rows if r["label"] == "tp")
    noise_count = sum(r["cnt"] for r in rows if r["label"] == "noise")
    fp_rate = fp_count / total_reviewed * 100 if total_reviewed > 0 else 0.0

    since_label = since_raw if since_raw else "all time"
    rule_label = f"rule={rule_filter}" if rule_filter else "all rules"
    header = f"Alert Review Summary ({rule_label} / since {since_label})"
    summary = (
        f"Reviewed: {total_reviewed} | FP: {fp_count} ({fp_rate:.1f}%) | "
        f"TP: {tp_count} | Noise: {noise_count}"
    )

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title=header)
        table.add_column("Rule", style="yellow")
        table.add_column("Label", style="cyan")
        table.add_column("Count", justify="right")
        for row in rows:
            table.add_row(row["rule_key"], row["label"], str(row["cnt"]))
        console.print(table)
        console.print(summary)
    except ImportError:
        print(header)
        print(summary)
        for row in rows:
            print(f"  {row['rule_key']}  {row['label']}  {row['cnt']}")

    return 0
