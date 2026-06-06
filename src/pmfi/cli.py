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
                return await replay_from_db(pool, limit=limit, verbose=args.verbose)
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
                return await replay_fixtures_persist(fixture_dir, pool, verbose=args.verbose)
            finally:
                await close_pool(pool)

        results = asyncio.run(_run_persist())
        print(f"[persist] wrote {len(results)} fixture(s) through DB pipeline")
    else:
        from pmfi.replay import replay_fixtures
        results = replay_fixtures(fixture_dir, verbose=args.verbose)

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

    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        lines = [
            f"[bold]DB:[/bold] {cfg.database.url.split('@')[-1]}",
            f"[bold]Live mode:[/bold] {'enabled' if cfg.live_mode_enabled else 'disabled'}",
            f"[bold]Polymarket live:[/bold] {cfg.features.enable_polymarket_live}",
            f"[bold]Kalshi live:[/bold] {cfg.features.enable_kalshi_live}",
            f"[bold]Delivery:[/bold] {cfg.alerts.default_delivery}",
            f"[bold]Alert rules:[/bold] {len(enabled_rules)} enabled — {', '.join(enabled_rules)}",
            f"[bold]Fixtures:[/bold] {fixture_count} in tests/fixtures/raw/",
        ]
        console.print(Panel("\n".join(lines), title="PMFI Status", expand=False))
    except ImportError:
        print(f"PMFI local | db={cfg.database.url.split('@')[-1]} | live={cfg.live_mode_enabled} | rules={len(enabled_rules)} | fixtures={fixture_count}")
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
            rows = await pool.fetch(
                "SELECT fired_at, rule_key, severity, confidence, score, venue_code, outcome_key "
                "FROM alerts ORDER BY fired_at DESC LIMIT $1",
                limit,
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

    count = len(rows)
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        table = Table(title=f"Recent Alerts (DB, last {count})")
        table.add_column("Fired At", style="cyan")
        table.add_column("Rule", style="yellow")
        table.add_column("Severity", style="red")
        table.add_column("Confidence")
        table.add_column("Venue", style="green")
        table.add_column("Score")
        for row in rows:
            table.add_row(
                str(row["fired_at"])[:19],
                row["rule_key"],
                row["severity"],
                row["confidence"],
                row["venue_code"],
                str(row["score"])[:6],
            )
        console.print(table)
    except ImportError:
        for row in rows:
            print(f"{str(row['fired_at'])[:19]}  {row['rule_key']}  {row['severity']}  {row['venue_code']}")
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
            last_event = await pool.fetchval("SELECT MAX(received_at) FROM raw_events")
            return {
                "raw_events": raw_count, "trades": trade_count, "alerts": alert_count,
                "markets": market_count, "baselines": baseline_count, "windows": window_count,
                "last_event": last_event,
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
        table.add_row("alerts", str(result["alerts"]))
        table.add_row("markets", str(result["markets"]))
        table.add_row("metric_windows", str(result["windows"]))
        table.add_row("market_baselines", str(result["baselines"]))
        console.print(table)
        if result["last_event"]:
            console.print(f"Last event: [cyan]{str(result['last_event'])[:19]}[/cyan]")
    except ImportError:
        for k, v in result.items():
            print(f"{k}: {v}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    cfg = load_config()
    interval = getattr(args, "interval", 5)
    limit = getattr(args, "limit", 15)

    async def _fetch_alerts(pool):
        return await pool.fetch(
            "SELECT fired_at, rule_key, severity, confidence, score, venue_code, outcome_key "
            "FROM alerts ORDER BY fired_at DESC LIMIT $1",
            limit,
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
                table = Table(title=f"Recent Alerts (refresh every {interval}s, limit {limit})")
                table.add_column("Fired At", style="cyan")
                table.add_column("Rule", style="yellow")
                table.add_column("Severity", style="red")
                table.add_column("Conf")
                table.add_column("Venue", style="green")
                table.add_column("Score")
                for row in rows:
                    table.add_row(
                        str(row["fired_at"])[:19],
                        row["rule_key"],
                        row["severity"],
                        row["confidence"],
                        row["venue_code"],
                        str(row["score"])[:6],
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

    async def _query():
        pool = await create_pool(cfg.database.url)
        try:
            if watched_only:
                rows = await pool.fetch(
                    "SELECT venue_code, venue_market_id, title, status, watched, last_seen_at "
                    "FROM markets WHERE watched=true ORDER BY venue_code, venue_market_id LIMIT $1",
                    limit,
                )
            else:
                rows = await pool.fetch(
                    """
                    SELECT m.venue_code, m.venue_market_id, m.title, m.status, m.watched,
                           COUNT(t.trade_id) AS trade_count,
                           MAX(t.received_at) AS last_trade_at
                    FROM markets m
                    LEFT JOIN normalized_trades t ON t.market_id = m.market_id
                    GROUP BY m.market_id, m.venue_code, m.venue_market_id, m.title, m.status, m.watched
                    ORDER BY last_trade_at DESC NULLS LAST
                    LIMIT $1
                    """,
                    limit,
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
        hint = "Run 'pmfi markets discover' to populate from Polymarket." if watched_only else "Run 'pmfi replay --persist' or 'pmfi markets discover' to populate."
        print(f"No markets in DB. {hint}")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        title = f"Watched Markets ({len(rows)})" if watched_only else f"Markets ({len(rows)})"
        table = Table(title=title)
        table.add_column("Venue", style="green")
        table.add_column("Market ID", style="cyan")
        table.add_column("Status")
        table.add_column("Watched")
        if not watched_only:
            table.add_column("Trades", justify="right", style="yellow")
            table.add_column("Last Trade", style="dim")
        for r in rows:
            w = "[green]yes[/green]" if r["watched"] else "no"
            if watched_only:
                table.add_row(r["venue_code"], r["venue_market_id"][:60], r["status"] or "active", w)
            else:
                table.add_row(
                    r["venue_code"], r["venue_market_id"][:50],
                    r["status"] or "active", w,
                    str(r["trade_count"]),
                    str(r["last_trade_at"])[:19] if r["last_trade_at"] else "—",
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
    from pmfi.markets import sync_polymarket_markets
    cfg = load_config()
    limit = getattr(args, "limit", 100)
    min_volume = getattr(args, "min_volume", None)

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            count = await sync_polymarket_markets(pool, limit=limit, min_volume=min_volume)
            return count
        finally:
            await close_pool(pool)

    print(f"Fetching up to {limit} active Polymarket markets...")
    try:
        count = asyncio.run(_run())
        print(f"Synced {count} market(s) to DB. Run 'pmfi markets list' to review.")
    except Exception as exc:
        print(f"Discover failed: {exc}")
        return 1
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
    from pmfi.replay import replay_fixtures
    from pmfi.reporting import build_report, write_report

    fixture_dir = Path(args.fixture_dir) if getattr(args, "fixture_dir", None) else ROOT / "tests" / "fixtures" / "raw"
    output_dir = Path(args.output_dir) if getattr(args, "output_dir", None) else ROOT / "reports"

    results = replay_fixtures(fixture_dir, verbose=getattr(args, "verbose", False))
    summary = build_report(results, title="PMFI Fixture Replay Report")

    for line in summary.lines:
        print(line)

    out_path = write_report(summary, output_dir)
    print(f"\nReport written to: {out_path}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool

    cfg = load_config()

    if args.baseline_cmd == "compute":
        lookback = getattr(args, "lookback_days", 7) * 86400

        async def _compute():
            from pmfi.baseline import compute_market_baselines
            pool = await create_pool(cfg.database.url)
            try:
                results = await compute_market_baselines(pool, lookback_seconds=lookback)
                return results
            finally:
                await close_pool(pool)

        results = asyncio.run(_compute())
        if not results:
            print("No baseline data computed. Run 'pmfi replay --persist' first to populate metric_windows.")
            return 0
        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            table = Table(title=f"Baselines computed ({len(results)} markets)")
            table.add_column("Venue", style="green")
            table.add_column("Market", style="cyan")
            table.add_column("Samples", justify="right")
            table.add_column("p99 Trade USD", justify="right", style="yellow")
            for r in results:
                table.add_row(
                    r["venue_code"],
                    r["venue_market_id"],
                    str(r["sample_size"]),
                    f"{r['p99_trade_usd']:.2f}" if r["p99_trade_usd"] is not None else "n/a",
                )
            console.print(table)
        except ImportError:
            for r in results:
                print(f"{r['venue_code']}:{r['venue_market_id']}  samples={r['sample_size']}  p99={r['p99_trade_usd']}")
        return 0

    if args.baseline_cmd == "list":
        async def _list():
            from pmfi.baseline import load_baselines
            pool = await create_pool(cfg.database.url)
            try:
                return await load_baselines(pool)
            finally:
                await close_pool(pool)

        baselines = asyncio.run(_list())
        if not baselines:
            print("No baselines found. Run 'pmfi baseline compute' first.")
            return 0
        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            table = Table(title=f"Market Baselines ({len(baselines)} entries)")
            table.add_column("Key", style="cyan")
            table.add_column("Samples", justify="right")
            table.add_column("p50 USD", justify="right")
            table.add_column("p99 USD", justify="right", style="yellow")
            table.add_column("p99.5 USD", justify="right", style="red")
            for key, b in baselines.items():
                table.add_row(
                    key,
                    str(b.get("sample_size", "")),
                    f"{b['p50_trade_usd']:.2f}" if b.get("p50_trade_usd") else "n/a",
                    f"{b['p99_trade_usd']:.2f}" if b.get("p99_trade_usd") else "n/a",
                    f"{b['p995_trade_usd']:.2f}" if b.get("p995_trade_usd") else "n/a",
                )
            console.print(table)
        except ImportError:
            for key, b in baselines.items():
                print(f"{key}  p99={b.get('p99_trade_usd')}")
        return 0

    return 0


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

    delivery_mode = cfg.alerts.default_delivery
    if delivery_mode == "file":
        from pmfi.delivery.file import FileDelivery as _FileDelivery
        _file_delivery = _FileDelivery(ROOT / "reports" / "alerts")
        async def _deliver(decision, venue_code, market_id):
            await _file_delivery.deliver(decision, venue_code=venue_code, market_id=market_id)
    elif delivery_mode == "localhost_http_receiver":
        from pmfi.delivery.http import HttpDelivery as _HttpDelivery
        _http_delivery = _HttpDelivery()
        async def _deliver(decision, venue_code, market_id):
            await _http_delivery.deliver(decision, venue_code=venue_code, market_id=market_id)
    else:
        async def _deliver(decision, venue_code, market_id):
            await deliver_stdout(decision, venue_code=venue_code, market_id=market_id)

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            await startup_maintenance(pool)
            baselines = {}
            try:
                baselines = await load_baselines(pool)
            except Exception:
                pass

            engine = AlertEngine(baselines=baselines)

            async with pool.acquire() as conn:
                watched = await fetch_watched_markets(conn)

            poly_ids = [m["venue_market_id"] for m in watched if m["venue_code"] == "polymarket"]
            kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]

            if not watched:
                print("No watched markets in DB. Run 'pmfi markets discover' then 'pmfi markets watch <id>'.")
                if not dry_run:
                    return 0

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

            async def _telemetry_loop(interval: int = 60):
                last = 0
                while True:
                    await asyncio.sleep(interval)
                    total = _events_seen[0]
                    delta = total - last
                    last = total
                    print(f"[ingest] events_total={total} (+{delta}/{interval}s) alerts_total={_alerts_fired[0]}")

            tasks = []
            import asyncio

            if "polymarket" in venues:
                from pmfi.adapters.polymarket import PolymarketAdapter
                adapter = PolymarketAdapter(
                    market_ids=poly_ids,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                )
                await adapter.connect()

                async def _run_poly():
                    try:
                        await run_adapter_pipeline(
                            _counted_events(adapter.events()),
                            pool, engine, alert_handler,
                            suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                        )
                    finally:
                        await adapter.disconnect()

                tasks.append(asyncio.create_task(_run_poly()))

            if "kalshi" in venues:
                from pmfi.adapters.kalshi import KalshiAdapter
                kalshi_key = os.environ.get("KALSHI_API_KEY")
                adapter_k = KalshiAdapter(
                    tickers=kalshi_tickers,
                    api_key_id=kalshi_key,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                )
                await adapter_k.connect()

                async def _run_kalshi():
                    try:
                        await run_adapter_pipeline(
                            _counted_events(adapter_k.events()),
                            pool, engine, alert_handler,
                            suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                        )
                    finally:
                        await adapter_k.disconnect()

                tasks.append(asyncio.create_task(_run_kalshi()))

            print(f"[ingest] started {len(tasks)} adapter(s) for venues={venues}. Ctrl+C to stop.")
            if tasks:
                tasks.append(asyncio.create_task(_telemetry_loop()))
                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    pass
        finally:
            await close_pool(pool)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[ingest] stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="pmfi", description="Prediction Market Flow Intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

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
    p_alerts_serve = alerts_sub.add_parser("serve", help="Run local HTTP receiver for alert delivery")
    p_alerts_serve.add_argument("--port", type=int, default=8765)
    p_alerts_serve.add_argument("--host", default="127.0.0.1")

    p_ingest = sub.add_parser("ingest", help="Persistent live ingest daemon (requires live venue enabled in config)")
    p_ingest.add_argument("--venue", action="append", metavar="VENUE",
                          help="Venue to ingest from: polymarket or kalshi (can repeat). Default: all enabled in config.")
    p_ingest.add_argument("--dry-run", action="store_true", help="Connect and log events but do not persist to DB")

    sub.add_parser("stats", help="Show aggregate DB statistics (row counts per table)")

    p_watch = sub.add_parser("watch", help="Live-refreshing alert display (requires DB)")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds (default: 5)")
    p_watch.add_argument("--limit", type=int, default=15, help="Number of alerts to show (default: 15)")

    p_markets = sub.add_parser("markets", help="Market commands: list, discover, watch, unwatch")
    markets_sub = p_markets.add_subparsers(dest="markets_cmd", required=False)
    p_markets_list = markets_sub.add_parser("list", help="List markets in DB")
    p_markets_list.add_argument("--limit", type=int, default=20)
    p_markets_list.add_argument("--watched", action="store_true", help="Show only watched markets")
    p_markets_discover = markets_sub.add_parser("discover", help="Fetch active markets from Polymarket REST API and sync to DB")
    p_markets_discover.add_argument("--limit", type=int, default=100, help="Max markets to fetch (default: 100)")
    p_markets_discover.add_argument("--min-volume", type=float, default=None, metavar="USD", help="Minimum market volume filter")
    p_markets_watch = markets_sub.add_parser("watch", help="Add a market to the watch list")
    p_markets_watch.add_argument("market_id", help="venue_market_id (e.g. Polymarket condition_id)")
    p_markets_watch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")
    p_markets_unwatch = markets_sub.add_parser("unwatch", help="Remove a market from the watch list")
    p_markets_unwatch.add_argument("market_id", help="venue_market_id to unwatch")
    p_markets_unwatch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")

    p_report = sub.add_parser("report", help="Generate fixture replay report to reports/")
    p_report.add_argument("--fixture-dir", default=None, help="Path to fixture directory")
    p_report.add_argument("--output-dir", default=None, help="Output directory (default: reports/)")
    p_report.add_argument("--verbose", action="store_true")

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

    sub.add_parser("live-smoke", help="Live smoke test (opt-in only)")
    sub.add_parser("review-pass", help="Governance review pass")

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
    elif cmd == "watch":
        return cmd_watch(args)
    elif cmd == "markets":
        return cmd_markets(args)
    elif cmd == "report":
        return cmd_report(args)
    elif cmd == "baseline":
        return cmd_baseline(args)
    elif cmd == "db-maintenance":
        return cmd_db_maintenance(args)
    elif cmd == "ingest":
        return cmd_ingest(args)
    elif cmd == "live-smoke":
        print("live-smoke is intentionally a stub until M5 opt-in live adapters are configured")
        return 0
    elif cmd == "review-pass":
        print(r"review-pass: run python scripts\verify.py")
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
