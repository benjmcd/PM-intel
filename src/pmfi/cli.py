from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from pathlib import Path

# Windows cp1252 can't encode many Unicode chars used in Rich output.
# Reconfigure at import time so all downstream print/Rich calls use UTF-8.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Imports from domain command modules (must come AFTER ROOT is defined above
# so that _shared.ROOT is computed independently; no cycle — commands never
# import pmfi.cli).
# ---------------------------------------------------------------------------
from pmfi.commands.reporting import (
    cmd_status,
    cmd_db_verify,
    cmd_db_maintenance,
    cmd_dead_letters,
    cmd_raw_events,
    cmd_stats,
    cmd_watch,
    cmd_report,
    cmd_health,
)
from pmfi.commands.alerts import (
    cmd_alerts_list,
    cmd_alerts_serve,
    cmd_alerts_review,
    cmd_alerts_review_packet,
    cmd_alerts_volume_spike_calibration,
    cmd_alerts_volume_spike_floor_audit,
    cmd_volume_spike_calibration_sweep,
    cmd_calibration_packet_batch,
    cmd_calibration_decision,
    cmd_calibration_review_queue,
    cmd_calibration_cluster_review,
    cmd_calibration_cluster_review_summary,
    cmd_alerts_outcome_audit,
    cmd_alerts_fp_rate,
    cmd_alerts_lineage_check,
)
from pmfi.commands.markets import (
    cmd_markets,
    _cmd_markets_list,
    _cmd_markets_discover,
    _cmd_markets_fetch_trades,
    _cmd_markets_recent_trades,
    _cmd_markets_sync_one,
    _cmd_markets_set_watched,
)
from pmfi.commands.ingest import (
    cmd_monitor,
    cmd_live,
    cmd_live_smoke,
)
from pmfi.commands.dashboard import cmd_dashboard
from pmfi.commands.soak import cmd_soak, non_negative_int, parse_soak_timestamp
from pmfi.commands.review_pass import cmd_review_pass
from pmfi.commands.data import cmd_backtest_analytics, cmd_data_coverage
from pmfi.commands.backtest import cmd_backtest
from pmfi.commands.rules import (
    _atomic_write_rules,
    _rules_yaml_path,
    cmd_rules,
)
from pmfi.commands.setup import (
    _check_result,
    _classify_checks,
    _copy_config_if_missing,
    cmd_doctor,
    cmd_init,
)

# Re-export shared helpers so that existing imports and patches on pmfi.cli.*
# continue to work (e.g. "from pmfi.cli import _delivery_banner",
# patch("pmfi.cli._is_maintenance_cycle"), etc.).
from pmfi.commands._shared import (
    _is_maintenance_cycle,
    _delivery_banner,
    _resolve_poly_token_ids,
    _select_ingest_venues,
    _cycles_from_minutes,
    _safe_recompute_baselines,
    _refresh_subscriptions,
)

logger = logging.getLogger(__name__)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure root logger.

    Always attaches a StreamHandler so console output is line-buffered (not
    block-buffered like bare print() to a redirected stdout).  When log_file is
    set, also attaches a RotatingFileHandler that survives redirected stdout.
    """
    import logging.handlers

    fmt = "%(asctime)s %(levelname)s %(name)s %(message)s"
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Console handler — attach only once (guard against repeated calls in tests)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setLevel(numeric_level)
        ch.setFormatter(logging.Formatter(fmt))
        root.addHandler(ch)

    # Rotating file handler
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
            delay=True,
        )
        fh.setLevel(numeric_level)
        fh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)


async def _bounded_shutdown(shutdown: asyncio.Event, max_seconds: float | int) -> None:
    """Set an existing shutdown event after a positive runtime bound."""
    if max_seconds <= 0:
        return
    await asyncio.sleep(max_seconds)
    shutdown.set()


# ---------------------------------------------------------------------------
# cmd_replay — kept here because tests monkeypatch pmfi.cli.ROOT and the
# function reads ROOT from this module's namespace.
# ---------------------------------------------------------------------------

def cmd_replay(args: argparse.Namespace) -> int:
    from pmfi.delivery.stdout import deliver_stdout

    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else ROOT / "tests" / "fixtures" / "raw"

    if getattr(args, "from_db", False):
        import re as _re

        limit = getattr(args, "limit", 100)

        def _parse_ts(label: str, raw: str | None):
            """Parse ISO 8601 or relative ('24h','7d','30m') to datetime or None."""
            if not raw:
                return None
            raw = raw.strip()
            m = _re.fullmatch(r"(\d+)([hdm])", raw)
            if m:
                n, unit = int(m.group(1)), m.group(2)
                if n <= 0:
                    print(f"[replay] Invalid {label} value: {raw!r}; relative window must be greater than zero")
                    return None
                delta_s = {"h": 3600, "d": 86400, "m": 60}[unit] * n
                from datetime import datetime, timezone, timedelta
                return datetime.now(timezone.utc) - timedelta(seconds=delta_s)
            try:
                return parse_soak_timestamp(raw)
            except ValueError as exc:
                print(f"[replay] Invalid {label} value: {raw!r}; {exc}")
                return None

        raw_from = getattr(args, "replay_from", None)
        raw_to = getattr(args, "replay_to", None)
        start_ts = _parse_ts("--from", raw_from)
        if raw_from and start_ts is None:
            return 1
        end_ts = _parse_ts("--to", raw_to)
        if raw_to and end_ts is None:
            return 1
        if start_ts is not None and end_ts is not None and start_ts >= end_ts:
            print("[replay] --from must be before --to.")
            return 1

        replay_venue = getattr(args, "replay_venue", None)
        replay_market = getattr(args, "replay_market", None)
        replay_persist = getattr(args, "persist", False)

        from pmfi.config import load_config
        from pmfi.db import create_pool, close_pool
        from pmfi.replay import replay_from_db

        async def _run_from_db():
            cfg = load_config()
            pool = await create_pool(cfg.database.url)
            try:
                # DB-canonical: always load baselines from DB; never prefer stale JSON file
                return await replay_from_db(
                    pool,
                    limit=limit,
                    verbose=args.verbose,
                    baselines=None,
                    start_ts=start_ts,
                    end_ts=end_ts,
                    venue=replay_venue,
                    market=replay_market,
                    persist=replay_persist,
                )
            finally:
                await close_pool(pool)

        try:
            results = asyncio.run(_run_from_db())
        except Exception as exc:
            print(f"[replay] DB connect failed: {exc}\nRun 'python scripts\\db_local.py up' to start Postgres.")
            return 1
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

        try:
            results = asyncio.run(_run_persist())
        except Exception as exc:
            print(f"[replay] DB connect failed: {exc}\nRun 'python scripts\\db_local.py up' to start Postgres.")
            return 1
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
        table = Table(title=f"Replay: {len(results)} events -> {alert_count} alerts")
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
        print(f"replay complete: {len(results)} events, {alert_count} alerts")
    if getattr(args, "report", False):
        from pmfi.reporting import build_report, write_report

        report_kind = "db" if getattr(args, "from_db", False) else "fixture"
        title = "DB Replay Report" if report_kind == "db" else "Fixture Replay Report"
        summary = build_report(results, title=title, report_kind=report_kind)
        report_path = write_report(summary, ROOT / "reports" / "replay")
        print(f"[report] wrote {report_path}")
    return 0


def cmd_volume_spike_calibration(_args: argparse.Namespace) -> int:
    return cmd_alerts_volume_spike_calibration(_args)


def cmd_volume_spike_sweep(_args: argparse.Namespace) -> int:
    return cmd_volume_spike_calibration_sweep(_args)


def cmd_volume_spike_floor_audit(_args: argparse.Namespace) -> int:
    return cmd_alerts_volume_spike_floor_audit(_args)


# ---------------------------------------------------------------------------
# cmd_alerts_explain — kept here because tests patch pmfi.cli.asyncio.run
# when testing this function.
# ---------------------------------------------------------------------------

def cmd_alerts_explain(args: argparse.Namespace) -> int:
    """Render a detailed plain-English explanation of a single alert by ID."""
    import json as _json
    from pmfi.config import load_config
    import asyncpg
    from pmfi.dashboard.queries import _summarize_evidence

    alert_id = args.alert_id

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
            from pmfi.db.repos.alerts import get_alert_by_id
            async with pool.acquire() as conn:
                row = await get_alert_by_id(conn, alert_id)
            return row, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await pool.close()

    row, err = asyncio.run(_query())
    if err:
        print(f"DB query failed: {err}", file=sys.stderr)
        return 1
    if row is None:
        print(f"Alert not found: {alert_id}", file=sys.stderr)
        return 1
    fmt = getattr(args, "format", "text")

    # Parse evidence
    ev_raw = row.get("evidence") or {}
    if isinstance(ev_raw, str):
        try:
            ev_dict = _json.loads(ev_raw)
        except Exception:
            ev_dict = {}
    elif isinstance(ev_raw, dict):
        ev_dict = ev_raw
    else:
        ev_dict = {}

    if fmt == "json":
        def _serial(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        payload = dict(row)
        payload["evidence"] = ev_dict
        payload["evidence_summary"] = _summarize_evidence(ev_dict)
        print(_json.dumps(payload, indent=2, default=_serial))
        return 0

    # Plain-English evidence rendering
    ev_lines: list[str] = []
    car = ev_dict.get("capital_at_risk_usd")
    for thresh_key in ("p99_threshold_usd", "p99_baseline_usd", "p995_threshold_usd", "threshold_usd"):
        thresh = ev_dict.get(thresh_key)
        if thresh is not None:
            if car is not None:
                ev_lines.append(f"  capital_at_risk_usd=${float(car):,.2f} exceeded {thresh_key} ${float(thresh):,.2f}")
            else:
                ev_lines.append(f"  {thresh_key}=${float(thresh):,.2f}")
            break
    else:
        if car is not None:
            ev_lines.append(f"  capital_at_risk_usd=${float(car):,.2f}")
    for pct_key in ("percentile", "pct_rank", "score_pct"):
        val = ev_dict.get(pct_key)
        if val is not None:
            ev_lines.append(f"  {pct_key}={float(val):.2f}")
            break
    side = ev_dict.get("dominant_side")
    if side:
        ev_lines.append(f"  dominant_side={side}")
    tc = ev_dict.get("trade_count")
    if tc is not None:
        ev_lines.append(f"  trade_count={int(tc)}")
    from pmfi.commands.alerts import explain_operator_evidence_lines
    operator_lines, operator_keys = explain_operator_evidence_lines(ev_dict)
    ev_lines.extend(operator_lines)
    # Remaining keys not already shown
    shown_keys = {"capital_at_risk_usd", "p99_threshold_usd", "p99_baseline_usd",
                  "p995_threshold_usd", "threshold_usd", "percentile", "pct_rank",
                  "score_pct", "dominant_side", "trade_count"} | operator_keys
    for k, v in ev_dict.items():
        if k not in shown_keys:
            ev_lines.append(f"  {k}={v}")

    fired = row.get("fired_at")
    fired_str = fired.isoformat() if hasattr(fired, "isoformat") else str(fired or "—")
    market_title = row.get("market_title") or "—"
    venue_market_id = row.get("venue_market_id") or "—"

    print(f"Alert: {row['alert_id']}")
    print(f"  Rule          : {row['rule_key']}  (version={row.get('rule_version') or '—'})")
    print(f"  Severity      : {row['severity']}  confidence={row['confidence']}  score={row.get('score') or '—'}")
    print(f"  Market        : {market_title}")
    print(f"  venue_market_id: {venue_market_id}")
    print(f"  Outcome       : {row.get('outcome_key') or '—'}")
    print(f"  Fired at      : {fired_str}")
    print(f"  Data quality  : {row.get('data_quality') or '—'}")
    dq = row.get("data_quality") or ""
    if dq and dq not in ("ok", "unknown", ""):
        degraded = ev_dict.get("degraded_reasons") or ev_dict.get("data_quality_reasons")
        if degraded:
            print(f"  DQ caveat     : {degraded}")
    print("  Evidence:")
    if ev_lines:
        for line in ev_lines:
            print(line)
    else:
        print("  (no evidence fields)")
    raw_event_id = row.get("raw_event_id")
    trade_id = row.get("trade_id")
    if raw_event_id or trade_id:
        print("  Lineage:")
        if raw_event_id:
            print(f"    raw_event_id={raw_event_id}")
        if trade_id:
            print(f"    trade_id={trade_id}")
    return 0


# ---------------------------------------------------------------------------
# cmd_alerts — kept here because it dispatches to cmd_alerts_explain which
# must resolve in cli.py's namespace for test patches to work.
# ---------------------------------------------------------------------------

def cmd_alerts(args: argparse.Namespace) -> int:
    alerts_cmd = getattr(args, "alerts_cmd", None)
    if alerts_cmd == "serve":
        return cmd_alerts_serve(args)
    if alerts_cmd == "explain":
        return cmd_alerts_explain(args)
    if alerts_cmd == "review":
        return cmd_alerts_review(args)
    if alerts_cmd == "review-packet":
        return cmd_alerts_review_packet(args)
    if alerts_cmd == "outcome-audit":
        return cmd_alerts_outcome_audit(args)
    if alerts_cmd == "lineage-check":
        return cmd_alerts_lineage_check(args)
    if alerts_cmd == "fp-rate":
        return cmd_alerts_fp_rate(args)
    # Default: list behavior (alerts_cmd is None or "list")
    return cmd_alerts_list(args)


# ---------------------------------------------------------------------------
# cmd_baseline, _cmd_baselines_compute, _cmd_baselines_show — kept here
# because tests patch pmfi.cli._cmd_baselines_compute and
# pmfi.cli._cmd_baselines_show, and cmd_baseline calls them by name.
# ---------------------------------------------------------------------------

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
        cat = vals.get("computed_at")
        cat_str = f"  computed={str(cat)[:19]}" if cat else ""
        is_fresh = vals.get("is_fresh", True)
        stale_tag = "  [STALE]" if not is_fresh else ""
        print(f"  {key}: p99=${float(p99):.0f}  p99.5=${float(p995):.0f}  n={vals.get('sample_size', 0)}{cat_str}{stale_tag}")
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


# ---------------------------------------------------------------------------
# cmd_ingest — kept here because tests patch pmfi.cli.asyncio.run when
# testing this function (so asyncio must remain in this module's namespace).
# ---------------------------------------------------------------------------

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
    kalshi_all_market_poll = getattr(args, "kalshi_all_market_poll", False)
    kalshi_poll_interval_seconds = getattr(args, "kalshi_poll_interval_seconds", None)
    if kalshi_poll_interval_seconds is None:
        kalshi_poll_interval_seconds = cfg.ingestion.kalshi_poll_interval_seconds
    kalshi_trade_poll_limit = getattr(args, "kalshi_trade_poll_limit", None)
    if kalshi_trade_poll_limit is None:
        kalshi_trade_poll_limit = cfg.ingestion.kalshi_trade_poll_limit
    kalshi_trade_poll_max_pages = getattr(args, "kalshi_trade_poll_max_pages", None)
    if kalshi_trade_poll_max_pages is None:
        kalshi_trade_poll_max_pages = cfg.ingestion.kalshi_trade_poll_max_pages

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
        max_events = getattr(args, "max_events", 0)
        max_seconds = getattr(args, "max_seconds", 0)
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
                    timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                    reconnect_jitter=cfg.ingestion.reconnect_jitter,
                    subscription_timeout_seconds=cfg.ingestion.polymarket_subscription_timeout_seconds,
                    receive_timeout_seconds=cfg.ingestion.polymarket_receive_timeout_seconds,
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
                            if max_events and _events_seen[0] >= max_events:
                                for t in tasks:
                                    t.cancel()
                                break
                    finally:
                        await adapter.disconnect()

                tasks.append(asyncio.create_task(_dry_poly()))

            if "kalshi" in dry_venues:
                from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
                adapter_k = KalshiRestPollingAdapter(
                    tickers=kalshi_tickers,
                    poll_interval_seconds=kalshi_poll_interval_seconds,
                    limit=kalshi_trade_poll_limit,
                    max_pages=kalshi_trade_poll_max_pages,
                    all_market_poll=kalshi_all_market_poll,
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
                            if max_events and _events_seen[0] >= max_events:
                                for t in tasks:
                                    t.cancel()
                                break
                    finally:
                        await adapter_k.disconnect()

                tasks.append(asyncio.create_task(_dry_kalshi()))

            _stop_parts = []
            if max_events:
                _stop_parts.append(f"{max_events} events")
            if max_seconds:
                _stop_parts.append(f"{max_seconds} seconds")
            _stop_msg = "stops after " + " or ".join(_stop_parts) if _stop_parts else "Ctrl+C to stop"
            print(f"[dry-run] started {len(tasks)} adapter(s) for venues={dry_venues} -- no DB writes. {_stop_msg}.")
            if tasks:
                try:
                    if max_seconds:
                        await asyncio.wait_for(asyncio.gather(*tasks), timeout=max_seconds)
                    else:
                        await asyncio.gather(*tasks)
                except asyncio.TimeoutError:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                except asyncio.CancelledError:
                    pass

        try:
            asyncio.run(_run_dry())
        except KeyboardInterrupt:
            print("\n[dry-run] stopped.")
        except Exception as exc:
            print(f"[dry-run] error: {exc}")
            print("Check DB connectivity with 'pmfi db-verify' and config with 'pmfi status'.")
            return 1
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

        max_seconds = getattr(args, "max_seconds", 0)
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

            engine = AlertEngine(
                baselines=baselines,
                directional_accumulator_max_markets=getattr(
                    cfg.ingestion, "directional_accumulator_max_markets", 5000
                ),
                directional_accumulator_ttl_seconds=getattr(
                    cfg.ingestion, "directional_accumulator_ttl_seconds", 3600.0
                ),
            )

            async with pm.pool.acquire() as conn:
                watched = await fetch_watched_markets(conn)

            # Polymarket WS subscriptions require token IDs (asset_ids from market_outcomes),
            # not condition IDs (venue_market_id). Resolve via the shared helper.
            from pmfi.markets import load_asset_id_mapping
            asset_id_map = await load_asset_id_mapping(pm.pool)
            poly_ids = _resolve_poly_token_ids(watched, asset_id_map)
            kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]
            # Mutable containers so mid-session refresh updates the values seen by
            # _make_poly / _make_kalshi on the next supervisor restart without any
            # forced adapter reconnect.
            _current_poly_ids: list = list(poly_ids)
            _current_kalshi_tickers: list = list(kalshi_tickers)

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

            # Per-venue counters: {venue: {"count": int, "last_event_at": str|None}}
            _venue_counters: dict = {}
            # Per-venue supervisor failure info: {venue: {"consecutive_failures": int, "last_error": str|None}}
            _venue_status: dict = {}

            async def alert_handler(decision, venue_code, market_id):
                _alerts_fired[0] += 1
                await _deliver(decision, venue_code, market_id)

            def _counted_events_for(venue: str):
                """Return a venue-aware async generator that wraps an event source."""
                async def _gen(source):
                    if venue not in _venue_counters:
                        _venue_counters[venue] = {"count": 0, "last_event_at": None}
                    async for raw in source:
                        _events_seen[0] += 1
                        _venue_counters[venue]["count"] += 1
                        _venue_counters[venue]["last_event_at"] = _dt.now(_tz.utc).isoformat()
                        yield raw
                return _gen

            from datetime import datetime as _dt, timezone as _tz
            from pmfi.health import write_heartbeat as _write_heartbeat, HEARTBEAT_PATH as _HB_PATH
            from pmfi.db.migrations import find_partitions_older_than as _find_old_partitions
            from pmfi.db.migrations import ensure_current_partitions as _ensure_partitions
            from pmfi.db.migrations import drop_old_partitions as _drop_old_partitions

            _ingest_started_at = _dt.now(_tz.utc)
            # Cadence constants (all in cycles; default interval=60s so daily≈1440)
            _BASELINE_REFRESH_CYCLES = 10    # refresh baselines every ~10 min
            _MAP_REFRESH_CYCLES = 10         # refresh subscription map every ~10 min
            _PARTITION_MAINT_CYCLES = 1440   # daily partition maintenance (60s interval)
            _BASELINE_RECOMPUTE_CYCLES = _cycles_from_minutes(
                cfg.baselines.recompute_interval_minutes, 60
            )

            # Recompute telemetry state (mutated by _telemetry_loop)
            _recompute_state: dict = {
                "last_recompute_at": None,
                "last_recompute_ok": None,
                "last_recompute_error": None,
            }
            _retention_enabled = bool(getattr(cfg.ingestion, "retention_enabled", False))
            _retention_operator_acknowledged = bool(
                getattr(cfg.ingestion, "retention_operator_acknowledged", False)
            )
            _partition_state: dict = {
                "retention_enabled": _retention_enabled,
                "retention_operator_acknowledged": _retention_operator_acknowledged,
                "retention_active": bool(
                    _retention_enabled
                    and _retention_operator_acknowledged
                ),
                "raw_retention_days": cfg.ingestion.raw_retention_days,
                "last_checked_at": None,
                "last_ensure_ok": None,
                "last_ensure_error": None,
                "last_retention_check_error": None,
                "old_partitions": [],
                "dropped_partitions": [],
                "last_drop_error": None,
            }

            def _build_venues_payload() -> dict:
                """Build the venues sub-dict for the heartbeat from live counters."""
                out = {}
                for v, ctr in _venue_counters.items():
                    sv = _venue_status.get(v, {})
                    out[v] = {
                        "events_total": ctr["count"],
                        "last_event_at": ctr["last_event_at"],
                        "consecutive_failures": sv.get("consecutive_failures", 0),
                        "last_error": sv.get("last_error"),
                        "circuit_open": bool(sv.get("circuit_open", False)),
                        "failure_window_seconds": sv.get("failure_window_seconds"),
                    }
                # Include venues that have supervisor failures but no events yet
                for v, sv in _venue_status.items():
                    if v not in out:
                        out[v] = {
                            "events_total": 0,
                            "last_event_at": None,
                            "consecutive_failures": sv.get("consecutive_failures", 0),
                            "last_error": sv.get("last_error"),
                            "circuit_open": bool(sv.get("circuit_open", False)),
                            "failure_window_seconds": sv.get("failure_window_seconds"),
                        }
                return out

            # Write an initial heartbeat right after preflight so `pmfi health`
            # works within the first interval without waiting for cycle 1.
            try:
                _write_heartbeat(
                    _HB_PATH,
                    events_total=_events_seen[0],
                    alerts_total=_alerts_fired[0],
                    started_at=_ingest_started_at,
                    now=_dt.now(_tz.utc),
                    venues=_build_venues_payload(),
                    partition_maintenance=dict(_partition_state),
                )
            except Exception as _hb_exc:
                logger.warning("[ingest] heartbeat write failed (non-fatal): %s", _hb_exc)

            from pmfi.commands.daemon import _telemetry_tick

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
                    await _telemetry_tick(
                        cycle=cycle,
                        events_total=total,
                        alerts_total=_alerts_fired[0],
                        delta=delta,
                        interval=interval,
                        hb_path=_HB_PATH,
                        write_heartbeat=_write_heartbeat,
                        started_at=_ingest_started_at,
                        build_venues_payload=_build_venues_payload,
                        recompute_state=_recompute_state,
                        recompute_enabled=cfg.baselines.recompute_enabled,
                        recompute_cycles=_BASELINE_RECOMPUTE_CYCLES,
                        safe_recompute_baselines=_safe_recompute_baselines,
                        pool=pm.pool,
                        window_days=cfg.baselines.window_days,
                        min_samples=cfg.baselines.min_samples,
                        baseline_refresh_cycles=_BASELINE_REFRESH_CYCLES,
                        load_baselines=load_baselines,
                        engine=engine,
                        map_refresh_cycles=_MAP_REFRESH_CYCLES,
                        refresh_subscriptions=_refresh_subscriptions,
                        asset_id_map=asset_id_map,
                        current_poly_ids=_current_poly_ids,
                        current_kalshi_tickers=_current_kalshi_tickers,
                        partition_maint_cycles=_PARTITION_MAINT_CYCLES,
                        ensure_partitions=_ensure_partitions,
                        find_old_partitions=_find_old_partitions,
                        raw_retention_days=cfg.ingestion.raw_retention_days,
                        drop_old_partitions=_drop_old_partitions,
                        retention_enabled=_retention_enabled,
                        retention_operator_acknowledged=_retention_operator_acknowledged,
                        partition_state=_partition_state,
                    )

            if "polymarket" in live_venues:
                from pmfi.adapters.polymarket import PolymarketAdapter
                _venue_counters.setdefault("polymarket", {"count": 0, "last_event_at": None})
                _poly_gen = _counted_events_for("polymarket")

                def _make_poly():
                    return PolymarketAdapter(
                        asset_ids=list(_current_poly_ids),
                        timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                        initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                        max_backoff=cfg.ingestion.reconnect_max_backoff,
                        reconnect_jitter=cfg.ingestion.reconnect_jitter,
                        subscription_timeout_seconds=cfg.ingestion.polymarket_subscription_timeout_seconds,
                        receive_timeout_seconds=cfg.ingestion.polymarket_receive_timeout_seconds,
                    )

                async def _run_poly(adapter, pool_manager):
                    await run_adapter_pipeline(
                        _poly_gen(adapter.events()),
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
                    status_map=_venue_status,
                    circuit_breaker_failure_threshold=getattr(
                        cfg.ingestion, "circuit_breaker_failure_threshold", 10
                    ),
                    circuit_breaker_window_seconds=getattr(
                        cfg.ingestion, "circuit_breaker_window_seconds", 300.0
                    ),
                    circuit_breaker_recovery_seconds=getattr(
                        cfg.ingestion, "circuit_breaker_recovery_seconds", 60.0
                    ),
                    circuit_breaker_progress_reset_min_events=getattr(
                        cfg.ingestion, "circuit_breaker_progress_reset_min_events", 2
                    ),
                )))

            if "kalshi" in live_venues:
                from pmfi.adapters.kalshi_rest import KalshiRestPollingAdapter
                _venue_counters.setdefault("kalshi", {"count": 0, "last_event_at": None})
                _kalshi_gen = _counted_events_for("kalshi")

                def _make_kalshi():
                    return KalshiRestPollingAdapter(
                        tickers=list(_current_kalshi_tickers),
                        poll_interval_seconds=kalshi_poll_interval_seconds,
                        limit=kalshi_trade_poll_limit,
                        max_pages=kalshi_trade_poll_max_pages,
                        all_market_poll=kalshi_all_market_poll,
                        timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                        initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                        max_backoff=cfg.ingestion.reconnect_max_backoff,
                        reconnect_jitter=cfg.ingestion.reconnect_jitter,
                    )

                async def _run_kalshi(adapter, pool_manager):
                    # Kalshi REST trades always carry the ticker as venue_market_id;
                    # no asset_id_map is needed (there are no unresolved token IDs).
                    await run_adapter_pipeline(
                        _kalshi_gen(adapter.events()),
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
                    status_map=_venue_status,
                    circuit_breaker_failure_threshold=getattr(
                        cfg.ingestion, "circuit_breaker_failure_threshold", 10
                    ),
                    circuit_breaker_window_seconds=getattr(
                        cfg.ingestion, "circuit_breaker_window_seconds", 300.0
                    ),
                    circuit_breaker_recovery_seconds=getattr(
                        cfg.ingestion, "circuit_breaker_recovery_seconds", 60.0
                    ),
                    circuit_breaker_progress_reset_min_events=getattr(
                        cfg.ingestion, "circuit_breaker_progress_reset_min_events", 2
                    ),
                )))

            poly_sub_count = len(_current_poly_ids) if "polymarket" in live_venues else 0
            kalshi_sub_count = len(_current_kalshi_tickers) if "kalshi" in live_venues else 0
            _run_limit_msg = (
                f"stops after {max_seconds} seconds or Ctrl+C."
                if max_seconds else
                "Ctrl+C to stop."
            )
            print(
                f"[ingest] started {len(tasks)} adapter(s) for venues={live_venues}, "
                f"watching {len(watched)} market(s) "
                f"(poly_tokens={poly_sub_count}, kalshi_tickers={kalshi_sub_count}). "
                f"{_run_limit_msg}"
            )
            for _m in watched:
                _title = (_m["title"] or _m["venue_market_id"])[:70]
                print(f"[ingest]   [{_m['venue_code']}] {_title}")
            if tasks:
                if max_seconds:
                    tasks.append(asyncio.create_task(_bounded_shutdown(shutdown, max_seconds)))
                tasks.append(asyncio.create_task(_telemetry_loop()))
                try:
                    done, pending = await asyncio.wait(
                        tasks,
                        return_when=asyncio.FIRST_COMPLETED if max_seconds else asyncio.FIRST_EXCEPTION,
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
    p_replay.add_argument("--report", action="store_true", help="Write a local replay report under reports\\replay")
    p_replay.add_argument("--limit", type=int, default=100, help="Max events when using --from-db (0=unlimited, default: 100)")
    p_replay.add_argument("--from", dest="replay_from", default=None,
                          metavar="TS", help="Start of replay window: ISO 8601 or relative ('24h','7d')")
    p_replay.add_argument("--to", dest="replay_to", default=None,
                          metavar="TS", help="End of replay window: ISO 8601 or relative ('1h','7d')")
    p_replay.add_argument("--venue", dest="replay_venue", default=None,
                          metavar="VENUE", help="Filter by venue_code (e.g. polymarket, kalshi)")
    p_replay.add_argument("--market", dest="replay_market", default=None,
                          metavar="MARKET_ID", help="Filter by venue_market_id")

    p_volume_spike_calibration = sub.add_parser(
        "volume-spike-calibration",
        help="Validate-only comparison replay for candidate volume_spike_v1 knobs",
    )
    p_volume_spike_calibration.add_argument("--from", dest="calibration_from", default=None,
                                            metavar="TS", help="Start of DB replay window: ISO 8601 or relative")
    p_volume_spike_calibration.add_argument("--to", dest="calibration_to", default=None,
                                            metavar="TS", help="End of DB replay window: ISO 8601 or relative")
    p_volume_spike_calibration.add_argument("--limit", type=int, default=0,
                                            help="Max raw_events to replay (0=unlimited, default: 0)")
    p_volume_spike_calibration.add_argument("--venue", dest="calibration_venue", default=None,
                                            metavar="VENUE", help="Filter by venue_code")
    p_volume_spike_calibration.add_argument("--market", dest="calibration_market", default=None,
                                            metavar="MARKET_ID", help="Filter by venue_market_id")
    p_volume_spike_calibration.add_argument("--min-spike-multiplier", type=float, default=None,
                                            help="Candidate volume_spike_v1 min_spike_multiplier")
    p_volume_spike_calibration.add_argument("--min-trade-usd", type=float, default=None,
                                            help="Candidate volume_spike_v1 min_trade_usd")
    p_volume_spike_calibration.add_argument("--min-baseline-trades", type=int, default=None,
                                            help="Candidate volume_spike_v1 min_baseline_trades")
    p_volume_spike_calibration.add_argument("--low-notional-min-baseline-trades", type=int, default=None,
                                            help="Candidate extra baseline trades required for low-notional volume_spike_v1 alerts")
    p_volume_spike_calibration.add_argument("--low-notional-min-baseline-median-usd", type=float, default=None,
                                            help="Candidate minimum baseline median for low-notional volume_spike_v1 alerts")
    p_volume_spike_calibration.add_argument("--low-notional-max-spike-multiplier", type=float, default=None,
                                            help="Candidate maximum observed multiplier for low-notional median-floor suppression")
    p_volume_spike_calibration.add_argument("--low-notional-threshold-usd", type=float, default=None,
                                            help="Candidate low-notional threshold for conditional volume_spike_v1 baseline maturity")
    p_volume_spike_calibration.add_argument("--history-max", type=int, default=None,
                                            help="Candidate volume_spike_v1 history_max")
    p_volume_spike_calibration.add_argument("--cold-start", action="store_true",
                                            help="Do not seed replay state from pre-window DB history")
    p_volume_spike_calibration.add_argument("--export-packet", action="store_true",
                                            help="Write a local calibration packet under reports\\calibration-packets")
    p_volume_spike_calibration.add_argument("--packet-output", default=None,
                                            help="Output JSON path inside reports\\calibration-packets")
    p_volume_spike_calibration.add_argument("--packet-limit", type=int, default=0,
                                            help="Max delta records in packet; 0 exports the full delta set")
    p_volume_spike_calibration.add_argument("--format", choices=["text", "json"], default="text",
                                            help="Output format (default: text)")

    p_calibration_packet_batch = sub.add_parser(
        "calibration-packet-batch",
        help="Export volume-spike calibration packets for explicit independent DB replay windows",
    )
    p_calibration_packet_batch.add_argument(
        "--window",
        action="append",
        required=True,
        help="Independent replay window as NAME:SINCE:UNTIL using timezone-aware ISO timestamps",
    )
    p_calibration_packet_batch.add_argument("--limit", type=int, default=0,
                                            help="Max raw_events per window (0=unlimited, default: 0)")
    p_calibration_packet_batch.add_argument("--venue", dest="calibration_venue", default=None,
                                            metavar="VENUE", help="Filter by venue_code")
    p_calibration_packet_batch.add_argument("--market", dest="calibration_market", default=None,
                                            metavar="MARKET_ID", help="Filter by venue_market_id")
    p_calibration_packet_batch.add_argument("--min-spike-multiplier", type=float, default=None,
                                            help="Candidate volume_spike_v1 min_spike_multiplier")
    p_calibration_packet_batch.add_argument("--min-trade-usd", type=float, default=None,
                                            help="Candidate volume_spike_v1 min_trade_usd")
    p_calibration_packet_batch.add_argument("--min-baseline-trades", type=int, default=None,
                                            help="Candidate volume_spike_v1 min_baseline_trades")
    p_calibration_packet_batch.add_argument("--low-notional-min-baseline-trades", type=int, default=None,
                                            help="Candidate extra baseline trades required for low-notional volume_spike_v1 alerts")
    p_calibration_packet_batch.add_argument("--low-notional-min-baseline-median-usd", type=float, default=None,
                                            help="Candidate minimum baseline median for low-notional volume_spike_v1 alerts")
    p_calibration_packet_batch.add_argument("--low-notional-max-spike-multiplier", type=float, default=None,
                                            help="Candidate maximum observed multiplier for low-notional median-floor suppression")
    p_calibration_packet_batch.add_argument("--low-notional-threshold-usd", type=float, default=None,
                                            help="Candidate low-notional threshold for conditional volume_spike_v1 baseline maturity")
    p_calibration_packet_batch.add_argument("--history-max", type=int, default=None,
                                            help="Candidate volume_spike_v1 history_max")
    p_calibration_packet_batch.add_argument("--cold-start", action="store_true",
                                            help="Do not seed replay state from pre-window DB history")
    p_calibration_packet_batch.add_argument("--packet-output-prefix", default="independent",
                                            help="Lowercase kebab-case output prefix under reports\\calibration-packets")
    p_calibration_packet_batch.add_argument("--packet-limit", type=int, default=0,
                                            help="Max delta records per packet; 0 exports the full delta set")
    p_calibration_packet_batch.add_argument("--format", choices=["text", "json"], default="text",
                                            help="Output format (default: text)")

    p_volume_spike_calibration_sweep = sub.add_parser(
        "volume-spike-calibration-sweep",
        help="Validate-only volume-spike calibration sweep over explicit DB replay windows",
    )
    p_volume_spike_calibration_sweep.add_argument(
        "--window",
        action="append",
        required=True,
        help="Replay window as NAME:SINCE:UNTIL using timezone-aware ISO timestamps",
    )
    p_volume_spike_calibration_sweep.add_argument("--limit", type=int, default=0,
                                                  help="Max raw_events per window (0=unlimited, default: 0)")
    p_volume_spike_calibration_sweep.add_argument("--venue", dest="calibration_venue", default=None,
                                                  metavar="VENUE", help="Filter by venue_code")
    p_volume_spike_calibration_sweep.add_argument("--market", dest="calibration_market", default=None,
                                                   metavar="MARKET_ID", help="Filter by venue_market_id")
    p_volume_spike_calibration_sweep.add_argument("--low-notional-min-baseline-trades", action="append", type=int,
                                                  default=[],
                                                  help="Candidate low-notional minimum baseline trades; repeat")
    p_volume_spike_calibration_sweep.add_argument("--low-notional-threshold-usd", action="append", type=float,
                                                  default=[],
                                                  help="Candidate low-notional threshold USD; repeat")
    p_volume_spike_calibration_sweep.add_argument("--low-notional-min-baseline-median-usd", action="append", type=float,
                                                  default=[],
                                                  help="Candidate low-notional baseline median USD floor; repeat")
    p_volume_spike_calibration_sweep.add_argument("--low-notional-max-spike-multiplier", action="append", type=float,
                                                  default=[],
                                                  help="Candidate low-notional max observed spike multiplier for median-floor suppression; repeat")
    p_volume_spike_calibration_sweep.add_argument("--cold-start", action="store_true",
                                                  help="Do not seed replay state from pre-window DB history")
    p_volume_spike_calibration_sweep.add_argument("--format", choices=["text", "json"], default="text",
                                                  help="Output format (default: text)")

    p_calibration_decision = sub.add_parser(
        "calibration-decision",
        help="Write a local decision record from calibration packet comparison evidence",
    )
    p_calibration_decision.add_argument(
        "--packet",
        action="append",
        default=[],
        help="Calibration packet filename under reports\\calibration-packets; repeat to select multiple",
    )
    p_calibration_decision.add_argument(
        "--decision",
        required=True,
        choices=["no-change", "needs-more-evidence", "change-ready"],
        help="Operator decision for the compared packet evidence",
    )
    p_calibration_decision.add_argument(
        "--rationale",
        required=True,
        help="Non-empty explanation for the decision record",
    )
    p_calibration_decision.add_argument(
        "--include-review-summary",
        action="store_true",
        help="Embed conservative calibration packet review-summary evidence",
    )
    p_calibration_decision.add_argument(
        "--include-cluster-review-summary",
        action="store_true",
        help="Embed local cluster-review coverage evidence for selected packets",
    )
    p_calibration_decision.add_argument(
        "--review",
        action="append",
        default=[],
        help="Cluster review filename under reports\\calibration-cluster-reviews; repeat to select multiple",
    )
    p_calibration_decision.add_argument(
        "--output",
        default=None,
        help="Output JSON path inside reports\\calibration-decisions",
    )
    p_calibration_decision.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    p_calibration_review_queue = sub.add_parser(
        "calibration-review-queue",
        help="Read-only operator queue from local calibration packet delta records",
    )
    p_calibration_review_queue.add_argument(
        "--packet",
        action="append",
        default=[],
        help="Calibration packet filename under reports\\calibration-packets; repeat to select multiple",
    )
    p_calibration_review_queue.add_argument(
        "--state",
        choices=["removed", "added", "all"],
        default="all",
        help="Packet delta state to include (default: all)",
    )
    p_calibration_review_queue.add_argument(
        "--review-group",
        choices=[
            "matched_noise",
            "matched_fp",
            "matched_tp",
            "matched_unreviewed",
            "matched_other",
            "unmatched_replay_only",
            "all",
        ],
        default="all",
        help="Review group to include (default: all)",
    )
    p_calibration_review_queue.add_argument(
        "--market-cluster",
        default=None,
        help="Exact market cluster key to inspect from the filtered queue",
    )
    p_calibration_review_queue.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max queue rows to return (0=unlimited, default: 0)",
    )
    p_calibration_review_queue.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    p_calibration_cluster_review = sub.add_parser(
        "calibration-cluster-review",
        help="Write a local packet-level review artifact for one market cluster",
    )
    p_calibration_cluster_review.add_argument(
        "--packet",
        action="append",
        default=[],
        help="Calibration packet filename under reports\\calibration-packets; repeat to select multiple",
    )
    p_calibration_cluster_review.add_argument(
        "--market-cluster",
        required=True,
        help="Exact market cluster key from calibration-review-queue",
    )
    p_calibration_cluster_review.add_argument(
        "--state",
        choices=["removed", "added", "all"],
        default="removed",
        help="Packet delta state to review (default: removed)",
    )
    p_calibration_cluster_review.add_argument(
        "--review-group",
        choices=[
            "matched_noise",
            "matched_fp",
            "matched_tp",
            "matched_unreviewed",
            "matched_other",
            "unmatched_replay_only",
            "all",
        ],
        default="unmatched_replay_only",
        help="Review group to review (default: unmatched_replay_only)",
    )
    p_calibration_cluster_review.add_argument(
        "--assessment",
        required=True,
        choices=["noise", "false-positive", "true-positive-risk", "uncertain"],
        help="Packet-level assessment for this cluster",
    )
    p_calibration_cluster_review.add_argument(
        "--rationale",
        required=True,
        help="Non-empty packet/raw-event review rationale",
    )
    p_calibration_cluster_review.add_argument(
        "--reviewed-by",
        default=None,
        help="Optional local reviewer identifier",
    )
    p_calibration_cluster_review.add_argument(
        "--output",
        default=None,
        help="Output JSON path inside reports\\calibration-cluster-reviews",
    )
    p_calibration_cluster_review.add_argument(
        "--include-raw-events",
        action="store_true",
        help="Embed read-only local Postgres raw-event lookup evidence in the artifact",
    )
    p_calibration_cluster_review.add_argument(
        "--include-raw-payload",
        action="store_true",
        help="Include full raw JSON payloads when embedding raw-event evidence",
    )
    p_calibration_cluster_review.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    p_calibration_cluster_review_summary = sub.add_parser(
        "calibration-cluster-review-summary",
        help="Read-only coverage summary for cluster review artifacts",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--packet",
        action="append",
        default=[],
        help="Calibration packet filename under reports\\calibration-packets; repeat to select multiple",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--review",
        action="append",
        default=[],
        help="Calibration cluster review filename under reports\\calibration-cluster-reviews; repeat to select multiple",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--state",
        choices=["removed", "added", "all"],
        default="removed",
        help="Packet delta state to summarize (default: removed)",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--review-group",
        choices=[
            "matched_noise",
            "matched_fp",
            "matched_tp",
            "matched_unreviewed",
            "matched_other",
            "unmatched_replay_only",
            "all",
        ],
        default="unmatched_replay_only",
        help="Review group to summarize (default: unmatched_replay_only)",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--market-cluster",
        default=None,
        help="Exact market cluster key to summarize",
    )
    p_calibration_cluster_review_summary.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    p_volume_spike_floor_audit = sub.add_parser(
        "volume-spike-floor-audit",
        help="Validate-only replay audit for current volume_spike_v1 min_trade_usd floor",
    )
    p_volume_spike_floor_audit.add_argument("--from", dest="audit_from", default=None,
                                            metavar="TS", help="Start of DB replay window: ISO 8601 or relative")
    p_volume_spike_floor_audit.add_argument("--to", dest="audit_to", default=None,
                                            metavar="TS", help="End of DB replay window: ISO 8601")
    p_volume_spike_floor_audit.add_argument("--limit", type=int, default=0,
                                            help="Max raw_events to replay (0=unlimited, default: 0)")
    p_volume_spike_floor_audit.add_argument("--venue", dest="audit_venue", default=None,
                                            metavar="VENUE", help="Filter by venue_code")
    p_volume_spike_floor_audit.add_argument("--market", dest="audit_market", default=None,
                                            metavar="MARKET_ID", help="Filter by venue_market_id")
    p_volume_spike_floor_audit.add_argument("--cold-start", action="store_true",
                                            help="Do not seed replay state from pre-window DB history")
    p_volume_spike_floor_audit.add_argument("--format", choices=["text", "json"], default="text",
                                            help="Output format (default: text)")

    sub.add_parser("status", help="Show current PMFI configuration and status")
    sub.add_parser("db-verify", help="Verify Postgres connectivity")
    p_init = sub.add_parser("init", help="Idempotently scaffold local config and initialize the local DB schema")
    p_init.add_argument(
        "--discover",
        action="store_true",
        help="Print the explicit market-discovery next step after initialization",
    )
    p_init.add_argument(
        "--watch-top",
        type=_positive_int,
        default=None,
        help="Print a discovery next step scoped to the top N markets",
    )
    p_doctor = sub.add_parser("doctor", help="Read-only local diagnostics with actionable fix hints")
    p_doctor.add_argument("--json", action="store_true", dest="json_output", help="Emit JSON diagnostics")
    p_monitor = sub.add_parser("monitor", help="Start live monitoring (requires live mode enabled)")
    p_monitor.add_argument("--fixture-replay", action="store_true", help="Stream fixture events as a live demo")
    p_monitor.add_argument("--fixture-dir", default=None, help="Path to fixture dir (default: tests/fixtures/raw)")
    p_monitor.add_argument("--delay", type=float, default=1.0, help="Seconds between fixture events (default: 1.0)")

    p_alerts = sub.add_parser("alerts", help="Alert commands: list, explain, review, review-packet, outcome-audit, fp-rate, serve")
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
    p_alerts_list.add_argument(
        "--triage-flag",
        action="append",
        choices=[
            "low_notional",
            "thin_baseline",
            "near_threshold",
            "degraded_data_quality",
            "missing_lineage",
        ],
        default=[],
        help="Filter by deterministic triage flag; repeat to require every flag",
    )
    review_state = p_alerts_list.add_mutually_exclusive_group()
    review_state.add_argument("--unreviewed", action="store_true", help="Show only alerts with no review rows")
    review_state.add_argument("--reviewed", action="store_true", help="Show only alerts with at least one review row")
    p_alerts_list.add_argument(
        "--review-label",
        choices=["tp", "fp", "noise"],
        default=None,
        help="Show alerts whose latest review label matches; can be combined with --reviewed",
    )
    p_alerts_explain = alerts_sub.add_parser("explain", help="Show detailed plain-English explanation of a single alert")
    p_alerts_explain.add_argument("alert_id", help="Alert UUID or 8-char prefix (from 'pmfi alerts list')")
    p_alerts_explain.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    p_alerts_serve = alerts_sub.add_parser("serve", help="Run local HTTP receiver for alert delivery")
    p_alerts_serve.add_argument("--port", type=int, default=8765)
    p_alerts_serve.add_argument("--host", default="127.0.0.1")
    p_alerts_review = alerts_sub.add_parser("review", help="Record a review label for an alert (tp/fp/noise)")
    p_alerts_review.add_argument("alert_id", help="Alert UUID or 8-char prefix (from 'pmfi alerts list' ID column)")
    p_alerts_review.add_argument("--label", required=True, choices=["tp", "fp", "noise"],
                                  help="tp=true-positive, fp=false-positive, noise=not-actionable")
    p_alerts_review.add_argument("--category", default=None, metavar="CAT",
                                  help="Optional FP category (e.g. stale_baseline, thin_market)")
    p_alerts_review.add_argument("--notes", default=None, metavar="TEXT", help="Optional free-text notes")
    p_alerts_review.add_argument("--reviewed-by", dest="reviewed_by", default=None,
                                  metavar="NAME", help="Reviewer name (optional)")
    p_alerts_review.add_argument("--dry-run", action="store_true", help="Preview the review target without writing")
    p_alerts_review_packet = alerts_sub.add_parser(
        "review-packet",
        help="Export a local JSON packet for reviewed or unreviewed alert cohorts",
    )
    p_alerts_review_packet.add_argument("--since", default="24h", help="Alert-created window: '24h', '7d', or timezone-aware ISO datetime")
    p_alerts_review_packet.add_argument("--rule", default=None, metavar="RULE_KEY", help="Filter by alert rule key")
    p_alerts_review_packet.add_argument(
        "--review-state",
        choices=["reviewed", "unreviewed"],
        default="reviewed",
        help="Export latest-reviewed alerts or unreviewed queue alerts",
    )
    p_alerts_review_packet.add_argument(
        "--review-label",
        choices=["tp", "fp", "noise"],
        default=None,
        help="Filter by latest review label",
    )
    p_alerts_review_packet.add_argument("--category", default=None, metavar="CAT", help="Filter by latest review category")
    p_alerts_review_packet.add_argument("--limit", type=int, default=50, help="Maximum alert rows to include")
    p_alerts_review_packet.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Output JSON path (default: reports\\review-packets\\review-packet-<timestamp>.json)",
    )
    p_alerts_review_packet.add_argument("--format", choices=["json"], default="json", help="Output format")
    p_alerts_outcome_audit = alerts_sub.add_parser(
        "outcome-audit",
        help="Audit directional alert outcome_key rows against dominant_side evidence",
    )
    p_alerts_outcome_audit.add_argument("--since", default="24h", help="Alert-fired window start: '24h', '7d', or timezone-aware ISO datetime")
    p_alerts_outcome_audit.add_argument("--until", default=None, help="Optional timezone-aware ISO alert-fired window end")
    p_alerts_outcome_audit.add_argument(
        "--rule",
        action="append",
        choices=["directional_cluster_v1", "momentum_v1"],
        default=None,
        help="Directional rule to audit; repeat to include multiple rules (default: both)",
    )
    p_alerts_outcome_audit.add_argument("--limit", type=int, default=50, help="Maximum directional alert rows to inspect")
    p_alerts_outcome_audit.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    p_alerts_outcome_audit.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if no rows, mismatches, or rows missing dominant_side are found",
    )
    p_alerts_lineage_check = alerts_sub.add_parser(
        "lineage-check",
        help="Report alerts with dangling raw_event_id/trade_id lineage references",
    )
    p_alerts_lineage_check.add_argument("--since", default=None, help="Optional alert-created window start: '24h', '7d', or timezone-aware ISO datetime")
    p_alerts_lineage_check.add_argument("--limit", type=int, default=50, help="Maximum orphan rows to show")
    p_alerts_lineage_check.add_argument("--format", choices=["table", "json"], default="table", help="Output format")
    p_alerts_lineage_check.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any dangling lineage references are found",
    )
    p_alerts_fp_rate = alerts_sub.add_parser("fp-rate", help="Show false-positive rate from recorded reviews")
    p_alerts_fp_rate.add_argument("--since", default=None, help="Time window: '7d', '24h', or ISO datetime")
    p_alerts_fp_rate.add_argument("--rule", default=None, metavar="RULE_KEY", help="Filter by rule key")

    p_ingest = sub.add_parser("ingest", help="Persistent live ingest daemon (requires live venue enabled in config)")
    p_ingest.add_argument("--venue", action="append", metavar="VENUE",
                          help="Venue to ingest from: polymarket or kalshi (can repeat). Default: all enabled in config.")
    p_ingest.add_argument("--dry-run", action="store_true", help="Connect and log events but do not persist to DB")
    p_ingest.add_argument("--max-events", type=int, default=0, metavar="N",
                          help="Stop dry-run after N events (0=unlimited, default: run until Ctrl+C)")
    p_ingest.add_argument("--max-seconds", type=int, default=0, metavar="N",
                          help="Stop after N seconds (0=unlimited, default: run until Ctrl+C)")
    p_ingest.add_argument("--kalshi-poll-interval-seconds", type=_positive_float, default=None, metavar="N",
                          help="Override Kalshi REST polling interval for this ingest run")
    p_ingest.add_argument("--kalshi-trade-poll-limit", type=_positive_int, default=None, metavar="N",
                          help="Override Kalshi REST trade count cap for this ingest run")
    p_ingest.add_argument("--kalshi-trade-poll-max-pages", type=_positive_int, default=None, metavar="N",
                          help="Override Kalshi REST trade poll max pages for this ingest run")
    p_ingest.add_argument("--kalshi-all-market-poll", action="store_true",
                          help="Poll Kalshi all-market recent trades once per cycle, filtered to watched tickers")
    p_ingest.add_argument("--log-file", default=None, dest="log_file", metavar="PATH",
                          help="Write daemon logs to this file (RotatingFileHandler, 5 MB × 3). "
                               "Overrides app.log_file from config.")

    sub.add_parser("stats", help="Show aggregate DB statistics (row counts per table)")

    p_data_coverage = sub.add_parser(
        "data-coverage",
        help="Read-only raw-event disposition coverage report",
    )
    p_data_coverage.add_argument("--since", default=None, help="Window start: ISO 8601 or relative such as 24h")
    p_data_coverage.add_argument("--until", default=None, help="Window end: ISO 8601 or relative such as 1h")
    p_data_coverage.add_argument("--venue", choices=["polymarket", "kalshi"], default=None, help="Filter by venue")
    p_data_coverage.add_argument(
        "--include-synthetic",
        action="store_true",
        help="Include known synthetic fixture markers such as Polymarket pm-* markets",
    )
    p_data_coverage.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_backtest_analytics = sub.add_parser(
        "backtest-analytics",
        help="Read-only historical replay analytics with alert-review governance",
    )
    p_backtest_analytics.add_argument("--from", dest="backtest_from", default=None, help="Replay window start")
    p_backtest_analytics.add_argument("--to", dest="backtest_to", default=None, help="Replay window end")
    p_backtest_analytics.add_argument("--limit", type=int, default=0, help="Max raw events to replay (0=unlimited)")
    p_backtest_analytics.add_argument("--venue", dest="backtest_venue", choices=["polymarket", "kalshi"], default=None)
    p_backtest_analytics.add_argument("--market", dest="backtest_market", default=None, help="Filter by venue_market_id")
    p_backtest_analytics.add_argument(
        "--volume-spike-min-trade-usd",
        action="append",
        type=float,
        default=None,
        help="Candidate volume_spike_v1 min_trade_usd value; repeat for a sweep",
    )
    p_backtest_analytics.add_argument("--cold-start", action="store_true", help="Do not seed replay accumulators")
    p_backtest_analytics.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_backtest = sub.add_parser(
        "backtest",
        help="Replay DB history and show what alerts would fire under current rules",
    )
    p_backtest.add_argument("--from", dest="backtest_from", default=None, help="Replay window start")
    p_backtest.add_argument("--to", dest="backtest_to", default=None, help="Replay window end")
    p_backtest.add_argument("--limit", type=int, default=200, help="Max raw events to replay (default: 200, 0=unlimited)")
    p_backtest.add_argument("--venue", dest="backtest_venue", choices=["polymarket", "kalshi"], default=None)
    p_backtest.add_argument("--market", dest="backtest_market", default=None, help="Filter by venue_market_id")
    p_backtest.add_argument("--cold-start", action="store_true", help="Do not seed replay accumulators")
    p_backtest.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_rules = sub.add_parser("rules", help="Inspect and tune alert rules")
    rules_sub = p_rules.add_subparsers(dest="rules_cmd", required=False)
    rules_sub.add_parser("list", help="Print all alert rules with enabled state and thresholds")
    p_rules_enable = rules_sub.add_parser("enable", help="Enable a rule by name")
    p_rules_enable.add_argument("rule_id", help="Rule ID, e.g. volume_spike_v1")
    p_rules_disable = rules_sub.add_parser("disable", help="Disable a rule by name")
    p_rules_disable.add_argument("rule_id", help="Rule ID, e.g. volume_spike_v1")
    p_rules_set = rules_sub.add_parser("set", help="Set an existing field on a rule")
    p_rules_set.add_argument("rule_id", help="Rule ID")
    p_rules_set.add_argument("field", help="Existing field name")
    p_rules_set.add_argument("value", help="New value parsed to the current field type")

    p_raw_events = sub.add_parser("raw-events", help="Inspect raw event lineage rows by raw_event_id")
    p_raw_events.add_argument("--id", action="append", type=_positive_int, required=True, help="Raw event ID to inspect; repeat for multiple")
    p_raw_events.add_argument("--include-payload", action="store_true", help="Include full raw JSON payload in JSON output")
    p_raw_events.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")

    p_dl = sub.add_parser("dead-letters", help="Show recent normalization failures")
    p_dl.add_argument("--limit", type=int, default=20, help="Number of dead letters to show (default: 20)")
    p_dl.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    dl_sub = p_dl.add_subparsers(dest="dead_letters_cmd", required=False)
    p_dl_resolve = dl_sub.add_parser("resolve", help="Mark one unresolved dead letter resolved by ID or unique prefix")
    p_dl_resolve.add_argument("dead_letter_id_or_prefix", help="Dead-letter UUID or unique prefix from the ID column")
    p_dl_resolve.add_argument("--dry-run", action="store_true", help="Preview the matched row without updating it")

    p_watch = sub.add_parser("watch", help="Live-refreshing alert display (requires DB)")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds (default: 5)")
    p_watch.add_argument("--limit", type=int, default=15, help="Number of alerts to show (default: 15)")
    p_watch.add_argument("--rule", metavar="RULE_KEY", help="Filter by rule key")
    p_watch.add_argument("--venue", metavar="VENUE", help="Filter by venue code")
    p_watch.add_argument("--severity", choices=["high", "medium", "low"], help="Filter by severity")

    p_markets = sub.add_parser("markets", help="Market commands: list, discover, sync-one, recent-trades, refresh-watchlist, watch, unwatch")
    markets_sub = p_markets.add_subparsers(dest="markets_cmd", required=False)
    p_markets_list = markets_sub.add_parser("list", help="List markets in DB ranked by volume")
    p_markets_list.add_argument("--limit", type=int, default=20)
    p_markets_list.add_argument("--watched", action="store_true", help="Show only watched markets")
    p_markets_list.add_argument("--venue", choices=["polymarket", "kalshi"], default=None,
                                help="Filter by venue code")
    p_markets_list.add_argument("--search", metavar="TEXT", help="Filter by title or venue id substring (case-insensitive)")
    p_markets_list.add_argument("--sort", choices=["volume", "trades", "last-trade"], default="volume",
                                help="Sort order: volume (default), trades, last-trade")
    p_markets_list.add_argument("--min-volume", type=float, default=None, metavar="USD",
                                help="Only show markets with volume >= this value")
    p_markets_list.add_argument("--format", choices=["table", "json"], default="table",
                                help="Output format (default: table)")
    p_markets_discover = markets_sub.add_parser("discover", help="Fetch active markets from venue REST API and sync to DB")
    p_markets_discover.add_argument("--venue", default="polymarket", choices=["polymarket", "kalshi"],
                                    help="Venue to discover markets from (default: polymarket)")
    p_markets_discover.add_argument("--limit", type=int, default=100, help="Max markets to fetch (default: 100)")
    p_markets_discover.add_argument("--min-volume", type=float, default=None, metavar="USD", help="Minimum market volume filter")
    p_markets_discover.add_argument("--watch-top", type=int, default=None, metavar="N",
                                    help="After syncing, auto-watch the top positive N markets by volume")
    p_markets_sync_one = markets_sub.add_parser("sync-one", help="Fetch one Kalshi market by ticker and sync it to the local DB")
    p_markets_sync_one.add_argument("ticker", help="Kalshi market ticker (e.g. KXBTCD-23DEC3100)")
    p_markets_sync_one.add_argument("--venue", default="kalshi", choices=["kalshi"],
                                    help="Venue to sync from (currently only kalshi)")
    p_markets_sync_one.add_argument("--watch", action="store_true", help="Mark the synced market watched")
    p_markets_fetch_trades = markets_sub.add_parser("fetch-trades", help="Fetch recent trades from Kalshi REST API (no auth needed)")
    p_markets_fetch_trades.add_argument("ticker", help="Kalshi market ticker (e.g. KXBTCD-23DEC3100)")
    p_markets_fetch_trades.add_argument("--limit", type=int, default=50, help="Max trades to fetch (default: 50)")
    p_markets_fetch_trades.add_argument("--save-fixtures", action="store_true", help="Save trades as replay fixtures in tests/fixtures/live/")
    p_markets_fetch_trades.add_argument("--force", action="store_true", help="Skip the PMFI_ENABLE_LIVE safety gate")
    p_markets_recent_trades = markets_sub.add_parser(
        "recent-trades",
        help="List recently traded Kalshi tickers from the public all-market trades endpoint",
    )
    p_markets_recent_trades.add_argument("--limit", type=int, default=50, help="Max trades to fetch (default: 50)")
    p_markets_recent_trades.add_argument(
        "--since-minutes",
        type=int,
        default=120,
        help="Look back this many minutes using Kalshi min_ts (default: 120)",
    )
    p_markets_recent_trades.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_markets_recent_trades.add_argument("--force", action="store_true", help="Skip the PMFI_ENABLE_LIVE safety gate")
    p_markets_refresh_watchlist = markets_sub.add_parser(
        "refresh-watchlist",
        help="Probe recent Kalshi trades and optionally sync/watch the top tickers",
    )
    p_markets_refresh_watchlist.add_argument("--limit", type=int, default=50, help="Max trades to fetch (default: 50)")
    p_markets_refresh_watchlist.add_argument(
        "--since-minutes",
        type=int,
        default=120,
        help="Look back this many minutes using Kalshi min_ts (default: 120)",
    )
    p_markets_refresh_watchlist.add_argument("--top", type=int, default=5, help="Number of recent tickers to select (default: 5)")
    p_markets_refresh_watchlist.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_markets_refresh_watchlist.add_argument("--force", action="store_true", help="Skip the PMFI_ENABLE_LIVE safety gate")
    p_markets_refresh_watchlist.add_argument("--sync", action="store_true", help="Sync selected Kalshi markets to the local DB")
    p_markets_refresh_watchlist.add_argument("--watch", action="store_true", help="Mark synced Kalshi markets watched; requires --sync")
    p_markets_refresh_watchlist.add_argument(
        "--replace-watch",
        action="store_true",
        help="With --sync --watch, unwatch other Kalshi markets after selecting active tickers",
    )
    p_markets_watch = markets_sub.add_parser("watch", help="Add market(s) to the watch list (positional, --top N, or --search TEXT)")
    p_markets_watch.add_argument("market_id", nargs="?", default=None,
                                 help="venue_market_id to watch (e.g. Polymarket condition_id); omit to use --top or --search")
    p_markets_watch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")
    p_markets_watch.add_argument("--top", type=int, default=None,
                                 help="Watch the top positive N markets by volume (stateless, no index file)")
    p_markets_watch.add_argument("--search", default=None,
                                 help="Watch all markets matching title search (stateless)")
    p_markets_unwatch = markets_sub.add_parser("unwatch", help="Remove market(s) from the watch list (positional or --search TEXT)")
    p_markets_unwatch.add_argument("market_id", nargs="?", default=None,
                                   help="venue_market_id to unwatch; omit to use --search")
    p_markets_unwatch.add_argument("--venue", default="polymarket", help="Venue code (default: polymarket)")
    p_markets_unwatch.add_argument("--search", default=None,
                                   help="Unwatch all markets matching title search (stateless)")

    p_report = sub.add_parser("report", help="Summary report of recent alert activity")
    p_report.add_argument("--since", default="24h", help="Time window: '1h', '24h', '7d', or ISO datetime (default: 24h)")
    p_report.add_argument("--format", choices=["table", "json"], default="table")

    p_soak = sub.add_parser("soak", help="Check read-only DB evidence for a completed live ingest soak")
    soak_window = p_soak.add_mutually_exclusive_group()
    soak_window.add_argument("--since", default=None, help="Explicit timezone-aware ISO timestamp start for the window")
    soak_window.add_argument("--window", default="2h", help="Lookback window: 60m, 2h, or 1d (default: 2h)")
    p_soak.add_argument("--until", default=None, help="Explicit timezone-aware ISO timestamp end for the window")
    p_soak.add_argument("--min-duration-minutes", type=int, default=60,
                        help="Minimum first-to-last raw evidence span in minutes (default: 60)")
    p_soak.add_argument("--min-required-venue-duration-minutes", type=non_negative_int, default=None,
                        help="Minimum first-to-last raw evidence span for each required venue")
    p_soak.add_argument("--min-raw-events", type=int, default=1,
                        help="Minimum raw_events in the window (default: 1)")
    p_soak.add_argument("--min-trades", type=int, default=1,
                        help="Minimum normalized_trades in the window (default: 1)")
    p_soak.add_argument("--required-venue", action="append", default=[],
                        help="Venue that must have raw and trade evidence; repeat or comma-separate")
    p_soak.add_argument("--max-dead-letters", type=int, default=0,
                        help="Maximum unresolved dead_letters created in the window (default: 0)")
    p_soak.add_argument("--max-incidents", type=int, default=0,
                        help="Maximum open data_quality_incidents (default: 0)")
    p_soak.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")

    # baselines command
    p_baselines = sub.add_parser("baselines", help="Compute and manage alert baselines from historical trades")
    baselines_sub = p_baselines.add_subparsers(dest="baselines_cmd")
    p_baselines_compute = baselines_sub.add_parser("compute", help="Compute baselines from DB trades")
    p_baselines_compute.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    p_baselines_compute.add_argument("--min-samples", type=int, default=10, dest="min_samples", help="Min trades required per market (default: 10)")
    p_baselines_compute.add_argument("--save", action="store_true", help="Save computed baselines to config/baselines.json")
    baselines_sub.add_parser("show", help="Show current baselines (reads DB market_baselines; falls back to config/baselines.json)")

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

    p_live = sub.add_parser("live", help="Continuous live capture (runs indefinitely, Ctrl+C to stop; requires PMFI_ENABLE_LIVE=1)")
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
    p_dashboard = sub.add_parser("dashboard", help="Run the localhost ingest-rate dashboard and alert-review endpoint")
    p_dashboard.add_argument("--port", type=int, default=8766, help="Localhost port (default: 8766)")
    p_dashboard.add_argument("--db-url", default=None, dest="db_url", help="Override database URL (default: from config)")

    p_review_pass = sub.add_parser("review-pass", help="Governance review pass")
    p_review_pass.add_argument("--format", choices=["text", "json"], default="text",
                               help="Output format (default: text)")

    p_health = sub.add_parser("health", help="Check daemon heartbeat freshness (exit 0=fresh, 1=stale/missing)")
    p_health.add_argument("--max-age-seconds", type=float, default=None, dest="max_age_seconds",
                          help="Staleness threshold in seconds (default: 120)")
    p_health.add_argument("--json", action="store_true", dest="json_output",
                          help="Output as JSON")
    p_health.add_argument("--heartbeat-path", default=None, dest="heartbeat_path",
                          help="Override heartbeat file path (default: reports/health/heartbeat.json)")
    p_health.add_argument("--venue-stale-seconds", type=int, default=None, dest="venue_stale_seconds",
                          help="Per-venue staleness threshold in seconds (default: 600 or health.venue_stale_seconds from config)")


def main(argv: list[str] | None = None) -> int:
    # Parse args first so --log-file CLI flag can override config before logging starts.
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "review-pass":
        return cmd_review_pass(args)

    # Load config to honour app.log_level and app.log_file; fall back to defaults
    # if config file is missing (e.g. during tests without a local app.yaml).
    try:
        from pmfi.config import load_config as _load_config
        _cfg = _load_config()
        _log_level = _cfg.log_level
        _log_file = _cfg.log_file
    except Exception:
        _log_level = "INFO"
        _log_file = None

    # CLI --log-file (ingest subcommand) overrides config value.
    _cli_log_file = getattr(args, "log_file", None)
    if _cli_log_file:
        _log_file = _cli_log_file

    _setup_logging(level=_log_level, log_file=_log_file)
    cmd = args.command

    if cmd in ("replay", "replay-fixtures"):
        return cmd_replay(args)
    elif cmd == "volume-spike-calibration":
        return cmd_volume_spike_calibration(args)
    elif cmd == "volume-spike-calibration-sweep":
        return cmd_volume_spike_sweep(args)
    elif cmd == "calibration-packet-batch":
        return cmd_calibration_packet_batch(args)
    elif cmd == "calibration-decision":
        return cmd_calibration_decision(args)
    elif cmd == "calibration-review-queue":
        return cmd_calibration_review_queue(args)
    elif cmd == "calibration-cluster-review":
        return cmd_calibration_cluster_review(args)
    elif cmd == "calibration-cluster-review-summary":
        return cmd_calibration_cluster_review_summary(args)
    elif cmd == "volume-spike-floor-audit":
        return cmd_volume_spike_floor_audit(args)
    elif cmd == "status":
        return cmd_status(args)
    elif cmd == "db-verify":
        return cmd_db_verify(args)
    elif cmd == "init":
        return cmd_init(args)
    elif cmd == "doctor":
        return cmd_doctor(args)
    elif cmd == "monitor":
        return cmd_monitor(args)
    elif cmd == "alerts":
        return cmd_alerts(args)
    elif cmd == "stats":
        return cmd_stats(args)
    elif cmd == "data-coverage":
        return cmd_data_coverage(args)
    elif cmd == "backtest-analytics":
        return cmd_backtest_analytics(args)
    elif cmd == "backtest":
        return cmd_backtest(args)
    elif cmd == "rules":
        return cmd_rules(args)
    elif cmd == "raw-events":
        return cmd_raw_events(args)
    elif cmd == "dead-letters":
        return cmd_dead_letters(args)
    elif cmd == "watch":
        return cmd_watch(args)
    elif cmd == "markets":
        return cmd_markets(args)
    elif cmd == "report":
        return cmd_report(args)
    elif cmd == "soak":
        return cmd_soak(args)
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
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
