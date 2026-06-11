"""Live ingest and monitor command handlers: monitor, live, live-smoke."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from pmfi.commands._shared import ROOT, _resolve_poly_token_ids


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
            engine = AlertEngine(
                baselines=baselines,
                enable_corroboration=cfg.features.enable_ml_scoring,
            )
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
            engine = AlertEngine(
                baselines=baselines,
                enable_corroboration=cfg.features.enable_ml_scoring,
            )
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
        engine = AlertEngine(
            baselines=_eff_baselines,
            enable_corroboration=cfg.features.enable_ml_scoring,
        )
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

