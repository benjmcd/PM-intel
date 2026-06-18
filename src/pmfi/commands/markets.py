"""Markets command handlers: list, discover, fetch-trades, watch, unwatch."""
from __future__ import annotations

import argparse
import asyncio
import os

from pmfi.commands._shared import ROOT


def _fmt_volume(v) -> str:
    """Format a venue-relative volume as a compact magnitude without currency symbol.

    Polymarket volume is USD notional; Kalshi volume is contract count.
    A $ prefix would be misleading for Kalshi, so none is shown.

    Accepts float or Decimal (the numeric(20,2) column round-trips as Decimal via
    asyncpg); coerce to float so the magnitude arithmetic is type-uniform.
    """
    if v is None:
        return "—"  # em-dash
    v = float(v)
    if v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{v / 1e3:.2f}K"
    return f"{v:.2f}"


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
    from pmfi.db.repos.markets import fetch_markets_ranked
    cfg = load_config()
    limit = getattr(args, "limit", 20)
    watched_only = getattr(args, "watched", False)
    search = getattr(args, "search", None)
    sort = getattr(args, "sort", "volume")
    min_volume = getattr(args, "min_volume", None)

    async def _query():
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                rows = await fetch_markets_ranked(
                    conn,
                    venue_code=None,
                    watched=(True if watched_only else None),
                    search=search,
                    min_volume=min_volume,
                    sort=sort,
                    limit=limit,
                )
            return rows, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await close_pool(pool)

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
        table.add_column("Volume", justify="right", style="magenta", min_width=8)
        table.add_column("Trades", justify="right", style="yellow", min_width=5)
        table.add_column("Last Trade", style="dim", min_width=10, no_wrap=True)
        for r in rows:
            w = "[green]y[/green]" if r["watched"] else "n"
            display_title = (r.get("title") or r["venue_market_id"])[:80]
            table.add_row(
                r["venue_code"], display_title,
                r["status"] or "active", w,
                _fmt_volume(r.get("volume")),
                str(r["trade_count"]),
                str(r["last_trade_at"])[5:16] if r["last_trade_at"] else "—",
            )
        console.print(table)
    except ImportError:
        for r in rows:
            w = "watched" if r.get("watched") else ""
            vol = _fmt_volume(r.get("volume"))
            print(f"{r['venue_code']}:{r['venue_market_id']}  vol={vol}  {w}")
    return 0


def _cmd_markets_discover(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    cfg = load_config()
    limit = getattr(args, "limit", 100)
    min_volume = getattr(args, "min_volume", None)
    venue = getattr(args, "venue", "polymarket")
    watch_top = getattr(args, "watch_top", None)
    if watch_top is not None and watch_top <= 0:
        print("Error: --watch-top must be a positive integer.")
        return 1

    async def _run():
        pool = await create_pool(cfg.database.url)
        try:
            if venue == "kalshi":
                from pmfi.markets import sync_kalshi_markets
                count = await sync_kalshi_markets(pool, limit=limit, min_volume=min_volume)
            else:
                from pmfi.markets import sync_polymarket_markets
                count = await sync_polymarket_markets(pool, limit=limit, min_volume=min_volume)

            preview: list = []
            top_ids: list = []
            watched_count = 0
            if count > 0:
                from pmfi.db.repos.markets import fetch_markets_ranked, set_markets_watched_bulk
                # Fetch enough rows to honor --watch-top even when it exceeds the
                # 10-row preview; the printed preview always shows the top 10.
                fetch_n = max(10, watch_top or 0)
                async with pool.acquire() as conn:
                    ranked = await fetch_markets_ranked(conn, venue_code=venue, sort="volume", limit=fetch_n)
                preview = ranked[:10]

                if watch_top is not None and watch_top > 0 and ranked:
                    top_ids = [r["venue_market_id"] for r in ranked[:watch_top]]
                    async with pool.acquire() as conn:
                        watched_count = await set_markets_watched_bulk(
                            conn, venue_code=venue, venue_market_ids=top_ids, watched=True
                        )

            return count, preview, top_ids, watched_count
        finally:
            await close_pool(pool)

    venue_label = "Kalshi" if venue == "kalshi" else "Polymarket"
    print(f"Fetching up to {limit} active {venue_label} markets...")
    try:
        count, ranked, watched_ids, watched_count = asyncio.run(_run())
    except Exception as exc:
        print(f"Discover failed: {exc}")
        return 1

    print(f"Synced {count} market(s) to DB.")

    if count > 0 and ranked:
        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
            table = Table(title=f"Top {len(ranked)} by Volume ({venue_label})", width=120)
            table.add_column("#", justify="right", style="dim", min_width=3)
            table.add_column("Venue", style="green", min_width=10)
            table.add_column("Title", style="cyan", min_width=50)
            table.add_column("Volume", justify="right", style="magenta", min_width=8)
            for i, r in enumerate(ranked, 1):
                table.add_row(
                    str(i), r["venue_code"],
                    (r.get("title") or r["venue_market_id"])[:70],
                    _fmt_volume(r.get("volume")),
                )
            console.print(table)
        except ImportError:
            print(f"\nTop {len(ranked)} by Volume:")
            for i, r in enumerate(ranked, 1):
                print(f"  {i}. {r['venue_code']}  {_fmt_volume(r.get('volume'))}  {(r.get('title') or r['venue_market_id'])[:70]}")

        print("")
        for r in ranked:
            mid = r["venue_market_id"]
            print(f"  pmfi markets watch {mid} --venue {venue}")

        if watched_ids:
            print(f"\nWatched top {len(watched_ids)} market(s):")
            for mid in watched_ids:
                print(f"  {venue}:{mid}")

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
    cfg = load_config()
    venue = getattr(args, "venue", "polymarket")

    market_id = getattr(args, "market_id", None)
    top = getattr(args, "top", None)
    search = getattr(args, "search", None)

    # Validate exactly one selection mode. --top is a watch-only mode (the
    # unwatch parser does not register it), so tailor the guidance accordingly.
    modes = [x for x in [market_id, top, search] if x is not None]
    _opts = "<market_id>, --top N, or --search TEXT" if watched else "<market_id> or --search TEXT"
    if len(modes) == 0:
        print(f"Error: provide exactly one of: {_opts}")
        return 1
    if len(modes) > 1:
        print(f"Error: provide exactly one of: {_opts} (not multiple)")
        return 1
    if top is not None and top <= 0:
        print("Error: --top must be a positive integer.")
        return 1

    action = "watched" if watched else "unwatched"

    # Bulk modes (--top or --search)
    if top is not None or search is not None:
        from pmfi.db.repos.markets import fetch_markets_ranked, set_markets_watched_bulk

        async def _bulk_run():
            try:
                pool = await create_pool(cfg.database.url)
            except Exception as exc:
                return None, None, str(exc)
            try:
                async with pool.acquire() as conn:
                    if top is not None:
                        rows = await fetch_markets_ranked(
                            conn, venue_code=venue, sort="volume", limit=top
                        )
                    else:
                        rows = await fetch_markets_ranked(
                            conn, venue_code=venue, search=search, limit=200
                        )
                    ids = [r["venue_market_id"] for r in rows]
                    if not ids:
                        return rows, 0, None
                    count = await set_markets_watched_bulk(
                        conn, venue_code=venue, venue_market_ids=ids, watched=watched
                    )
                return rows, count, None
            except Exception as exc:
                return None, None, str(exc)
            finally:
                await close_pool(pool)

        rows, count, err = asyncio.run(_bulk_run())
        if err:
            print(f"DB error: {err}\nRun 'pmfi db-verify' to check connectivity.")
            return 1
        if not rows:
            mode_desc = f"--top {top}" if top is not None else f"--search {search!r}"
            print(f"No markets found for {venue} with {mode_desc}. Run 'pmfi markets discover' first.")
            return 1
        for r in rows:
            print(f"Market {venue}:{r['venue_market_id']} marked as {action}.")
        print(f"{count} market(s) {action}.")
        return 0

    # Single positional path
    from pmfi.db.repos.markets import set_market_watched

    async def _run():
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            return None, str(exc)
        try:
            async with pool.acquire() as conn:
                found = await set_market_watched(conn, venue_code=venue, venue_market_id=market_id, watched=watched)
            return found, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await close_pool(pool)

    found, err = asyncio.run(_run())
    if err:
        print(f"DB error: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    if found:
        print(f"Market {venue}:{market_id} marked as {action}.")
    else:
        print(f"Market not found: {venue}:{market_id}. Run 'pmfi markets discover' first.")
        return 1
    return 0
