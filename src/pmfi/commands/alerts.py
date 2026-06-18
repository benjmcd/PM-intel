"""Alert command handlers: alerts list and alerts serve.

Note: cmd_alerts_explain stays in pmfi.cli because tests patch pmfi.cli.asyncio.run
when testing it.  cmd_alerts also stays in pmfi.cli because it dispatches to
cmd_alerts_explain which must resolve in cli.py's namespace.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pmfi.alert_triage import parse_evidence as _parse_evidence
from pmfi.alert_triage import triage_flags as _triage_flags


def _parse_since_window(raw: str | None, *, command: str):
    """Parse a relative or ISO since value; return (datetime, error_message)."""
    if not raw:
        return datetime.now(timezone.utc) - timedelta(hours=24), None
    import re
    match = re.match(r"^(\d+)([hdm])$", raw)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
        return datetime.now(timezone.utc) - timedelta(seconds=delta), None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, f"[{command}] Invalid --since value: {raw!r}"
    if dt.tzinfo is None:
        return None, f"[{command}] Invalid --since value: {raw!r}; timestamp must include timezone."
    return dt.astimezone(timezone.utc), None


def _default_review_packet_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return _review_packet_output_root() / f"review-packet-{stamp}.json"


def _review_packet_output_root() -> Path:
    from pmfi.commands._shared import ROOT

    return ROOT / "reports" / "review-packets"


def _resolve_review_packet_output(output_raw: str | None) -> tuple[Path | None, str | None]:
    output_root = _review_packet_output_root().resolve()
    if not output_raw:
        output_path = _default_review_packet_path()
    else:
        raw_path = Path(output_raw)
        if raw_path.is_absolute():
            output_path = raw_path
        elif raw_path.parent == Path("."):
            output_path = output_root / raw_path.name
        else:
            from pmfi.commands._shared import ROOT

            output_path = ROOT / raw_path
    resolved = output_path.resolve()
    try:
        resolved.relative_to(output_root)
    except ValueError:
        return None, (
            "[alerts review-packet] --output must be inside "
            f"{output_root}"
        )
    if resolved.exists():
        return None, f"[alerts review-packet] output already exists: {resolved}"
    return resolved, None


def _json_serial(obj):  # noqa: ANN001
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


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
    triage_filters = list(getattr(args, "triage_flag", None) or [])
    needs_triage = bool(triage_filters)
    needs_evidence_fields = show_evidence or needs_triage
    fmt = getattr(args, "format", "table")
    has_result_filters = any([
        rule_filter,
        venue_filter,
        severity_filter,
        market_filter,
        unreviewed_filter,
        reviewed_filter,
        review_label_filter,
        getattr(args, "since", None),
    ])

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
            ev_col = ", a.evidence, a.raw_event_id, a.trade_id::text AS trade_id" if needs_evidence_fields else ""
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
            limit_clause = ""
            if not needs_triage:
                params.append(limit)
                limit_clause = f" LIMIT ${idx}"
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
                f"{where} ORDER BY a.fired_at DESC{limit_clause}",
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
        if needs_triage:
            print(f"No alerts match triage flags: {', '.join(triage_filters)}.")
            return 0
        if has_result_filters:
            print("No alerts match the selected filters.")
            return 0
        print("No alerts in DB. Run 'pmfi replay --persist' to populate.")
        return 0

    if needs_evidence_fields:
        enriched_rows = []
        required_flags = set(triage_filters)
        for row in rows:
            item = dict(row)
            evidence = _parse_evidence(item.get("evidence"))
            flags = _triage_flags(item, evidence)
            item["_evidence_parsed"] = evidence
            item["triage_flags"] = flags
            if required_flags and not required_flags.issubset(set(flags)):
                continue
            enriched_rows.append(item)
        rows = enriched_rows[:limit] if needs_triage else enriched_rows
        if needs_triage and not rows:
            print(f"No alerts match triage flags: {', '.join(triage_filters)}.")
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
                evidence = item.pop("_evidence_parsed", None) or _parse_evidence(item.get("evidence"))
                item["evidence_parsed"] = evidence
                item["evidence_summary"] = _summarize_evidence(evidence)
                item["triage_flags"] = item.get("triage_flags") or _triage_flags(item, evidence)
            else:
                item.pop("_evidence_parsed", None)
                if needs_triage:
                    item.pop("evidence", None)
                    item.pop("raw_event_id", None)
                    item.pop("trade_id", None)
            payload.append(item)
        print(_json.dumps(payload, indent=2, default=_serial))
        return 0

    count = len(rows)
    try:
        from rich.console import Console
        from rich.table import Table
        # Force wide output so rule names, timestamps, and optional flags stay visible.
        console = Console(width=240 if needs_triage else 140)
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
        if needs_triage:
            table.add_column("Flags", min_width=12, no_wrap=True)
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
            if needs_triage:
                cells.append(",".join(row.get("triage_flags") or []) or "-")
            if show_evidence:
                cells.append(ev_cell)
            table.add_row(*cells)
        console.print(table)
    except ImportError:
        for row in rows:
            flags = ""
            if needs_triage:
                flags = f"  flags={','.join(row.get('triage_flags') or []) or '-'}"
            print(f"{str(row['alert_id'])[:8]}  {str(row['fired_at'])[5:16]}  {row['rule_key']}  {row['severity']}  {row['venue_code']}  {row['outcome_key']}{flags}")
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
        from pmfi.db.repos.alerts import get_alert_by_id, insert_alert_review
        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return str(exc)
        try:
            if dry_run:
                async with pool.acquire() as _conn:
                    row = await get_alert_by_id(_conn, alert_id)
                if not row:
                    return f"__fk__{alert_id}"
                return {"dry_run": True, "alert": row, "alert_id": row["alert_id"]}

            async with pool.acquire() as _conn:
                review = await insert_alert_review(
                    _conn,
                    alert_id,
                    label=label,
                    category=category,
                    notes=notes,
                    reviewed_by=reviewed_by,
                )
            if review is None:
                return f"__fk__{alert_id}"
            return {"review": review}
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
    if isinstance(err, dict) and err.get("review"):
        review = err["review"]
        print(f"[review] alert_id={review.get('alert_id', alert_id)} label={label} recorded.")
        return 0
    if isinstance(err, str) and err.startswith("__fk__"):
        aid = err[len("__fk__"):]
        print(f"Alert {aid} not found.")
        return 1
    print(f"DB error: {err}\nRun 'pmfi db-verify' to check connectivity.")
    return 1


def cmd_alerts_review_packet(args: argparse.Namespace) -> int:
    """Export a read-only local JSON review packet for a latest-reviewed cohort."""
    from pmfi.config import load_config

    since_dt, since_err = _parse_since_window(
        getattr(args, "since", None),
        command="alerts review-packet",
    )
    if since_err:
        print(since_err)
        return 1
    limit = getattr(args, "limit", 50)
    if limit <= 0:
        print("[alerts review-packet] --limit must be a positive integer.")
        return 1
    review_label = getattr(args, "review_label", None)
    if review_label and review_label not in {"tp", "fp", "noise"}:
        print("[alerts review-packet] --review-label must be one of: tp, fp, noise.")
        return 1
    fmt = getattr(args, "format", "json")
    if fmt != "json":
        print("[alerts review-packet] only JSON output is supported.")
        return 1

    output_path, output_err = _resolve_review_packet_output(getattr(args, "output", None))
    if output_err:
        print(output_err)
        return 1
    assert output_path is not None

    async def _export():
        import asyncpg
        from pmfi.db.repos.alerts import get_review_packet

        cfg = load_config()
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                packet = await get_review_packet(
                    conn,
                    since=since_dt,
                    rule=getattr(args, "rule", None),
                    review_label=review_label,
                    category=getattr(args, "category", None),
                    limit=limit,
                )
            return packet, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    packet, err = asyncio.run(_export())
    if err:
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, indent=2, default=_json_serial) + "\n",
        encoding="utf-8",
    )
    totals = (packet or {}).get("reviewed_cohort_totals") or {}
    print(
        f"[review-packet] wrote {output_path} "
        f"alerts={totals.get('alerts', 0)}"
    )
    return 0


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
