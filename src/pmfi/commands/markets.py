"""Markets command handlers: list, discover, fetch-trades, watch, unwatch."""
from __future__ import annotations

import argparse
import asyncio
import os

from pmfi.commands._shared import ROOT


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
