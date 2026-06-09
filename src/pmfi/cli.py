from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


def _is_maintenance_cycle(cycle: int, every: int) -> bool:
    """Return True when *cycle* should trigger maintenance.

    Fires on cycle 1 (first interval after startup) and then every *every* cycles.
    """
    return cycle == 1 or (cycle % every == 0)


def _delivery_banner(mode: str, destination: str) -> str:
    """Return a multi-line startup banner describing the active alert delivery mode.

    Pure helper (no I/O) so it can be unit-tested directly.
    """
    lines = [
        "=" * 60,
        "[ingest] ALERT DELIVERY",
        f"  mode        : {mode}",
        f"  destination : {destination}",
    ]
    if mode == "file":
        lines += [
            "  Alerts are written durably to the path above.",
            "  They are ALSO always stored in the DB (insert_alert).",
        ]
    elif mode == "localhost_http_receiver":
        lines += [
            "  Alerts are POSTed to the local HTTP receiver above.",
            "  They are ALSO always stored in the DB (insert_alert).",
        ]
    else:
        lines += [
            "  WARNING: console mode is EPHEMERAL — alerts printed here",
            "  are lost when this terminal closes.",
            "  Recommend: set alerts.default_delivery: file in app.yaml",
            "  for durable on-disk storage.",
        ]
    lines += [
        "  Alert history is always queryable regardless of delivery mode:",
        "    pmfi alerts list   — recent alerts from DB",
        "    pmfi watch         — live tail from DB",
        "    pmfi dashboard     — browser view (localhost)",
        "=" * 60,
    ]
    return "\n".join(lines)


def cmd_replay(args: argparse.Namespace) -> int:
    from pmfi.delivery.stdout import deliver_stdout

    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else ROOT / "tests" / "fixtures" / "raw"

    if getattr(args, "from_db", False):
        from pmfi.config import load_config
        from pmfi.db import create_pool, close_pool
        from pmfi.replay import replay_from_db

        limit = getattr(args, "limit", 100)

        async def _run_from_db():
            cfg = load_config()
            pool = await create_pool(cfg.database.url)
            try:
                # DB-canonical: always load baselines from DB; never prefer stale JSON file
                return await replay_from_db(pool, limit=limit, verbose=args.verbose, baselines=None)
            finally:
                await close_pool(pool)

        results = asyncio.run(_run_from_db())
        print(f"[from-db] replayed {len(results)} raw_event(s) from Postgres")
    elif getattr(args, "persist", False):
        from pmfi.config import load_config
        from pmfi.db import create_pool, close_pool
        from pmfi.db.migrations import ensure_current_partitions
        from pmfi.replay import replay_fixtures_persist

        async def _run_persist():
            cfg = load_config()
            pool = await create_pool(cfg.database.url)
            try:
                await ensure_current_partitions(pool)
                # DB-canonical: always load baselines from DB; never prefer stale JSON file
                return await replay_fixtures_persist(fixture_dir, pool, verbose=args.verbose, baselines=None)
            finally:
                await close_pool(pool)

        results = asyncio.run(_run_persist())
        print(f"[persist] wrote {len(results)} fixture(s) through DB pipeline")
    else:
        # Pure-fixture path (no DB): file baselines acceptable as fallback
        _baselines = None
        _baselines_path = ROOT / "config" / "baselines.json"
        if _baselines_path.exists():
            import json as _json
            try:
                _baselines = _json.loads(_baselines_path.read_text(encoding="utf-8"))
                logging.debug("loaded %d baseline(s) from %s", len(_baselines), _baselines_path)
            except Exception:
                pass
        from pmfi.replay import replay_fixtures
        results = replay_fixtures(fixture_dir, verbose=args.verbose, baselines=_baselines)

    alert_count = sum(len(r.alerts) for r in results)
    for r in results:
        for d in r.alerts:
            asyncio.run(deliver_stdout(d, venue_code=r.trade.venue_code, market_id=r.trade.venue_market_id))
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title=f"Replay: {len(results)} fixtures, {alert_count} alerts")
        table.add_column("Fixture", style="cyan")
        table.add_column("Venue", style="green")
        table.add_column("Market", style="yellow")
        table.add_column("Alerts", style="red")
        for r in results:
            table.add_row(
                Path(r.fixture_path).name,
                r.trade.venue_code,
                r.trade.venue_market_id[:40],
                str(len(r.alerts)),
            )
        console.print(table)
    except ImportError:
        print(f"replay complete: {len(results)} fixtures, {alert_count} alerts")
    return 0


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


def cmd_monitor(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.delivery.stdout import deliver_stdout

    cfg = load_config()
    fixture_replay = getattr(args, "fixture_replay", False)

    if fixture_replay:
        fixture_dir = Path(args.fixture_dir) if getattr(args, "fixture_dir", None) else ROOT / "tests" / "fixtures" / "raw"
        delay = getattr(args, "delay", 1.0)

        async def _stream():
            from pmfi.fixtures import load_raw_event
            from pmfi.pipeline.normalize import normalize_event
            from pmfi.db import create_pool, close_pool
            from pmfi.baseline import load_baselines
            baselines: dict = {}
            pool = None
            try:
                pool = await create_pool(cfg.database.url)
                baselines = await load_baselines(pool)
                if baselines:
                    print(f"Loaded {len(baselines)} baseline(s) from DB.")
            except Exception:
                pass
            engine = AlertEngine(baselines=baselines)
            fixtures = sorted(fixture_dir.glob("*.json"))
            print(f"Streaming {len(fixtures)} fixture(s) (delay={delay}s). Press Ctrl+C to stop.")
            total_alerts = 0
            for path in fixtures:
                try:
                    raw = load_raw_event(path)
                except Exception:
                    continue
                print(f"\n[{path.name}] venue={raw.venue_code} market={raw.venue_market_id}")
                await asyncio.sleep(delay)
                trade = normalize_event(raw)
                if trade is None:
                    print("  normalization failed")
                    continue
                decisions = engine.evaluate(trade)
                if decisions:
                    for d in decisions:
                        await deliver_stdout(d, venue_code=trade.venue_code, market_id=trade.venue_market_id)
                        total_alerts += 1
                else:
                    print("  no alert")
            print(f"\nStream complete: {total_alerts} alert(s) from {len(fixtures)} fixture(s).")
            if pool:
                await pool.close()

        try:
            asyncio.run(_stream())
        except KeyboardInterrupt:
            print("\nMonitor stopped.")
        return 0

    if not cfg.live_mode_enabled and not cfg.features.enable_polymarket_live and not cfg.features.enable_kalshi_live:
        print("Live mode is disabled. Use --fixture-replay for a streaming demo, or set live_mode_enabled=true in config.")
        print("Example: pmfi monitor --fixture-replay --delay 2")
        return 0

    print("Live WebSocket monitor requires enable_polymarket_live or enable_kalshi_live in config.")
    print("Use 'pmfi monitor --fixture-replay' to test the pipeline with fixture data.")
    return 0


def cmd_alerts_list(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    import asyncpg

    limit = getattr(args, "limit", None) or 20
    show_evidence = getattr(args, "evidence", False)
    rule_filter = getattr(args, "rule", None)
    venue_filter = getattr(args, "venue", None)
    severity_filter = getattr(args, "severity", None)
    market_filter = getattr(args, "market", None)
    fmt = getattr(args, "format", "table")

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
            ev_col = ", a.evidence" if show_evidence else ""
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
                conditions.append(f"m.title ILIKE ${idx}")
                params.append(f"%{market_filter}%"); idx += 1
            if since_dt is not None:
                conditions.append(f"a.fired_at >= ${idx}")
                params.append(since_dt); idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            rows = await pool.fetch(
                f"SELECT a.fired_at, a.rule_key, a.rule_version, a.severity, a.confidence, a.score, "
                f"a.venue_code, a.outcome_key, a.data_quality, LEFT(m.title, 60) AS market_title, "
                f"mo.outcome_label{ev_col} "
                f"FROM alerts a "
                f"LEFT JOIN markets m ON m.market_id = a.market_id "
                f"LEFT JOIN market_outcomes mo ON mo.market_id = a.market_id AND mo.outcome_key = a.outcome_key "
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
        def _serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)
        print(_json.dumps([dict(r) for r in rows], indent=2, default=_serial))
        return 0

    count = len(rows)
    try:
        from rich.console import Console
        from rich.table import Table
        # Force 140 cols so rule names and timestamps never wrap/truncate.
        console = Console(width=140)
        table = Table(title=f"Recent Alerts (DB, last {count})", show_lines=show_evidence)
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
                when,
                row["rule_key"],
                row.get("rule_version") or "—",
                row["severity"],
                row["confidence"],
                row.get("data_quality") or "—",
                row["venue_code"],
                row["outcome_key"] or "—",
                row.get("outcome_label") or "—",
                str(row["score"])[:6],
                title,
            ]
            if show_evidence:
                cells.append(ev_cell)
            table.add_row(*cells)
        console.print(table)
    except ImportError:
        for row in rows:
            print(f"{str(row['fired_at'])[5:16]}  {row['rule_key']}  {row['severity']}  {row['venue_code']}  {row['outcome_key']}")
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


def cmd_alerts(args: argparse.Namespace) -> int:
    alerts_cmd = getattr(args, "alerts_cmd", None)
    if alerts_cmd == "serve":
        return cmd_alerts_serve(args)
    # Default: list behavior (alerts_cmd is None or "list")
    return cmd_alerts_list(args)


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
        pool = await create_pool(cfg.database.url)
        try:
            raw_count = await pool.fetchval("SELECT COUNT(*) FROM raw_events")
            trade_count = await pool.fetchval("SELECT COUNT(*) FROM normalized_trades")
            alert_count = await pool.fetchval("SELECT COUNT(*) FROM alerts")
            market_count = await pool.fetchval("SELECT COUNT(*) FROM markets")
            baseline_count = await pool.fetchval("SELECT COUNT(*) FROM market_baselines")
            window_count = await pool.fetchval("SELECT COUNT(*) FROM metric_windows")
            dl_count = await pool.fetchval("SELECT COUNT(*) FROM dead_letters")
            last_event = await pool.fetchval("SELECT MAX(received_at) FROM raw_events")
            last_trade = await pool.fetchval("SELECT MAX(received_at) FROM normalized_trades")
            rule_counts = await pool.fetch(
                "SELECT rule_key, COUNT(*) AS cnt FROM alerts GROUP BY rule_key ORDER BY cnt DESC"
            )
            return {
                "raw_events": raw_count, "trades": trade_count, "alerts": alert_count,
                "markets": market_count, "baselines": baseline_count, "windows": window_count,
                "dead_letters": dl_count,
                "last_event": last_event, "last_trade": last_trade,
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
        console.print(table)
        if result["last_event"]:
            console.print(f"Last event : [cyan]{str(result['last_event'])[:19]}[/cyan]")
        if result["last_trade"]:
            console.print(f"Last trade : [cyan]{str(result['last_trade'])[:19]}[/cyan]")
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
            f"SELECT a.fired_at, a.rule_key, a.severity, a.confidence, a.score, "
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


def cmd_markets(args: argparse.Namespace) -> int:
    markets_cmd = getattr(args, "markets_cmd", None) or "list"

    if markets_cmd == "discover":
        return _cmd_markets_discover(args)
    elif markets_cmd == "fetch-trades":
        return _cmd_markets_fetch_trades(args)
    if markets_cmd == "watch":
        return _cmd_markets_set_watched(args, watched=True)
    if markets_cmd == "unwatch":
        return _cmd_markets_set_watched(args, watched=False)
    return _cmd_markets_list(args)


def _cmd_markets_list(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    cfg = load_config()
    limit = getattr(args, "limit", 20)
    watched_only = getattr(args, "watched", False)
    search = getattr(args, "search", None)

    async def _query():
        pool = await create_pool(cfg.database.url)
        try:
            conditions: list[str] = []
            params: list = []
            idx = 1
            if watched_only:
                conditions.append("m.watched=true")
            if search:
                conditions.append(f"m.title ILIKE ${idx}")
                params.append(f"%{search}%"); idx += 1
            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
            params.append(limit)
            rows = await pool.fetch(
                f"""
                SELECT m.venue_code, m.venue_market_id, m.title, m.status, m.watched,
                       COUNT(t.trade_id) AS trade_count,
                       MAX(t.received_at) AS last_trade_at
                FROM markets m
                LEFT JOIN normalized_trades t ON t.market_id = m.market_id
                {where}
                GROUP BY m.market_id, m.venue_code, m.venue_market_id, m.title, m.status, m.watched
                ORDER BY last_trade_at DESC NULLS LAST
                LIMIT ${idx}
                """,
                *params,
            )
            return rows, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}")
        return 1
    if not rows:
        if watched_only:
            print("No watched markets. Use 'pmfi markets list' to see all markets, then 'pmfi markets watch <market_id>'.")
        else:
            print("No markets in DB. Run 'pmfi replay --persist' or 'pmfi markets discover' to populate.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        tbl_title = f"Watched Markets ({len(rows)})" if watched_only else f"Markets ({len(rows)})"
        table = Table(title=tbl_title, width=160)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Question / Title", style="cyan", min_width=40)
        table.add_column("Status", min_width=6)
        table.add_column("W", min_width=1)
        table.add_column("Trades", justify="right", style="yellow", min_width=5)
        table.add_column("Last Trade", style="dim", min_width=10, no_wrap=True)
        for r in rows:
            w = "[green]y[/green]" if r["watched"] else "n"
            display_title = (r.get("title") or r["venue_market_id"])[:80]
            table.add_row(
                r["venue_code"], display_title,
                r["status"] or "active", w,
                str(r["trade_count"]),
                str(r["last_trade_at"])[5:16] if r["last_trade_at"] else "—",
            )
        console.print(table)
    except ImportError:
        for r in rows:
            w = "watched" if r.get("watched") else ""
            print(f"{r['venue_code']}:{r['venue_market_id']}  {w}")
    return 0


def _cmd_markets_discover(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    cfg = load_config()
    limit = getattr(args, "limit", 100)
    min_volume = getattr(args, "min_volume", None)
    venue = getattr(args, "venue", "polymarket")

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            if venue == "kalshi":
                from pmfi.markets import sync_kalshi_markets
                return await sync_kalshi_markets(pool, limit=limit, min_volume=min_volume)
            else:
                from pmfi.markets import sync_polymarket_markets
                return await sync_polymarket_markets(pool, limit=limit, min_volume=min_volume)
        finally:
            await close_pool(pool)

    venue_label = "Kalshi" if venue == "kalshi" else "Polymarket"
    print(f"Fetching up to {limit} active {venue_label} markets...")
    try:
        count = asyncio.run(_run())
        print(f"Synced {count} market(s) to DB. Run 'pmfi markets list' to review.")
    except Exception as exc:
        print(f"Discover failed: {exc}")
        return 1
    return 0


def _cmd_markets_fetch_trades(args: argparse.Namespace) -> int:
    """Fetch recent Kalshi trades from REST API and optionally save as replay fixtures."""
    enable_live = os.environ.get("PMFI_ENABLE_LIVE") == "1"
    force = getattr(args, "force", False)
    if not enable_live and not force:
        print("fetch-trades requires: $env:PMFI_ENABLE_LIVE = '1'")
        print("Or use --force to skip the safety gate.")
        return 1

    from pmfi.markets import fetch_kalshi_trades, kalshi_trade_to_raw_event

    ticker = args.ticker
    limit = getattr(args, "limit", 50)
    save_fixtures = getattr(args, "save_fixtures", False)

    async def _run():
        return await fetch_kalshi_trades(ticker, limit=limit)

    print(f"[fetch-trades] Fetching up to {limit} recent trades for {ticker}...")
    try:
        trades = asyncio.run(_run())
    except Exception as exc:
        print(f"[fetch-trades] Failed: {exc}")
        return 1

    print(f"[fetch-trades] Got {len(trades)} trade(s)")

    if save_fixtures and trades:
        import json as _json
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fix_dir = ROOT / "tests" / "fixtures" / "live"
        fix_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, trade in enumerate(trades):
            raw = kalshi_trade_to_raw_event(trade, ticker)
            path = fix_dir / f"kalshi_rest_{ticker}_{ts}_{i:03d}.json"
            try:
                fixture_data = {
                    "venue_code": raw.venue_code,
                    "source_channel": raw.source_channel,
                    "source_event_type": raw.source_event_type,
                    "source_event_id": raw.source_event_id,
                    "venue_market_id": raw.venue_market_id,
                    "exchange_ts": raw.exchange_ts.isoformat() if raw.exchange_ts else None,
                    "received_at": raw.received_at.isoformat(),
                    "payload": raw.payload,
                }
                path.write_text(_json.dumps(fixture_data, indent=2, default=str), encoding="utf-8")
                saved += 1
            except Exception as exc:
                print(f"  [save] error on #{i}: {exc}")
        print(f"[fetch-trades] saved {saved} fixture(s) to {fix_dir}")
        print("  Run 'pmfi replay' to normalize and evaluate alerts from these fixtures.")

    return 0


def _cmd_markets_set_watched(args: argparse.Namespace, *, watched: bool) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    from pmfi.db.repos.markets import set_market_watched
    cfg = load_config()
    venue_market_id = args.market_id
    venue = getattr(args, "venue", "polymarket")

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            async with pool.acquire() as conn:
                found = await set_market_watched(conn, venue_code=venue, venue_market_id=venue_market_id, watched=watched)
            return found
        finally:
            await close_pool(pool)

    found = asyncio.run(_run())
    action = "watched" if watched else "unwatched"
    if found:
        print(f"Market {venue}:{venue_market_id} marked as {action}.")
    else:
        print(f"Market not found: {venue}:{venue_market_id}. Run 'pmfi markets discover' first.")
        return 1
    return 0


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


def _cmd_baselines_compute(args: argparse.Namespace) -> int:
    """Compute baselines from normalized_trades and store to DB (canonical) and optionally JSON."""
    # NOTE: This command uses normalized_trades.capital_at_risk_usd (per-trade percentiles)
    # which is more accurate than the older 'pmfi baseline compute' path that uses
    # metric_windows.max_trade_capital_at_risk_usd window aggregates. Prefer this command.
    # Baselines are stored in the DB market_baselines table and picked up automatically
    # by 'pmfi ingest', 'pmfi live', 'pmfi replay', and 'pmfi monitor'.
    import asyncio
    import json as _json
    from pmfi.config import load_config
    from pmfi.db import create_pool

    cfg = load_config()
    days = getattr(args, "days", 30)
    min_samples = getattr(args, "min_samples", 10)
    save = getattr(args, "save", False)

    async def _run():
        from pmfi.db import create_pool, close_pool
        from pmfi.baseline import compute_and_store_baselines
        pool = await create_pool(cfg.database.url)
        try:
            baselines = await compute_and_store_baselines(pool, window_days=days, min_samples=min_samples)
        finally:
            await close_pool(pool)
        return baselines

    try:
        baselines = asyncio.run(_run())
    except Exception as exc:
        print(f"[baselines compute] Failed: {exc}")
        return 1

    if not baselines:
        print(f"[baselines compute] No markets with >= {min_samples} trades in last {days} days.")
        print("  Run 'pmfi replay --persist' first to populate normalized_trades.")
        return 0

    print(f"[baselines compute] Stored baselines for {len(baselines)} market(s) to DB.")
    print("  These are now used automatically by 'pmfi ingest', 'pmfi live', and 'pmfi replay'.")
    for key, vals in sorted(baselines.items())[:20]:
        print(f"  {key}: p99=${vals['p99_trade_usd']:.0f} p99.5=${vals['p995_trade_usd']:.0f} n={vals['sample_size']}")
    if len(baselines) > 20:
        print(f"  ... and {len(baselines) - 20} more")

    if save:
        out_path = ROOT / "config" / "baselines.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Strip internal market_id from the JSON file (not needed for portability).
        json_baselines = {
            k: {ek: ev for ek, ev in v.items() if ek != "market_id"}
            for k, v in baselines.items()
        }
        out_path.write_text(_json.dumps(json_baselines, indent=2), encoding="utf-8")
        print(f"[baselines compute] Also saved portable JSON to {out_path}")
        print("  (JSON file is optional — 'pmfi ingest'/'live'/'replay' read the DB directly.)")

    return 0


def _cmd_baselines_show(args: argparse.Namespace) -> int:
    """Show current baselines from the DB (canonical), falling back to config/baselines.json."""
    import asyncio
    import json as _json
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    from pmfi.baseline import load_baselines

    async def _load_db() -> dict:
        cfg = load_config()
        pool = await create_pool(cfg.database.url)
        try:
            return await load_baselines(pool)
        finally:
            await close_pool(pool)

    data: dict = {}
    source = "DB market_baselines"
    try:
        data = asyncio.run(_load_db())
    except Exception as exc:
        print(f"[baselines show] DB read failed ({exc}); trying config/baselines.json")

    if not data:
        path = ROOT / "config" / "baselines.json"
        if path.exists():
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
                source = str(path)
            except Exception as exc:
                print(f"[baselines show] Failed to read {path}: {exc}")
                return 1

    if not data:
        print("[baselines show] No baselines found in DB or config/baselines.json.")
        print("  Run 'pmfi baselines compute' to populate the DB (used by ingest/live/replay).")
        return 1

    print(f"[baselines show] {len(data)} market baseline(s) (source: {source}):")
    for key, vals in sorted(data.items()):
        p99 = vals.get("p99_trade_usd") or 0
        p995 = vals.get("p995_trade_usd") or 0
        print(f"  {key}: p99=${float(p99):.0f}  p99.5=${float(p995):.0f}  n={vals.get('sample_size', 0)}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """DEPRECATED command group. Use 'pmfi baselines' (plural) instead.

    'pmfi baseline compute' is aliased to 'pmfi baselines compute' with a deprecation notice.
    'pmfi baseline list' is aliased to 'pmfi baselines show' with a deprecation notice.
    """
    if args.baseline_cmd == "compute":
        print(
            "[baseline compute] DEPRECATED: 'pmfi baseline compute' is an alias for "
            "'pmfi baselines compute'. Please update your scripts to use "
            "'pmfi baselines compute' (plural)."
        )
        # Delegate to the canonical path: build a compatible Namespace and call the canonical handler.
        import copy
        canonical_args = copy.copy(args)
        canonical_args.baselines_cmd = "compute"
        # Map --lookback-days (7-day default) to --days (30-day default in canonical path)
        canonical_args.days = getattr(args, "lookback_days", 7)
        canonical_args.min_samples = 10
        canonical_args.save = False
        return _cmd_baselines_compute(canonical_args)

    if args.baseline_cmd == "list":
        print(
            "[baseline list] DEPRECATED: 'pmfi baseline list' is an alias for "
            "'pmfi baselines show'. Please update your scripts to use "
            "'pmfi baselines show' (plural)."
        )
        import copy
        canonical_args = copy.copy(args)
        canonical_args.baselines_cmd = "show"
        return _cmd_baselines_show(canonical_args)

    return 0


def cmd_live_smoke(args: argparse.Namespace) -> int:
    """Bounded opt-in live smoke: connect to venue WS, capture N events in T seconds.

    Requires PMFI_ENABLE_LIVE=1 env var or --force.
    """
    enable_live = os.environ.get("PMFI_ENABLE_LIVE") == "1"
    force = getattr(args, "force", False)
    if not enable_live and not force:
        print("Live smoke requires: $env:PMFI_ENABLE_LIVE = '1'")
        print("Or use --force to skip the safety gate.")
        print("Example: $env:PMFI_ENABLE_LIVE = '1'; python -m pmfi.cli live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw")
        return 1

    from pmfi.config import load_config
    from pmfi.adapters.polymarket import PolymarketAdapter
    from pmfi.pipeline.normalize import normalize_event
    from pmfi.delivery.stdout import deliver_stdout

    cfg = load_config()
    venue = getattr(args, "venue", "polymarket")
    if venue == "kalshi":
        force_kalshi = getattr(args, "force", False)
        if not force_kalshi:
            print("[live-smoke] Kalshi WS live smoke requires --force to attempt (auth uncertain).")
            print("  For Kalshi data, use the REST lane instead:")
            print("    pmfi markets discover --venue kalshi --limit 20")
            print("    pmfi markets fetch-trades <ticker> --save-fixtures")
            return 1
        print("[live-smoke] Kalshi WS live smoke not yet fully implemented. Use REST lane.")
        return 1
    max_events = getattr(args, "max_events", 50)
    max_seconds = getattr(args, "max_seconds", 120)
    save_fixtures = getattr(args, "save_fixtures", False)
    persist_raw = getattr(args, "persist_raw", False)

    raw_asset_ids = getattr(args, "asset_ids", None) or ""
    asset_ids = [a.strip() for a in raw_asset_ids.split(",") if a.strip()] if raw_asset_ids else []

    # If no asset_ids provided, load from market_outcomes (same source as cmd_ingest)
    if not asset_ids and venue == "polymarket":
        async def _get_watched_asset_ids() -> list[str]:
            from pmfi.db import create_pool, close_pool
            from pmfi.db.repos.markets import fetch_watched_markets
            from pmfi.markets import load_asset_id_mapping
            try:
                pool = await create_pool(cfg.database.url)
                try:
                    async with pool.acquire() as conn:
                        watched = await fetch_watched_markets(conn)
                    asset_id_map = await load_asset_id_mapping(pool)
                    return _resolve_poly_token_ids(watched, asset_id_map)
                finally:
                    await close_pool(pool)
            except Exception:
                return []

        try:
            asset_ids = asyncio.run(_get_watched_asset_ids())
        except Exception:
            asset_ids = []

    asset_id_desc = f"asset_ids={asset_ids[:3]}{'...' if len(asset_ids) > 3 else ''}" if asset_ids else "(no asset IDs — will not subscribe)"
    print(f"[live-smoke] venue={venue} max_events={max_events} max_seconds={max_seconds}")
    print(f"[live-smoke] subscription: {asset_id_desc}")
    if not asset_ids and venue == "polymarket":
        print("[live-smoke] ERROR: Polymarket live smoke requires asset IDs (token IDs) to subscribe.")
        print("  Without asset IDs, PolymarketAdapter connects but receives no trade events.")
        print("  Options:")
        print("    1. Run 'pmfi markets discover' then 'pmfi markets watch <id>' to populate the DB")
        print("    2. Pass --asset-ids <token_id1,token_id2,...> directly")
        return 1

    captured_events: list = []

    async def _run() -> int:
        pool = None
        engine = None

        if persist_raw:
            from pmfi.db import create_pool, close_pool
            from pmfi.db.migrations import ensure_current_partitions
            from pmfi.pipeline.engine import AlertEngine
            from pmfi.pipeline.runner import run_adapter_pipeline
            from pmfi.baseline import load_baselines

            pool = await create_pool(cfg.database.url)
            await ensure_current_partitions(pool)
            try:
                baselines = await load_baselines(pool)
            except Exception:
                baselines = {}
            engine = AlertEngine(baselines=baselines)
            from pmfi.markets import load_asset_id_mapping as _load_map
            try:
                _live_smoke_asset_id_map = await _load_map(pool)
            except Exception:
                _live_smoke_asset_id_map = {}

        try:
            adapter = PolymarketAdapter(
                asset_ids=asset_ids,
                timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                max_backoff=cfg.ingestion.reconnect_max_backoff,
            )

            # Intercept events to capture them for fixtures, then yield on.
            async def _capturing_events():
                async for raw in adapter.events():
                    captured_events.append(raw)
                    event_type = raw.source_event_type or "?"
                    market = (raw.venue_market_id or "?")[:40]
                    print(f"  [#{len(captured_events)}] type={event_type} market={market}")
                    yield raw

            events_source = _capturing_events()

            if persist_raw and pool and engine:
                from pmfi.pipeline.runner import run_adapter_pipeline

                async def _deliver(decision, vc, mid):
                    await deliver_stdout(decision, venue_code=vc, market_id=mid)

                processed = 0
                async with adapter:
                    try:
                        processed = await asyncio.wait_for(
                            run_adapter_pipeline(
                                events_source, pool, engine, _deliver,
                                max_events=max_events,
                                suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                                asset_id_map=_live_smoke_asset_id_map,
                            ),
                            timeout=max_seconds,
                        )
                    except asyncio.TimeoutError:
                        print(f"[live-smoke] reached max_seconds={max_seconds}")
                return processed
            else:
                # Capture only — no DB writes
                await adapter.connect()
                try:
                    async def _capture_only():
                        async for _ in events_source:
                            if len(captured_events) >= max_events:
                                break
                    try:
                        await asyncio.wait_for(_capture_only(), timeout=max_seconds)
                    except asyncio.TimeoutError:
                        print(f"[live-smoke] reached max_seconds={max_seconds}")
                finally:
                    await adapter.disconnect()
                return len(captured_events)

        finally:
            if pool:
                from pmfi.db import close_pool
                await close_pool(pool)

    try:
        total = asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[live-smoke] stopped by user.")
        total = len(captured_events)

    # Save fixtures if requested
    if save_fixtures and captured_events:
        import json as _json
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fix_dir = ROOT / "tests" / "fixtures" / "live"
        fix_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, raw in enumerate(captured_events):
            path = fix_dir / f"{venue}_smoke_{ts}_{i:03d}.json"
            try:
                fixture_data = {
                    "venue_code": raw.venue_code,
                    "source_channel": raw.source_channel,
                    "source_event_type": raw.source_event_type,
                    "source_event_id": raw.source_event_id,
                    "venue_market_id": raw.venue_market_id,
                    "exchange_ts": raw.exchange_ts.isoformat() if raw.exchange_ts else None,
                    "received_at": raw.received_at.isoformat(),
                    "payload": raw.payload,
                }
                path.write_text(_json.dumps(fixture_data, indent=2, default=str), encoding="utf-8")
                saved += 1
            except Exception as exc:
                print(f"  [save-fixture] error on #{i}: {exc}")
        print(f"[live-smoke] saved {saved} fixture(s) to {fix_dir}")

    print(f"\n[live-smoke] done: {total} event(s) processed, {len(captured_events)} captured")

    if persist_raw:
        print("[live-smoke] run 'pmfi stats' and 'pmfi alerts list' to inspect DB results")

    return 0


def cmd_live(args: argparse.Namespace) -> int:
    """Continuous live capture: connects to WS and processes events indefinitely.

    Auto-reconnects on disconnect. Ctrl+C to stop.
    PMFI_ENABLE_LIVE=1 required.
    """
    import asyncio
    import signal
    enable_live = os.environ.get("PMFI_ENABLE_LIVE") == "1"
    if not enable_live:
        print("pmfi live requires: $env:PMFI_ENABLE_LIVE = '1'")
        return 1

    venue = getattr(args, "venue", "polymarket")
    if venue != "polymarket":
        print(f"[live] Venue '{venue}' not yet supported for continuous capture. Use: polymarket")
        return 1

    from pmfi.config import load_config
    from pmfi.db import create_pool
    from pmfi.adapters.polymarket import PolymarketAdapter
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline
    from pmfi.markets import load_asset_id_mapping
    from pmfi.baseline import load_baselines

    cfg = load_config()
    capture_orderbook = getattr(args, "orderbook", False)
    refresh_minutes = getattr(args, "refresh_map_minutes", 30)
    markets_raw = getattr(args, "markets", None)

    _baselines = None
    _baselines_path = ROOT / "config" / "baselines.json"
    if _baselines_path.exists():
        import json as _json
        try:
            _baselines = _json.loads(_baselines_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    async def _alert_handler(decision, venue_code, market_id):
        print(f"[ALERT] {decision.severity.upper():<6} rule={decision.rule_id} market={market_id} side={decision.evidence.get('dominant_side', '?')}")

    async def _run():
        pool = await create_pool(cfg.database.url)

        # Load watched condition IDs from args or DB
        if markets_raw:
            condition_ids = [m.strip() for m in markets_raw.split(",") if m.strip()]
        else:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT venue_market_id FROM markets WHERE venue_code = 'polymarket' AND watched = true LIMIT 50"
                )
                condition_ids = [r["venue_market_id"] for r in rows]
            if not condition_ids:
                print("[live] No watched markets found. Run 'pmfi markets discover' and 'pmfi markets watch <id>'.")
                await pool.close()
                return 1

        # Resolve condition IDs → asset_ids (token IDs required by PolymarketAdapter WS)
        async def _load_asset_ids() -> list[str]:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT mo.venue_outcome_id
                       FROM market_outcomes mo
                       JOIN markets m ON m.market_id = mo.market_id
                       WHERE mo.venue_code = 'polymarket'
                       AND m.venue_market_id = ANY($1::text[])""",
                    condition_ids,
                )
                return [r["venue_outcome_id"] for r in rows if r["venue_outcome_id"]]

        asset_ids = await _load_asset_ids()
        if not asset_ids:
            print("[live] No token IDs found for watched markets. Run 'pmfi markets discover' to populate asset_ids.")
            await pool.close()
            return 1

        # Prefer DB baselines (canonical, written by 'pmfi baselines compute'); fall
        # back to the optional config/baselines.json bootstrap only if the DB has none.
        _eff_baselines = await load_baselines(pool) or _baselines
        engine = AlertEngine(baselines=_eff_baselines)
        asset_id_map = await load_asset_id_mapping(pool)
        print(f"[live] Starting: venue=polymarket watched={len(condition_ids)} asset_ids={len(asset_ids)} baselines={len(_eff_baselines or {})}")
        print("[live] Ctrl+C to stop.")

        reconnect_delay = 5
        map_refresh_interval = refresh_minutes * 60
        last_map_refresh = asyncio.get_event_loop().time()
        total_processed = 0
        reconnect_count = 0

        stop_event = asyncio.Event()

        def _on_sigint():
            stop_event.set()

        loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        except (NotImplementedError, OSError):
            pass  # Windows may not support add_signal_handler

        try:
            while not stop_event.is_set():
                try:
                    # Refresh asset_id map and token IDs periodically
                    now = loop.time()
                    if now - last_map_refresh > map_refresh_interval:
                        asset_id_map = await load_asset_id_mapping(pool)
                        asset_ids = await _load_asset_ids()
                        last_map_refresh = now
                        print(f"[live] Refreshed: asset_ids={len(asset_ids)} map={len(asset_id_map)}")
                        try:
                            _fresh_baselines = await load_baselines(pool)
                            engine.update_baselines(_fresh_baselines)
                            print(f"[live] baselines refreshed ({len(_fresh_baselines)} market(s))")
                        except Exception as _bl_exc:
                            print(f"[live] baseline refresh failed (non-fatal): {_bl_exc}")

                    reconnect_count += 1
                    print(f"[live] Connecting... asset_ids={len(asset_ids)} (attempt {reconnect_count})")
                    adapter = PolymarketAdapter(
                        asset_ids=asset_ids,
                        timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                        initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                        max_backoff=cfg.ingestion.reconnect_max_backoff,
                    )
                    async with adapter:
                        processed = await run_adapter_pipeline(
                            adapter.events(),
                            pool,
                            engine,
                            _alert_handler,
                            capture_orderbook=capture_orderbook,
                            asset_id_map=asset_id_map,
                        )
                        total_processed += processed
                    reconnect_delay = 5  # reset on clean disconnect
                    print(f"[live] Stream ended cleanly. total={total_processed} reconnecting in {reconnect_delay}s...")
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    print(f"[live] Error: {exc}. Reconnecting in {reconnect_delay}s...")
                    reconnect_delay = min(reconnect_delay * 2, 120)

                if not stop_event.is_set():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=reconnect_delay)
                    except asyncio.TimeoutError:
                        pass
        except KeyboardInterrupt:
            pass
        finally:
            print(f"[live] Stopped. Total events processed: {total_processed}")
            await pool.close()
        return 0

    return asyncio.run(_run())


def _resolve_poly_token_ids(
    watched: list[dict],
    asset_id_map: dict[str, dict],
) -> list[str]:
    """Return Polymarket token IDs for watched markets resolved from market_outcomes.

    Returns an empty list when no outcomes have been synced yet (caller must decide
    whether to error or skip rather than falling back to condition IDs).
    """
    watched_poly_market_ids = {m["market_id"] for m in watched if m["venue_code"] == "polymarket"}
    return [
        token_id for token_id, info in asset_id_map.items()
        if info["venue_code"] == "polymarket" and info["market_id"] in watched_poly_market_ids
    ]


def _select_ingest_venues(
    venues: list[str],
    poly_ids: list[str],
    kalshi_tickers: list[str],
) -> "tuple[list[str], list[str]]":
    """Select enabled venues that have usable subscription targets; drop the rest.

    Pure function — no I/O. Returns (usable_venues, messages). A venue with no
    resolved targets is dropped with an informational message, so an operator
    running both venues but watching only one still ingests the usable venue
    instead of hard-failing. The caller hard-fails only when nothing is usable.
    """
    usable: list[str] = []
    messages: list[str] = []
    for v in venues:
        if v == "polymarket" and not poly_ids:
            messages.append(
                "Polymarket enabled but no token IDs resolved for watched markets; "
                "skipping it. Run 'pmfi markets discover --venue polymarket' then "
                "'pmfi markets watch <market_id>'."
            )
        elif v == "kalshi" and not kalshi_tickers:
            messages.append(
                "Kalshi enabled but no tickers among watched markets; skipping it. "
                "Run 'pmfi markets discover --venue kalshi' then "
                "'pmfi markets watch <market_id> --venue kalshi'."
            )
        else:
            usable.append(v)
    return usable, messages


def cmd_ingest(args: argparse.Namespace) -> int:
    """Persistent live ingest daemon. Ctrl+C to stop."""
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    from pmfi.db.migrations import startup_maintenance
    from pmfi.db.repos.markets import fetch_watched_markets
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import run_adapter_pipeline
    from pmfi.baseline import load_baselines
    from pmfi.delivery.stdout import deliver_stdout

    venues = getattr(args, "venue", []) or []
    dry_run = getattr(args, "dry_run", False)
    cfg = load_config()

    if not venues:
        if cfg.features.enable_polymarket_live:
            venues.append("polymarket")
        if cfg.features.enable_kalshi_live:
            venues.append("kalshi")

    if not venues:
        print("No live venues enabled. Set enable_polymarket_live=true in config/app.yaml.")
        print("Or pass --venue polymarket --venue kalshi explicitly.")
        return 1

    if dry_run:
        from pmfi.pipeline.normalize import normalize_event
        from pmfi.db import create_pool, close_pool
        from pmfi.db.repos.markets import fetch_watched_markets
        from pmfi.markets import load_asset_id_mapping
        _events_seen = [0]

        async def _run_dry():
            # Resolve asset IDs from market_outcomes (same logic as the real path) so
            # dry-run actually subscribes real token streams rather than nothing.
            pool = await create_pool(cfg.database.url)
            dry_venues = list(venues)
            try:
                async with pool.acquire() as conn:
                    watched = await fetch_watched_markets(conn)
                asset_id_map = await load_asset_id_mapping(pool)
                poly_ids = _resolve_poly_token_ids(watched, asset_id_map)
                kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]
            finally:
                await close_pool(pool)

            # Pre-flight mirrors the live path so --dry-run reports misconfig early.
            if not watched:
                print("[ingest] No watched markets. Run 'pmfi markets discover --venue <venue>' then 'pmfi markets watch <market_id>'.")
                return
            dry_venues, _dry_msgs = _select_ingest_venues(dry_venues, poly_ids, kalshi_tickers)
            for _m in _dry_msgs:
                print(f"[ingest] {_m}")
            if not dry_venues:
                print("[ingest] No usable subscriptions among watched markets. Nothing to ingest.")
                return

            tasks = []

            if "polymarket" in dry_venues:
                from pmfi.adapters.polymarket import PolymarketAdapter
                adapter = PolymarketAdapter(
                    asset_ids=poly_ids,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                    reconnect_jitter=cfg.ingestion.reconnect_jitter,
                )
                await adapter.connect()

                async def _dry_poly():
                    try:
                        async for raw in adapter.events():
                            _events_seen[0] += 1
                            trade = normalize_event(raw)
                            if trade:
                                print(f"[dry:poly] #{_events_seen[0]} market={trade.venue_market_id} price={trade.price} side={trade.directional_side}")
                            else:
                                print(f"[dry:poly] #{_events_seen[0]} norm-skip keys={list(raw.payload)}")
                    finally:
                        await adapter.disconnect()

                tasks.append(asyncio.create_task(_dry_poly()))

            if "kalshi" in dry_venues:
                from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
                adapter_k = KalshiRestPollingAdapter(
                    tickers=kalshi_tickers,
                    poll_interval_seconds=cfg.ingestion.kalshi_poll_interval_seconds,
                    timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                    reconnect_jitter=cfg.ingestion.reconnect_jitter,
                )
                await adapter_k.connect()

                async def _dry_kalshi():
                    try:
                        async for raw in adapter_k.events():
                            _events_seen[0] += 1
                            trade = normalize_event(raw)
                            if trade:
                                print(f"[dry:kalshi] #{_events_seen[0]} market={trade.venue_market_id} price={trade.price} side={trade.directional_side}")
                            else:
                                print(f"[dry:kalshi] #{_events_seen[0]} norm-skip keys={list(raw.payload)}")
                    finally:
                        await adapter_k.disconnect()

                tasks.append(asyncio.create_task(_dry_kalshi()))

            print(f"[dry-run] started {len(tasks)} adapter(s) for venues={dry_venues} — no DB writes. Ctrl+C to stop.")
            if tasks:
                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    pass

        try:
            asyncio.run(_run_dry())
        except KeyboardInterrupt:
            print("\n[dry-run] stopped.")
        return 0

    delivery_mode = cfg.alerts.default_delivery
    if delivery_mode == "file":
        from pmfi.delivery.file import FileDelivery as _FileDelivery
        _alerts_dir = ROOT / "reports" / "alerts"
        _file_delivery = _FileDelivery(_alerts_dir)
        _delivery_destination = str(_alerts_dir / "alerts_YYYY-MM-DD.jsonl")
        async def _deliver(decision, venue_code, market_id):
            await _file_delivery.deliver(decision, venue_code=venue_code, market_id=market_id)
    elif delivery_mode == "localhost_http_receiver":
        from pmfi.delivery.http import HttpDelivery as _HttpDelivery
        _http_delivery = _HttpDelivery()
        _delivery_destination = _http_delivery._endpoint
        async def _deliver(decision, venue_code, market_id):
            await _http_delivery.deliver(decision, venue_code=venue_code, market_id=market_id)
    else:
        _delivery_destination = "stdout (ephemeral)"
        async def _deliver(decision, venue_code, market_id):
            await deliver_stdout(decision, venue_code=venue_code, market_id=market_id)

    print(_delivery_banner(delivery_mode, _delivery_destination))

    async def _run():
        from pmfi.pipeline.supervisor import PoolManager, supervise as _supervise
        from pmfi.pipeline.runner import run_adapter_pipeline

        pm = PoolManager(
            cfg.database.url,
            min_size=cfg.database.pool_min_size,
            max_size=cfg.database.pool_max_size,
        )
        await pm.open()
        shutdown = asyncio.Event()
        tasks: list = []  # bound before try so the finally is safe on early-return paths
        try:
            await startup_maintenance(pm.pool)
            baselines = {}
            try:
                baselines = await load_baselines(pm.pool)
            except Exception:
                pass

            engine = AlertEngine(baselines=baselines)

            async with pm.pool.acquire() as conn:
                watched = await fetch_watched_markets(conn)

            # Polymarket WS subscriptions require token IDs (asset_ids from market_outcomes),
            # not condition IDs (venue_market_id). Resolve via the shared helper.
            from pmfi.markets import load_asset_id_mapping
            asset_id_map = await load_asset_id_mapping(pm.pool)
            poly_ids = _resolve_poly_token_ids(watched, asset_id_map)
            kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]

            # Pre-flight: drop venues with no usable subscriptions (with guidance) and
            # fail fast BEFORE starting any adapter / printing the banner if none remain.
            if not watched:
                print("[ingest] No watched markets. Run 'pmfi markets discover --venue <venue>' then 'pmfi markets watch <market_id>'.")
                return 1
            live_venues, _venue_msgs = _select_ingest_venues(list(venues), poly_ids, kalshi_tickers)
            for _m in _venue_msgs:
                print(f"[ingest] {_m}")
            if not live_venues:
                print("[ingest] No usable subscriptions among watched markets (no resolved Polymarket tokens / Kalshi tickers). Nothing to ingest.")
                return 1

            # Shared telemetry counters (mutable lists for closure capture)
            _events_seen = [0]
            _alerts_fired = [0]

            async def alert_handler(decision, venue_code, market_id):
                _alerts_fired[0] += 1
                await _deliver(decision, venue_code, market_id)

            async def _counted_events(source):
                async for raw in source:
                    _events_seen[0] += 1
                    yield raw

            from datetime import datetime as _dt, timezone as _tz
            from pmfi.health import write_heartbeat as _write_heartbeat, HEARTBEAT_PATH as _HB_PATH
            from pmfi.db.migrations import find_partitions_older_than as _find_old_partitions
            from pmfi.db.migrations import ensure_current_partitions as _ensure_partitions

            _ingest_started_at = _dt.now(_tz.utc)
            # Cadence constants (all in cycles; default interval=60s so daily≈1440)
            _BASELINE_REFRESH_CYCLES = 10    # refresh baselines every ~10 min
            _PARTITION_MAINT_CYCLES = 1440   # daily partition maintenance (60s interval)

            # Write an initial heartbeat right after preflight so `pmfi health`
            # works within the first interval without waiting for cycle 1.
            try:
                _write_heartbeat(
                    _HB_PATH,
                    events_total=_events_seen[0],
                    alerts_total=_alerts_fired[0],
                    started_at=_ingest_started_at,
                    now=_dt.now(_tz.utc),
                )
            except Exception as _hb_exc:
                print(f"[ingest] heartbeat write failed (non-fatal): {_hb_exc}")

            async def _telemetry_loop(interval: int = 60):
                last = 0
                cycle = 0
                while not shutdown.is_set():
                    try:
                        await asyncio.wait_for(shutdown.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
                    if shutdown.is_set():
                        break
                    cycle += 1
                    total = _events_seen[0]
                    delta = total - last
                    last = total
                    print(f"[ingest] events_total={total} (+{delta}/{interval}s) alerts_total={_alerts_fired[0]}")

                    # US-09: write heartbeat every cycle
                    try:
                        _write_heartbeat(
                            _HB_PATH,
                            events_total=_events_seen[0],
                            alerts_total=_alerts_fired[0],
                            started_at=_ingest_started_at,
                            now=_dt.now(_tz.utc),
                        )
                    except Exception as _hb_exc:
                        print(f"[ingest] heartbeat write failed (non-fatal): {_hb_exc}")

                    if cycle % _BASELINE_REFRESH_CYCLES == 0:
                        try:
                            fresh = await load_baselines(pm.pool)
                            engine.update_baselines(fresh)
                            print(f"[ingest] baselines refreshed ({len(fresh)} market(s))")
                        except Exception as _bl_exc:
                            print(f"[ingest] baseline refresh failed (non-fatal): {_bl_exc}")

                    # US-08: daily partition maintenance — also fires on cycle 1 so a
                    # long-idle start still provisions before the first full day.
                    if _is_maintenance_cycle(cycle, _PARTITION_MAINT_CYCLES):
                        try:
                            await _ensure_partitions(pm.pool)
                            print("[ingest] partition maintenance: current partitions verified")
                        except Exception as _pm_exc:
                            print(f"[ingest] partition maintenance failed (non-fatal): {_pm_exc}")
                        # US-08: retention WARNING (read-only, never auto-drops)
                        try:
                            old = await _find_old_partitions(pm.pool, before_days=cfg.ingestion.raw_retention_days)
                            if old:
                                print(
                                    f"[ingest] WARNING: {len(old)} partition(s) older than "
                                    f"{cfg.ingestion.raw_retention_days} days: "
                                    f"{', '.join(old)}. "
                                    "Run 'pmfi db-maintenance --prune-old-partitions' to reclaim space."
                                )
                        except Exception as _rw_exc:
                            print(f"[ingest] retention check failed (non-fatal): {_rw_exc}")

            if "polymarket" in live_venues:
                from pmfi.adapters.polymarket import PolymarketAdapter

                def _make_poly():
                    return PolymarketAdapter(
                        asset_ids=poly_ids,
                        timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                        initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                        max_backoff=cfg.ingestion.reconnect_max_backoff,
                        reconnect_jitter=cfg.ingestion.reconnect_jitter,
                    )

                async def _run_poly(adapter, pool_manager):
                    await run_adapter_pipeline(
                        _counted_events(adapter.events()),
                        pool_manager.pool, engine, alert_handler,
                        suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                        capture_orderbook=cfg.features.enable_orderbook_reconstruction,
                        asset_id_map=asset_id_map,
                        raise_on_connection_loss=True,
                    )

                tasks.append(asyncio.create_task(_supervise(
                    "polymarket", _make_poly, _run_poly,
                    shutdown=shutdown,
                    pool_manager=pm,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                    jitter=cfg.ingestion.reconnect_jitter,
                )))

            if "kalshi" in live_venues:
                from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter

                def _make_kalshi():
                    return KalshiRestPollingAdapter(
                        tickers=kalshi_tickers,
                        poll_interval_seconds=cfg.ingestion.kalshi_poll_interval_seconds,
                        timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                        initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                        max_backoff=cfg.ingestion.reconnect_max_backoff,
                        reconnect_jitter=cfg.ingestion.reconnect_jitter,
                    )

                async def _run_kalshi(adapter, pool_manager):
                    # Kalshi REST trades always carry the ticker as venue_market_id;
                    # no asset_id_map is needed (there are no unresolved token IDs).
                    await run_adapter_pipeline(
                        _counted_events(adapter.events()),
                        pool_manager.pool, engine, alert_handler,
                        suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                        capture_orderbook=cfg.features.enable_orderbook_reconstruction,
                        raise_on_connection_loss=True,
                    )

                tasks.append(asyncio.create_task(_supervise(
                    "kalshi", _make_kalshi, _run_kalshi,
                    shutdown=shutdown,
                    pool_manager=pm,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                    jitter=cfg.ingestion.reconnect_jitter,
                )))

            poly_sub_count = len(poly_ids) if "polymarket" in live_venues else 0
            kalshi_sub_count = len(kalshi_tickers) if "kalshi" in live_venues else 0
            print(
                f"[ingest] started {len(tasks)} adapter(s) for venues={live_venues}, "
                f"watching {len(watched)} market(s) "
                f"(poly_tokens={poly_sub_count}, kalshi_tickers={kalshi_sub_count}). "
                f"Ctrl+C to stop."
            )
            for _m in watched:
                _title = (_m["title"] or _m["venue_market_id"])[:70]
                print(f"[ingest]   [{_m['venue_code']}] {_title}")
            if tasks:
                tasks.append(asyncio.create_task(_telemetry_loop()))
                try:
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_EXCEPTION
                    )
                    # Re-raise the first task exception (if any) so the outer
                    # KeyboardInterrupt/exception handler in cmd_ingest fires.
                    for t in done:
                        if not t.cancelled() and t.exception() is not None:
                            raise t.exception()  # type: ignore[misc]
                except asyncio.CancelledError:
                    pass
        finally:
            shutdown.set()
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await pm.close()

    try:
        rc = asyncio.run(_run())
        if rc:
            return rc
    except KeyboardInterrupt:
        print("\n[ingest] stopped.")
    except Exception as exc:
        print(f"[ingest] fatal error: {exc}")
        print("Check DB connectivity with 'pmfi db-verify' and config with 'pmfi status'.")
        return 1
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Read the daemon heartbeat and report freshness. Exit 0=fresh, 1=stale/missing."""
    from datetime import datetime, timezone
    from pmfi.health import (
        HEARTBEAT_PATH,
        read_heartbeat,
        heartbeat_age_seconds,
        is_stale,
    )

    hb_path = Path(getattr(args, "heartbeat_path", None) or HEARTBEAT_PATH)
    max_age = getattr(args, "max_age_seconds", None)
    if max_age is None:
        # Default: 2x the telemetry interval (interval=60s → 120s)
        max_age = 120
    fmt = getattr(args, "json_output", False)

    hb = read_heartbeat(hb_path)
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
            })
        print(_json.dumps(out, indent=2))
    else:
        if hb is None:
            print(f"[health] No heartbeat found at {hb_path}")
            print("  Is the ingest daemon running? ('pmfi ingest')")
        else:
            age_str = f"{age:.1f}s" if age is not None else "unknown"
            status = "STALE" if stale else "fresh"
            print(
                f"[health] {status}  last_heartbeat={hb.get('ts', '?')}"
                f"  age={age_str}  events={hb.get('events_total', '?')}"
                f"  alerts={hb.get('alerts_total', '?')}"
            )
            if stale:
                print(f"  Heartbeat is older than {max_age}s threshold.")
                print("  Check that 'pmfi ingest' is still running.")

    return 1 if stale else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pmfi", description="Prediction Market Flow Intelligence")
    sub = parser.add_subparsers(dest="command", required=True)
    _register_subcommands(sub)
    return parser


def _register_subcommands(sub) -> None:  # noqa: ANN001

    p_replay = sub.add_parser("replay", aliases=["replay-fixtures"], help="Replay fixture files through the alert pipeline")
    p_replay.add_argument("--fixture-dir", default=None, help="Path to fixture directory")
    p_replay.add_argument("--verbose", action="store_true")
    p_replay.add_argument("--persist", action="store_true", help="Write through full DB pipeline (proves M2-M4)")
    p_replay.add_argument("--from-db", action="store_true", help="Replay raw_events stored in Postgres (proves M2 replayability)")
    p_replay.add_argument("--limit", type=int, default=100, help="Max events when using --from-db (default: 100)")

    sub.add_parser("status", help="Show current PMFI configuration and status")
    sub.add_parser("db-verify", help="Verify Postgres connectivity")
    p_monitor = sub.add_parser("monitor", help="Start live monitoring (requires live mode enabled)")
    p_monitor.add_argument("--fixture-replay", action="store_true", help="Stream fixture events as a live demo")
    p_monitor.add_argument("--fixture-dir", default=None, help="Path to fixture dir (default: tests/fixtures/raw)")
    p_monitor.add_argument("--delay", type=float, default=1.0, help="Seconds between fixture events (default: 1.0)")

    p_alerts = sub.add_parser("alerts", help="Alert commands: list, serve")
    alerts_sub = p_alerts.add_subparsers(dest="alerts_cmd", required=False)
    p_alerts_list = alerts_sub.add_parser("list", help="Show recent alerts from DB")
    p_alerts_list.add_argument("--limit", type=int, default=20)
    p_alerts_list.add_argument("--evidence", action="store_true", help="Show alert evidence details")
    p_alerts_list.add_argument("--rule", metavar="RULE_KEY", help="Filter by rule key (e.g. large_trade_absolute_v1)")
    p_alerts_list.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_alerts_list.add_argument("--venue", choices=["polymarket", "kalshi"], default=None, help="Filter by venue code")
    p_alerts_list.add_argument("--severity", choices=["low", "medium", "high"], help="Filter by severity")
    p_alerts_list.add_argument("--market", default=None, help="Filter by market ID substring")
    p_alerts_list.add_argument("--since", default=None, help="ISO datetime or relative: '1h', '24h', '7d'")
    p_alerts_serve = alerts_sub.add_parser("serve", help="Run local HTTP receiver for alert delivery")
    p_alerts_serve.add_argument("--port", type=int, default=8765)
    p_alerts_serve.add_argument("--host", default="127.0.0.1")

    p_ingest = sub.add_parser("ingest", help="Persistent live ingest daemon (requires live venue enabled in config)")
    p_ingest.add_argument("--venue", action="append", metavar="VENUE",
                          help="Venue to ingest from: polymarket or kalshi (can repeat). Default: all enabled in config.")
    p_ingest.add_argument("--dry-run", action="store_true", help="Connect and log events but do not persist to DB")

    sub.add_parser("stats", help="Show aggregate DB statistics (row counts per table)")

    p_dl = sub.add_parser("dead-letters", help="Show recent normalization failures")
    p_dl.add_argument("--limit", type=int, default=20, help="Number of dead letters to show (default: 20)")

    p_watch = sub.add_parser("watch", help="Live-refreshing alert display (requires DB)")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds (default: 5)")
    p_watch.add_argument("--limit", type=int, default=15, help="Number of alerts to show (default: 15)")
    p_watch.add_argument("--rule", metavar="RULE_KEY", help="Filter by rule key")
    p_watch.add_argument("--venue", metavar="VENUE", help="Filter by venue code")
    p_watch.add_argument("--severity", choices=["high", "medium", "low"], help="Filter by severity")

    p_markets = sub.add_parser("markets", help="Market commands: list, discover, watch, unwatch")
    markets_sub = p_markets.add_subparsers(dest="markets_cmd", required=False)
    p_markets_list = markets_sub.add_parser("list", help="List markets in DB")
    p_markets_list.add_argument("--limit", type=int, default=20)
    p_markets_list.add_argument("--watched", action="store_true", help="Show only watched markets")
    p_markets_list.add_argument("--search", metavar="TEXT", help="Filter by title substring (case-insensitive)")
    p_markets_discover = markets_sub.add_parser("discover", help="Fetch active markets from venue REST API and sync to DB")
    p_markets_discover.add_argument("--venue", default="polymarket", choices=["polymarket", "kalshi"],
                                    help="Venue to discover markets from (default: polymarket)")
    p_markets_discover.add_argument("--limit", type=int, default=100, help="Max markets to fetch (default: 100)")
    p_markets_discover.add_argument("--min-volume", type=float, default=None, metavar="USD", help="Minimum market volume filter")
    p_markets_fetch_trades = markets_sub.add_parser("fetch-trades", help="Fetch recent trades from Kalshi REST API (no auth needed)")
    p_markets_fetch_trades.add_argument("ticker", help="Kalshi market ticker (e.g. KXBTCD-23DEC3100)")
    p_markets_fetch_trades.add_argument("--limit", type=int, default=50, help="Max trades to fetch (default: 50)")
    p_markets_fetch_trades.add_argument("--save-fixtures", action="store_true", help="Save trades as replay fixtures in tests/fixtures/live/")
    p_markets_fetch_trades.add_argument("--force", action="store_true", help="Skip the PMFI_ENABLE_LIVE safety gate")
    p_markets_watch = markets_sub.add_parser("watch", help="Add a market to the watch list")
    p_markets_watch.add_argument("market_id", help="venue_market_id (e.g. Polymarket condition_id)")
    p_markets_watch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")
    p_markets_unwatch = markets_sub.add_parser("unwatch", help="Remove a market from the watch list")
    p_markets_unwatch.add_argument("market_id", help="venue_market_id to unwatch")
    p_markets_unwatch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")

    p_report = sub.add_parser("report", help="Summary report of recent alert activity")
    p_report.add_argument("--since", default="24h", help="Time window: '1h', '24h', '7d', or ISO datetime (default: 24h)")
    p_report.add_argument("--format", choices=["table", "json"], default="table")

    # baselines command
    p_baselines = sub.add_parser("baselines", help="Compute and manage alert baselines from historical trades")
    baselines_sub = p_baselines.add_subparsers(dest="baselines_cmd")
    p_baselines_compute = baselines_sub.add_parser("compute", help="Compute baselines from DB trades")
    p_baselines_compute.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    p_baselines_compute.add_argument("--min-samples", type=int, default=10, dest="min_samples", help="Min trades required per market (default: 10)")
    p_baselines_compute.add_argument("--save", action="store_true", help="Save computed baselines to config/baselines.json")
    baselines_sub.add_parser("show", help="Show current baselines from config/baselines.json")

    p_baseline = sub.add_parser("baseline", help="Baseline compute and listing")
    baseline_sub = p_baseline.add_subparsers(dest="baseline_cmd", required=True)
    p_bc = baseline_sub.add_parser("compute", help="Compute market baselines from metric_windows")
    p_bc.add_argument("--lookback-days", type=int, default=7, help="Lookback window in days (default: 7)")
    baseline_sub.add_parser("list", help="List current computed baselines")

    p_db_maint = sub.add_parser("db-maintenance", help="Partition creation and data retention cleanup")
    p_db_maint.add_argument("--create-partitions", action="store_true", help="Create/verify partitions for current + N months ahead")
    p_db_maint.add_argument("--months-ahead", type=int, default=3, help="Months ahead to create partitions (default: 3)")
    p_db_maint.add_argument("--prune-old-partitions", action="store_true", help="Drop partitions older than --before-days")
    p_db_maint.add_argument("--before-days", type=int, default=None, help="Drop partitions older than this many days (default: raw_retention_days from config)")

    p_live = sub.add_parser("live", help="Continuous live capture (runs indefinitely, Ctrl+C to stop)")
    p_live.add_argument("--venue", choices=["polymarket", "kalshi"], default="polymarket")
    p_live.add_argument("--markets", default=None, help="Comma-separated market IDs (default: watched markets from DB)")
    p_live.add_argument("--orderbook", action="store_true", help="Capture order book snapshots")
    p_live.add_argument("--refresh-map-minutes", type=int, default=30, dest="refresh_map_minutes",
                        help="How often to refresh asset_id map from DB (default: 30)")

    p_live_smoke = sub.add_parser(
        "live-smoke",
        help="Bounded live smoke test (set PMFI_ENABLE_LIVE=1 to use)"
    )
    p_live_smoke.add_argument("--venue", default="polymarket", choices=["polymarket", "kalshi"],
                               help="Venue to connect to (default: polymarket)")
    p_live_smoke.add_argument("--max-events", type=int, default=50,
                               help="Stop after N events (default: 50)")
    p_live_smoke.add_argument("--max-seconds", type=int, default=120,
                               help="Stop after N seconds (default: 120)")
    p_live_smoke.add_argument("--asset-ids", type=str, default=None,
                               help="Comma-separated Polymarket asset/token IDs to subscribe to")
    p_live_smoke.add_argument("--save-fixtures", action="store_true",
                               help="Save captured raw events as JSON fixtures to tests/fixtures/live/")
    p_live_smoke.add_argument("--persist-raw", action="store_true",
                               help="Write events through full DB pipeline (raw + normalized + alerts)")
    p_live_smoke.add_argument("--force", action="store_true",
                               help="Skip PMFI_ENABLE_LIVE check (for testing)")
    p_dashboard = sub.add_parser("dashboard", help="Run the localhost ingest-rate dashboard (read-only JSON endpoints)")
    p_dashboard.add_argument("--port", type=int, default=8766, help="Localhost port (default: 8766)")
    p_dashboard.add_argument("--db-url", default=None, dest="db_url", help="Override database URL (default: from config)")

    sub.add_parser("review-pass", help="Governance review pass")

    p_health = sub.add_parser("health", help="Check daemon heartbeat freshness (exit 0=fresh, 1=stale/missing)")
    p_health.add_argument("--max-age-seconds", type=float, default=None, dest="max_age_seconds",
                          help="Staleness threshold in seconds (default: 120)")
    p_health.add_argument("--json", action="store_true", dest="json_output",
                          help="Output as JSON")
    p_health.add_argument("--heartbeat-path", default=None, dest="heartbeat_path",
                          help="Override heartbeat file path (default: reports/health/heartbeat.json)")


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Run the localhost ingest-rate dashboard (read-only JSON endpoints)."""
    import asyncio
    from pmfi.config import load_config
    from pmfi.dashboard.server import run_dashboard

    cfg = load_config()
    db_url = getattr(args, "db_url", None) or cfg.database.url
    port = getattr(args, "port", 8766)
    try:
        asyncio.run(run_dashboard(db_url=db_url, host="127.0.0.1", port=port))
    except KeyboardInterrupt:
        print("\n[dashboard] stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = args.command

    if cmd in ("replay", "replay-fixtures"):
        return cmd_replay(args)
    elif cmd == "status":
        return cmd_status(args)
    elif cmd == "db-verify":
        return cmd_db_verify(args)
    elif cmd == "monitor":
        return cmd_monitor(args)
    elif cmd == "alerts":
        return cmd_alerts(args)
    elif cmd == "stats":
        return cmd_stats(args)
    elif cmd == "dead-letters":
        return cmd_dead_letters(args)
    elif cmd == "watch":
        return cmd_watch(args)
    elif cmd == "markets":
        return cmd_markets(args)
    elif cmd == "report":
        return cmd_report(args)
    elif cmd == "baselines":
        baselines_cmd = getattr(args, "baselines_cmd", None)
        if baselines_cmd == "compute":
            return _cmd_baselines_compute(args)
        elif baselines_cmd == "show":
            return _cmd_baselines_show(args)
        else:
            print("Usage: pmfi baselines {compute|show}")
            return 1
    elif cmd == "baseline":
        return cmd_baseline(args)
    elif cmd == "db-maintenance":
        return cmd_db_maintenance(args)
    elif cmd == "ingest":
        return cmd_ingest(args)
    elif cmd == "live":
        return cmd_live(args)
    elif cmd == "live-smoke":
        return cmd_live_smoke(args)
    elif cmd == "dashboard":
        return cmd_dashboard(args)
    elif cmd == "health":
        return cmd_health(args)
    elif cmd == "review-pass":
        print(r"review-pass: run python scripts\verify.py")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
