"""Reporting and status command handlers: status, db-verify, db-maintenance,
dead-letters, stats, watch, report, health."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pmfi.commands._shared import ROOT


def cmd_status(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.pipeline.engine import AlertEngine
    cfg = load_config()

    engine = AlertEngine()
    rules = engine._rules.get("rules", {})
    enabled_rules = [k for k, v in rules.items() if v.get("enabled", True)]
    fixture_dir = ROOT / "tests" / "fixtures" / "raw"
    fixture_count = len(list(fixture_dir.glob("*.json"))) if fixture_dir.exists() else 0

    # Attempt a quick DB health check and stat fetch (non-fatal if DB is down).
    db_status = "unreachable"
    db_stats: dict = {}
    try:
        from pmfi.db import create_pool, close_pool

        async def _db_check():
            pool = await create_pool(cfg.database.url)
            try:
                stats = {}
                stats["markets"] = await pool.fetchval("SELECT COUNT(*) FROM markets")
                stats["raw_events"] = await pool.fetchval("SELECT COUNT(*) FROM raw_events")
                stats["alerts"] = await pool.fetchval("SELECT COUNT(*) FROM alerts")
                stats["baselines"] = await pool.fetchval("SELECT COUNT(*) FROM market_baselines")
                stats["last_alert"] = await pool.fetchval("SELECT MAX(fired_at) FROM alerts")
                # Extended diagnostics (best-effort; tables may not exist yet)
                try:
                    stats["normalized_trades"] = await pool.fetchval("SELECT COUNT(*) FROM normalized_trades")
                    stats["dead_letters"] = await pool.fetchval("SELECT COUNT(*) FROM dead_letters")
                    stats["asset_id_mappings"] = await pool.fetchval("SELECT COUNT(*) FROM market_outcomes")
                    stats["last_trade"] = await pool.fetchval("SELECT MAX(received_at) FROM normalized_trades")
                except Exception:
                    pass
                return "ok", stats
            finally:
                await close_pool(pool)

        db_status, db_stats = asyncio.run(_db_check())
    except Exception as exc:
        db_status = f"error: {exc}"

    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        db_color = "green" if db_status == "ok" else "red"
        db_line = f"[bold]DB:[/bold] {cfg.database.url.split('@')[-1]} [{db_color}]{db_status}[/{db_color}]"
        if db_stats:
            last = str(db_stats.get("last_alert") or "—")[:16]
            db_line += (
                f"  markets={db_stats['markets']} raw_events={db_stats['raw_events']}"
                f" alerts={db_stats['alerts']} baselines={db_stats['baselines']}"
                f" last_alert={last}"
            )
        lines = [
            db_line,
            f"[bold]Live mode:[/bold] {'[green]enabled[/green]' if cfg.live_mode_enabled else 'disabled'}",
            f"[bold]Polymarket live:[/bold] {cfg.features.enable_polymarket_live}",
            f"[bold]Kalshi live:[/bold] {cfg.features.enable_kalshi_live}",
            f"[bold]Delivery:[/bold] {cfg.alerts.default_delivery}",
            f"[bold]Alert rules:[/bold] {len(enabled_rules)} enabled: {', '.join(enabled_rules)}",
            f"[bold]Fixtures:[/bold] {fixture_count} in tests/fixtures/raw/",
        ]
        if db_stats and "normalized_trades" in db_stats:
            last_trade = db_stats.get("last_trade")
            last_trade_str = last_trade.isoformat() if last_trade else "—"
            lines.append(
                f"[bold]Extended:[/bold]"
                f" normalized_trades={db_stats['normalized_trades']}"
                f" dead_letters={db_stats.get('dead_letters', '?')}"
                f" asset_id_mappings={db_stats.get('asset_id_mappings', '?')}"
                f" last_trade={last_trade_str}"
            )
        console.print(Panel("\n".join(lines), title="PMFI Status", expand=False))
    except ImportError:
        print(f"PMFI local | db={db_status} | live={cfg.live_mode_enabled} | rules={len(enabled_rules)} | fixtures={fixture_count}")
    return 0


def cmd_db_verify(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    cfg = load_config()
    try:
        import asyncpg
        async def _check() -> bool:
            pool = await asyncpg.create_pool(cfg.database.url, min_size=1, max_size=1, server_settings={"search_path": "pmfi,public"})
            try:
                row = await pool.fetchrow("SELECT count(*) AS n FROM venues")
                print(f"DB OK — {row['n']} venue(s) registered")
                return True
            finally:
                await pool.close()
        ok = asyncio.run(_check())
        return 0 if ok else 1
    except Exception as exc:
        print(f"DB check failed: {exc}", file=sys.stderr)
        return 1


def cmd_db_maintenance(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    from pmfi.db.migrations import ensure_current_partitions, drop_old_partitions

    cfg = load_config()
    do_create = getattr(args, "create_partitions", False)
    do_prune = getattr(args, "prune_old_partitions", False)
    months_ahead = getattr(args, "months_ahead", 3)
    before_days = getattr(args, "before_days", cfg.ingestion.raw_retention_days)

    if not do_create and not do_prune:
        print("Specify --create-partitions and/or --prune-old-partitions")
        return 1

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            if do_create:
                await ensure_current_partitions(pool, months_ahead=months_ahead)
                print(f"Partitions created/verified for current + {months_ahead} months ahead.")
            if do_prune:
                dropped = await drop_old_partitions(pool, before_days=before_days)
                if dropped:
                    print(f"Dropped {len(dropped)} old partition(s): {', '.join(dropped)}")
                else:
                    print(f"No partitions older than {before_days} days found.")
        finally:
            await close_pool(pool)

    asyncio.run(_run())
    return 0


def cmd_dead_letters(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    import asyncpg
    cfg = load_config()
    limit = getattr(args, "limit", 20)

    async def _query():
        try:
            pool = await asyncpg.create_pool(
                cfg.database.url, min_size=1, max_size=1,
                server_settings={"search_path": "pmfi,public"},
            )
        except Exception as exc:
            return None, str(exc)
        try:
            rows = await pool.fetch(
                "SELECT dl.created_at, dl.venue_code, dl.failure_stage, dl.error_class, "
                "dl.error_message, dl.source_channel, LEFT(dl.payload::text, 120) AS payload_preview "
                "FROM dead_letters dl ORDER BY dl.created_at DESC LIMIT $1",
                limit,
            )
            return rows, None
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}")
        return 1
    if not rows:
        print("No dead letters — all events normalized successfully.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console(width=160)
        table = Table(title=f"Dead Letters ({len(rows)} recent)", show_lines=True)
        table.add_column("When", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Stage", min_width=14)
        table.add_column("Error", style="red", min_width=20)
        table.add_column("Payload (120 chars)", style="dim")
        for r in rows:
            table.add_row(
                str(r["created_at"])[5:16],
                r["venue_code"],
                r["failure_stage"],
                r["error_class"] or r["error_message"] or "—",
                r["payload_preview"] or "—",
            )
        console.print(table)
    except ImportError:
        for r in rows:
            print(f"{str(r['created_at'])[5:16]}  {r['venue_code']}  {r['failure_stage']}  {r['error_class']}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    cfg = load_config()

    async def _query():
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            return None, str(exc)
        try:
            raw_count = await pool.fetchval("SELECT COUNT(*) FROM raw_events")
            trade_count = await pool.fetchval("SELECT COUNT(*) FROM normalized_trades")
            alert_count = await pool.fetchval("SELECT COUNT(*) FROM alerts")
            market_count = await pool.fetchval("SELECT COUNT(*) FROM markets")
            baseline_count = await pool.fetchval("SELECT COUNT(*) FROM market_baselines")
            window_count = await pool.fetchval("SELECT COUNT(*) FROM metric_windows")
            dl_count = await pool.fetchval("SELECT COUNT(*) FROM dead_letters")
            ob_count = await pool.fetchval("SELECT COUNT(*) FROM orderbook_snapshots")
            last_event = await pool.fetchval("SELECT MAX(received_at) FROM raw_events")
            last_trade = await pool.fetchval("SELECT MAX(received_at) FROM normalized_trades")
            last_alert = await pool.fetchval("SELECT MAX(fired_at) FROM alerts")
            rule_counts = await pool.fetch(
                "SELECT rule_key, COUNT(*) AS cnt FROM alerts GROUP BY rule_key ORDER BY cnt DESC"
            )
            return {
                "raw_events": raw_count, "trades": trade_count, "alerts": alert_count,
                "markets": market_count, "baselines": baseline_count, "windows": window_count,
                "dead_letters": dl_count, "orderbook_snapshots": ob_count,
                "last_event": last_event, "last_trade": last_trade, "last_alert": last_alert,
                "rule_counts": rule_counts,
            }
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    result = asyncio.run(_query())
    if isinstance(result, tuple) and result[0] is None:
        print(f"DB query failed: {result[1]}")
        return 1

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title="PMFI DB Statistics")
        table.add_column("Table", style="cyan")
        table.add_column("Count", justify="right", style="yellow")
        table.add_row("raw_events", str(result["raw_events"]))
        table.add_row("normalized_trades", str(result["trades"]))
        table.add_row("dead_letters", str(result["dead_letters"]))
        table.add_row("metric_windows", str(result["windows"]))
        table.add_row("alerts", str(result["alerts"]))
        table.add_row("markets", str(result["markets"]))
        table.add_row("market_baselines", str(result["baselines"]))
        table.add_row("orderbook_snapshots", str(result["orderbook_snapshots"]))
        console.print(table)
        if result["last_event"]:
            console.print(f"Last event : [cyan]{str(result['last_event'])[:19]}[/cyan]")
        if result["last_trade"]:
            console.print(f"Last trade : [cyan]{str(result['last_trade'])[:19]}[/cyan]")
        if result["last_alert"]:
            console.print(f"Last alert : [cyan]{str(result['last_alert'])[:19]}[/cyan]")
        if result["rule_counts"]:
            rtable = Table(title="Alerts by Rule")
            rtable.add_column("Rule", style="yellow")
            rtable.add_column("Count", justify="right", style="cyan")
            for row in result["rule_counts"]:
                rtable.add_row(row["rule_key"], str(row["cnt"]))
            console.print(rtable)
    except ImportError:
        for k, v in result.items():
            if k != "rule_counts":
                print(f"{k}: {v}")
        for row in (result.get("rule_counts") or []):
            print(f"  {row['rule_key']}: {row['cnt']}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    cfg = load_config()
    interval = getattr(args, "interval", 5)
    limit = getattr(args, "limit", 15)
    rule_filter = getattr(args, "rule", None)
    venue_filter = getattr(args, "venue", None)
    severity_filter = getattr(args, "severity", None)

    async def _fetch_alerts(pool):
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
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)
        return await pool.fetch(
            f"SELECT a.alert_id, a.fired_at, a.rule_key, a.severity, a.confidence, a.score, "
            f"a.venue_code, a.outcome_key, a.data_quality, LEFT(m.title, 50) AS market_title "
            f"FROM alerts a LEFT JOIN markets m ON m.market_id = a.market_id "
            f"{where} ORDER BY a.fired_at DESC LIMIT ${idx}",
            *params,
        )

    async def _fetch_metrics(pool):
        row = await pool.fetchrow(
            "SELECT COUNT(*) AS alert_count, MAX(fired_at) AS last_alert FROM alerts"
        )
        return row

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.live import Live
        from rich.layout import Layout
        import asyncpg

        console = Console()

        async def _run():
            try:
                pool = await asyncpg.create_pool(
                    cfg.database.url, min_size=1, max_size=2,
                    server_settings={"search_path": "pmfi,public"},
                )
            except Exception as exc:
                console.print(f"[red]DB connect failed:[/red] {exc}")
                return

            def _build_table(rows, meta):
                table = Table(title=f"Recent Alerts (refresh every {interval}s, limit {limit})", width=160)
                table.add_column("ID", style="dim", no_wrap=True, min_width=8)
                table.add_column("When", style="cyan", no_wrap=True, min_width=11)
                table.add_column("Rule", style="yellow", min_width=32)
                table.add_column("Sev", style="red", min_width=4)
                table.add_column("Conf", min_width=6)
                table.add_column("DQ", min_width=10)
                table.add_column("Venue", style="green", min_width=10)
                table.add_column("Outcome", min_width=3)
                table.add_column("Score", min_width=6)
                table.add_column("Market", style="dim", min_width=20)
                for row in rows:
                    table.add_row(
                        str(row["alert_id"])[:8],
                        str(row["fired_at"])[5:16],
                        row["rule_key"],
                        row["severity"],
                        row["confidence"],
                        row.get("data_quality") or "—",
                        row["venue_code"],
                        row["outcome_key"] or "—",
                        str(row["score"])[:6],
                        row.get("market_title") or "—",
                    )
                total = meta["alert_count"] if meta else "?"
                last = str(meta["last_alert"])[:19] if meta and meta["last_alert"] else "—"
                footer = Panel(f"Total alerts: {total}  |  Last: {last}  |  Ctrl+C to exit", style="dim")
                from rich.console import Group
                return Group(table, footer)

            try:
                with Live(console=console, refresh_per_second=1) as live:
                    while True:
                        rows = await _fetch_alerts(pool)
                        meta = await _fetch_metrics(pool)
                        live.update(_build_table(rows, meta))
                        await asyncio.sleep(interval)
            except KeyboardInterrupt:
                pass
            finally:
                await pool.close()

        asyncio.run(_run())
    except ImportError:
        print("rich is required for pmfi watch. Run: pip install rich")
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate a summary report of recent alert activity."""
    import re
    from datetime import datetime, timezone, timedelta
    from pmfi.config import load_config
    from pmfi.db import create_pool

    # Parse --since
    since_dt = None
    since_raw = getattr(args, "since", "24h")
    m = re.match(r"^(\d+)([hdm])$", since_raw or "24h")
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"h": 3600, "d": 86400, "m": 60}[unit] * n
        since_dt = datetime.now(timezone.utc) - timedelta(seconds=delta)
    else:
        try:
            since_dt = datetime.fromisoformat(since_raw)
        except (ValueError, TypeError):
            since_dt = datetime.now(timezone.utc) - timedelta(hours=24)

    fmt = getattr(args, "format", "table")

    async def _run():
        from pmfi.db.repos.alerts import get_alert_summary
        cfg = load_config()
        pool = await create_pool(cfg.database.url)
        async with pool.acquire() as conn:
            summary = await get_alert_summary(conn, since=since_dt)
            # Also get DB row counts
            try:
                summary["raw_events"] = await conn.fetchval("SELECT COUNT(*) FROM raw_events")
                summary["normalized_trades"] = await conn.fetchval("SELECT COUNT(*) FROM normalized_trades")
                summary["dead_letters"] = await conn.fetchval("SELECT COUNT(*) FROM dead_letters")
            except Exception:
                pass
        await pool.close()
        return summary

    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        print(f"[report] DB unavailable: {exc}")
        print("  Run 'python scripts/db_local.py up' and 'pmfi replay --persist' first.")
        return 1

    if fmt == "json":
        import json as _json
        def _serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)
        print(_json.dumps(summary, indent=2, default=_serial))
        return 0

    # Table format
    print(f"\n=== PM-intel Report (since {since_dt.strftime('%Y-%m-%d %H:%M UTC')}) ===\n")
    print(f"Total alerts: {summary['total']}")

    if summary.get("by_severity"):
        sev_str = "  " + "  ".join(f"{r['severity']}={r['cnt']}" for r in summary["by_severity"])
        print(f"By severity:{sev_str}")
    if summary.get("by_venue"):
        venue_str = "  " + "  ".join(f"{r['venue_code']}={r['cnt']}" for r in summary["by_venue"])
        print(f"By venue:{venue_str}")
    if summary.get("by_rule"):
        print("\nAlert rules fired:")
        for r in summary["by_rule"]:
            print(f"  {r['rule_key']:<35} {r['cnt']:>4}x")

    if summary.get("top_markets"):
        print("\nMost alerted markets:")
        for r in summary["top_markets"]:
            print(f"  [{r['max_severity']:<6}] {r['title'][:60]:<60} {r['cnt']:>3}x")

    if summary.get("recent_high"):
        print("\nRecent high/medium alerts:")
        for r in summary["recent_high"]:
            ts = r["created_at"].strftime("%H:%M:%S") if hasattr(r["created_at"], "strftime") else str(r["created_at"])
            rv = r.get("rule_version") or "—"
            dq = r.get("data_quality") or "—"
            print(f"  {ts}  [{r['severity']:<6}] {r['rule_key']:<30} ver={rv}  dq={dq}  {r['title'][:40]}")

    # DB context
    if "raw_events" in summary:
        print(f"\nDB totals: raw_events={summary['raw_events']}  trades={summary.get('normalized_trades', '?')}  dead_letters={summary.get('dead_letters', '?')}")

    print()
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Read the daemon heartbeat and report freshness. Exit 0=fresh, 1=stale/missing."""
    from datetime import datetime, timezone
    from pmfi.health import (
        HEARTBEAT_PATH,
        read_heartbeat_ex,
        heartbeat_age_seconds,
        is_stale,
    )

    hb_path = Path(getattr(args, "heartbeat_path", None) or HEARTBEAT_PATH)
    max_age = getattr(args, "max_age_seconds", None)
    if max_age is None:
        # Default: 2x the telemetry interval (interval=60s → 120s)
        max_age = 120
    fmt = getattr(args, "json_output", False)

    # Load config for venue_stale_seconds and recompute settings (best-effort)
    _venue_stale_seconds_default = 600
    _recompute_interval_minutes = 1440
    _recompute_enabled = True
    try:
        from pmfi.config import load_config as _load_config
        _cfg = _load_config()
        _venue_stale_seconds_default = _cfg.health.venue_stale_seconds
        _recompute_interval_minutes = _cfg.baselines.recompute_interval_minutes
        _recompute_enabled = _cfg.baselines.recompute_enabled
    except Exception:
        pass

    venue_stale_sec = getattr(args, "venue_stale_seconds", None)
    if venue_stale_sec is None:
        venue_stale_sec = _venue_stale_seconds_default

    hb, error_kind = read_heartbeat_ex(hb_path)
    now = datetime.now(timezone.utc)
    age = heartbeat_age_seconds(hb, now) if hb else None
    stale = is_stale(hb, now, threshold_seconds=max_age)

    if fmt:
        import json as _json
        out = {
            "found": hb is not None,
            "stale": stale,
            "age_seconds": age,
            "max_age_seconds": max_age,
            "path": str(hb_path),
        }
        if hb:
            out.update({
                "ts": hb.get("ts"),
                "events_total": hb.get("events_total"),
                "alerts_total": hb.get("alerts_total"),
                "started_at": hb.get("started_at"),
                "pid": hb.get("pid"),
                "venues": hb.get("venues"),
                "last_recompute_at": hb.get("last_recompute_at"),
                "last_recompute_ok": hb.get("last_recompute_ok"),
                "last_recompute_error": hb.get("last_recompute_error"),
            })
        print(_json.dumps(out, indent=2))
    else:
        if hb is None:
            if error_kind == "not_found":
                print(
                    f"[health] No heartbeat file — daemon likely never started or never "
                    f"completed a cycle; expected at {hb_path}"
                )
                print("  Run 'pmfi ingest' to start the daemon.")
            else:
                # error_kind starts with "unreadable:"
                reason = error_kind.split(":", 1)[-1] if ":" in error_kind else error_kind
                print(f"[health] Heartbeat unreadable ({reason}) at {hb_path}")
                print("  Check file permissions or delete and restart 'pmfi ingest'.")
        else:
            age_str = f"{age:.1f}s" if age is not None else "unknown"
            status = "STALE" if stale else "fresh"
            print(
                f"[health] {status}  last_heartbeat={hb.get('ts', '?')}"
                f"  age={age_str}  events={hb.get('events_total', '?')}"
                f"  alerts={hb.get('alerts_total', '?')}"
            )
            if stale:
                # Include pid/started_at/ts so operator can check Task Manager
                print(f"  Heartbeat is older than {max_age}s threshold.")
                print(f"  pid={hb.get('pid', '?')}  started_at={hb.get('started_at', '?')}  ts={hb.get('ts', '?')}")
                print("  Check that 'pmfi ingest' is still running.")

            # Per-venue lines
            venues: dict = hb.get("venues") or {}
            if venues:
                print("[health] per-venue:")
                for vname, vdata in sorted(venues.items()):
                    vevents = vdata.get("events_total", 0)
                    vlast = vdata.get("last_event_at")
                    vfails = vdata.get("consecutive_failures", 0)
                    if vlast:
                        try:
                            _vlast_dt = datetime.fromisoformat(vlast)
                            if _vlast_dt.tzinfo is None:
                                from datetime import timezone as _tz
                                _vlast_dt = _vlast_dt.replace(tzinfo=_tz.utc)
                            _vage_s = (now - _vlast_dt).total_seconds()
                            vage_str = f"{_vage_s:.0f}s ago"
                            # Emit venue-stale warning (informational, no exit-code effect)
                            if _vage_s > venue_stale_sec:
                                print(
                                    f"  WARNING: venue {vname} stale "
                                    f"(last_event={vage_str}, threshold={venue_stale_sec}s)"
                                )
                        except Exception:
                            vage_str = "unknown"
                    else:
                        vage_str = "never"

                    fail_str = f"  consecutive_failures={vfails}" if vfails > 0 else ""
                    print(f"  {vname}: events={vevents}  last_event={vage_str}{fail_str}")

            # Recompute status
            lr_at = hb.get("last_recompute_at")
            lr_ok = hb.get("last_recompute_ok")
            lr_err = hb.get("last_recompute_error")
            if lr_at is not None:
                try:
                    _lr_dt = datetime.fromisoformat(lr_at)
                    if _lr_dt.tzinfo is None:
                        from datetime import timezone as _tz
                        _lr_dt = _lr_dt.replace(tzinfo=_tz.utc)
                    _lr_age_s = (now - _lr_dt).total_seconds()
                    lr_age_str = f"{_lr_age_s:.0f}s ago"
                    # Warn when last recompute failed
                    if lr_ok is False:
                        print(f"  WARNING: last baseline recompute FAILED ({lr_err or 'unknown error'})  at={lr_at}")
                    else:
                        print(f"  last_recompute: ok  at={lr_at}  ({lr_age_str})")
                    # Warn when overdue (only if recompute is enabled)
                    if _recompute_enabled:
                        overdue_threshold = _recompute_interval_minutes * 2 * 60
                        if _lr_age_s > overdue_threshold:
                            print(
                                f"  WARNING: baseline recompute overdue "
                                f"(last={lr_age_str}, expected every {_recompute_interval_minutes}min)"
                            )
                except Exception:
                    print(f"  last_recompute: {lr_at}  ok={lr_ok}")
            elif _recompute_enabled:
                print("  last_recompute: not yet run this session")

    return 1 if stale else 0
