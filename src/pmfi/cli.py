from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from uuid import UUID
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def _database_display_target(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.hostname:
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return f"{host}{parsed.path or ''}"
    return database_url.split("@")[-1]


def _sanitize_database_error(exc: Exception, database_url: str) -> str:
    message = str(exc)
    display_target = _database_display_target(database_url)
    if database_url:
        message = message.replace(database_url, display_target)
    parsed = urlsplit(database_url)
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        message = message.replace(f"{userinfo}@", "")
    if parsed.password:
        message = message.replace(parsed.password, "<redacted>")
    return message


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )


def _print_db_unavailable(command: str, exc: Exception) -> None:
    print(f"[{command}] DB unavailable: {exc}")
    print("Run 'python scripts\\db_local.py up' and 'python scripts\\db_local.py verify' first.")


_DB_UNAVAILABLE_NEXT_ACTIONS = (
    "Start local Postgres with 'python scripts\\db_local.py up'.",
    "Verify local Postgres with 'python scripts\\db_local.py verify'.",
)


def _db_unavailable_payload(
    exc: Exception | str,
    *,
    extra: dict | None = None,
    extra_next_actions: list[str] | tuple[str, ...] = (),
) -> dict:
    payload = {"ok": False}
    if extra:
        payload.update(extra)
    payload["error"] = f"DB unavailable: {exc}"
    payload["next_actions"] = [*_DB_UNAVAILABLE_NEXT_ACTIONS, *extra_next_actions]
    return payload


def _print_json_db_unavailable(
    exc: Exception | str,
    *,
    extra: dict | None = None,
    extra_next_actions: list[str] | tuple[str, ...] = (),
) -> None:
    print(json.dumps(
        _db_unavailable_payload(exc, extra=extra, extra_next_actions=extra_next_actions),
        indent=2,
        sort_keys=True,
    ))


_ALERT_REVIEW_LABEL_ALIASES = {
    "true-positive": "true_positive",
    "true_positive": "true_positive",
    "tp": "true_positive",
    "false-positive": "false_positive",
    "false_positive": "false_positive",
    "fp": "false_positive",
    "noise": "noise",
    "unsure": "unsure",
}


def _normalize_alert_review_label(label: str) -> str:
    normalized = _ALERT_REVIEW_LABEL_ALIASES.get(label.strip().lower())
    if normalized is None:
        allowed = ", ".join(sorted(_ALERT_REVIEW_LABEL_ALIASES))
        raise ValueError(f"invalid review label {label!r}; expected one of: {allowed}")
    return normalized


def _parse_alert_uuid(alert_id: str) -> str:
    try:
        return str(UUID(alert_id))
    except ValueError as exc:
        raise ValueError(f"invalid alert_id {alert_id!r}; expected a UUID from 'pmfi alerts list --format json'") from exc


def _parse_relative_or_iso_since(value: str, *, option_name: str = "--since"):
    import re
    from datetime import datetime, timedelta, timezone

    raw = value.strip()
    match = re.match(r"^(\d+)([hdm])$", raw)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError(f"invalid {option_name}; expected a positive relative window like '24h' or '30d'")
        seconds = {"h": 3600, "d": 86400, "m": 60}[match.group(2)] * amount
        return datetime.now(timezone.utc) - timedelta(seconds=seconds)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid {option_name} value {value!r}; expected ISO datetime or relative value like '24h' or '30d'") from exc


def _json_default(obj):  # noqa: ANN001
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _json_error_payload(error: str, *, next_actions: list[str] | None = None, **extra) -> dict:
    payload = {"ok": False, **extra, "error": error}
    if next_actions is not None:
        payload["next_actions"] = next_actions
    return payload


def _print_markets_list_json_db_unavailable(exc: Exception) -> None:
    _print_json_db_unavailable(
        exc,
        extra_next_actions=("Populate markets with 'pmfi markets discover' or 'pmfi replay --persist'.",),
    )


def cmd_replay(args: argparse.Namespace) -> int:
    from pmfi.delivery.stdout import deliver_stdout

    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else ROOT / "tests" / "fixtures" / "raw"

    _baselines = None
    _baselines_path = ROOT / "config" / "baselines.json"
    if _baselines_path.exists():
        import json as _json
        try:
            _baselines = _json.loads(_baselines_path.read_text(encoding="utf-8"))
            logging.debug("loaded %d baseline(s) from %s", len(_baselines), _baselines_path)
        except Exception:
            pass

    if getattr(args, "from_db", False):
        from pmfi.config import load_config
        from pmfi.db import create_pool, close_pool
        from pmfi.replay import replay_from_db

        limit = getattr(args, "limit", 100)

        async def _run_from_db():
            cfg = load_config()
            pool = await create_pool(cfg.database.url)
            try:
                return await replay_from_db(pool, limit=limit, verbose=args.verbose, baselines=_baselines)
            finally:
                await close_pool(pool)

        try:
            results = asyncio.run(_run_from_db())
        except Exception as exc:
            _print_db_unavailable("from-db", exc)
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
                return await replay_fixtures_persist(fixture_dir, pool, verbose=args.verbose, baselines=_baselines)
            finally:
                await close_pool(pool)

        try:
            results = asyncio.run(_run_persist())
        except Exception as exc:
            _print_db_unavailable("persist", exc)
            return 1
        print(f"[persist] processed {len(results)} normalized fixture(s) through DB pipeline")
    else:
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
    from pmfi.config import enabled_unsupported_features, feature_flags_dict, load_config
    from pmfi.pipeline.engine import AlertEngine

    fmt = getattr(args, "format", "table")
    cfg = load_config()
    database_target = _database_display_target(cfg.database.url)
    features = feature_flags_dict(cfg.features)
    unsupported_enabled = enabled_unsupported_features(cfg.features)

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
                    stats["market_outcomes"] = await pool.fetchval("SELECT COUNT(*) FROM market_outcomes")
                    stats["last_trade"] = await pool.fetchval("SELECT MAX(received_at) FROM normalized_trades")
                except Exception:
                    pass
                return "ok", stats
            finally:
                await close_pool(pool)

        db_status, db_stats = asyncio.run(_db_check())
    except Exception as exc:
        db_status = f"error: {_sanitize_database_error(exc, cfg.database.url)}"

    if fmt == "json":
        payload = {
            "ok": True,
            "database": {
                "target": database_target,
                "status": db_status,
                "stats": db_stats,
            },
            "live_mode_enabled": bool(cfg.live_mode_enabled),
            "features": features,
            "unsupported_enabled_features": unsupported_enabled,
            "delivery": {
                "default_delivery": cfg.alerts.default_delivery,
            },
            "alert_rules": {
                "enabled_count": len(enabled_rules),
                "enabled": enabled_rules,
            },
            "fixtures": {
                "raw_dir": "tests/fixtures/raw",
                "count": fixture_count,
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=True, default=_health_json_default))
        return 0

    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()
        db_color = "green" if db_status == "ok" else "red"
        db_line = f"[bold]DB:[/bold] {database_target} [{db_color}]{db_status}[/{db_color}]"
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
            f"[bold]Unsupported enabled features:[/bold] {', '.join(unsupported_enabled) if unsupported_enabled else 'none'}",
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
                f" asset_id_mappings={db_stats.get('market_outcomes', '?')}"
                f" last_trade={last_trade_str}"
            )
        console.print(Panel("\n".join(lines), title="PMFI Status", expand=False))
    except ImportError:
        print(f"PMFI local | db={db_status} | live={cfg.live_mode_enabled} | rules={len(enabled_rules)} | fixtures={fixture_count}")
    return 0


def _health_check(name: str, status: str, message: str, **details) -> dict:
    check = {"name": name, "status": status, "message": message}
    if details:
        check["details"] = details
    return check


def _health_row(row) -> dict | None:
    if not row:
        return None
    return dict(row)


def _health_json_default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _print_health_json(result: dict) -> None:
    print(json.dumps(result, indent=2, default=_health_json_default))


def _print_health_table(result: dict) -> None:
    print(f"PMFI Health: {result['status']} (ok={str(result['ok']).lower()})")
    for check in result["checks"]:
        status = check["status"].upper()
        print(f"{status:5} {check['name']}: {check['message']}")
        details = check.get("details") or {}
        for key in sorted(details):
            value = details[key]
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            print(f"      {key}: {value}")
    if result.get("next_actions"):
        print("Next actions:")
        for action in result["next_actions"]:
            print(f"  - {action}")


def cmd_health(args: argparse.Namespace) -> int:
    fmt = getattr(args, "format", "table")
    checks: list[dict] = []
    next_actions: list[str] = []
    fatal = False

    supported_delivery_modes = {"console", "file", "localhost_http_receiver"}
    cfg = None

    try:
        from pmfi.config import enabled_unsupported_features, feature_flags_dict, load_config

        cfg = load_config()
        features = feature_flags_dict(cfg.features)
        unsupported_enabled = enabled_unsupported_features(cfg.features)
        delivery_mode = getattr(cfg.alerts, "default_delivery", None)
        allowed_modes = getattr(cfg.alerts, "allowed_delivery_modes", [])
        delivery_allowed = delivery_mode in allowed_modes
        delivery_supported = delivery_mode in supported_delivery_modes
        checks.append(_health_check("config", "pass", "config loaded and validated"))
        checks.append(
            _health_check(
                "delivery",
                "pass" if delivery_allowed and delivery_supported else "fail",
                (
                    f"delivery mode {delivery_mode!r} is allowed and supported"
                    if delivery_allowed and delivery_supported
                    else f"delivery mode {delivery_mode!r} is not allowed or not supported by ingest"
                ),
                mode=delivery_mode,
                allowed_modes=allowed_modes,
                supported_modes=sorted(supported_delivery_modes),
            )
        )
        if not delivery_allowed or not delivery_supported:
            fatal = True
            next_actions.append("Set alerts.default_delivery to an allowed mode supported by ingest: console, file, or localhost_http_receiver.")
        if unsupported_enabled:
            fatal = True
            checks.append(
                _health_check(
                    "feature_support",
                    "fail",
                    "unsupported feature flag(s) are enabled and ignored by the current local-only build",
                    unsupported_enabled_features=unsupported_enabled,
                    features=features,
                )
            )
            next_actions.append(
                "Disable unsupported feature flag(s) in config/app.yaml: "
                + ", ".join(unsupported_enabled)
                + "."
            )
        else:
            checks.append(
                _health_check(
                    "feature_support",
                    "pass",
                    "no unsupported feature flags are enabled",
                    unsupported_enabled_features=[],
                    features=features,
                )
            )
        checks.append(
            _health_check(
                "live",
                "pass",
                "live flags inspected without opening venue connections",
                live_mode_enabled=bool(getattr(cfg, "live_mode_enabled", False)),
                enable_polymarket_live=bool(getattr(cfg.features, "enable_polymarket_live", False)),
                enable_kalshi_live=bool(getattr(cfg.features, "enable_kalshi_live", False)),
            )
        )
    except Exception as exc:
        fatal = True
        checks.append(_health_check("config", "fail", f"config failed to load: {exc}"))
        checks.append(_health_check("delivery", "warn", "delivery not checked because config did not load"))
        checks.append(_health_check("live", "warn", "live flags not checked because config did not load"))
        checks.append(_health_check("db", "fail", "DB not checked because config did not load"))
        next_actions.append("Run 'pmfi status' after fixing config/app.yaml or DATABASE_URL.")
        result = {
            "ok": False,
            "status": "blocked",
            "checks": checks,
            "next_actions": next_actions,
        }
        _print_health_json(result) if fmt == "json" else _print_health_table(result)
        return 1

    async def _query_health() -> tuple[dict | None, str | None]:
        try:
            from pmfi.db import close_pool, create_pool
            from pmfi.db import verify as db_verify
        except Exception as exc:
            return None, str(exc)

        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc)
        try:
            integrity = await db_verify.verify_database_integrity(pool)
            if not integrity.ok:
                return {"db_integrity": integrity}, None
            stats = {
                "markets": await pool.fetchval("SELECT COUNT(*) FROM markets"),
                "watched_markets": await pool.fetchval("SELECT COUNT(*) FROM markets WHERE watched=true"),
                "watched_polymarket": await pool.fetchval(
                    "SELECT COUNT(*) FROM markets WHERE watched=true AND venue_code='polymarket'"
                ),
                "watched_kalshi": await pool.fetchval(
                    "SELECT COUNT(*) FROM markets WHERE watched=true AND venue_code='kalshi'"
                ),
                "raw_events": await pool.fetchval("SELECT COUNT(*) FROM raw_events"),
                "normalized_trades": await pool.fetchval("SELECT COUNT(*) FROM normalized_trades"),
                "dead_letters": await pool.fetchval("SELECT COUNT(*) FROM dead_letters"),
                "data_quality_incidents": await pool.fetchval("SELECT COUNT(*) FROM v_open_data_quality_incidents"),
                "alerts": await pool.fetchval("SELECT COUNT(*) FROM alerts"),
                "market_baselines": await pool.fetchval("SELECT COUNT(*) FROM market_baselines"),
                "market_outcomes": await pool.fetchval("SELECT COUNT(*) FROM market_outcomes"),
                "last_raw_event_at": await pool.fetchval("SELECT MAX(received_at) FROM raw_events"),
                "last_trade_at": await pool.fetchval("SELECT MAX(received_at) FROM normalized_trades"),
                "last_alert_at": await pool.fetchval("SELECT MAX(fired_at) FROM alerts"),
                "last_baseline_at": await pool.fetchval("SELECT MAX(computed_at) FROM market_baselines"),
                "ingestion_connection_count": await pool.fetchval("SELECT COUNT(*) FROM ingestion_connections"),
                "system_heartbeat_count": await pool.fetchval("SELECT COUNT(*) FROM system_heartbeats"),
                "polymarket_incomplete_outcomes": await pool.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT m.market_id
                        FROM markets m
                        LEFT JOIN market_outcomes mo
                          ON mo.market_id=m.market_id
                            AND mo.venue_code='polymarket'
                            AND mo.is_active=true
                            AND mo.venue_outcome_id IS NOT NULL
                            AND mo.venue_outcome_id <> ''
                        WHERE m.watched=true
                          AND m.venue_code='polymarket'
                        GROUP BY m.market_id
                        HAVING COUNT(DISTINCT mo.venue_outcome_id) < 2
                    ) incomplete
                    """
                ),
            }
            missing_rows = await pool.fetch(
                """
                SELECT m.venue_market_id, m.title, COUNT(DISTINCT mo.venue_outcome_id) AS token_count
                FROM markets m
                LEFT JOIN market_outcomes mo
                  ON mo.market_id=m.market_id
                        AND mo.venue_code='polymarket'
                        AND mo.is_active=true
                        AND mo.venue_outcome_id IS NOT NULL
                        AND mo.venue_outcome_id <> ''
                WHERE m.watched=true
                  AND m.venue_code='polymarket'
                GROUP BY m.market_id, m.venue_market_id, m.title
                HAVING COUNT(DISTINCT mo.venue_outcome_id) < 2
                ORDER BY venue_market_id
                LIMIT 5
                """
            )
            stats["polymarket_missing_outcome_examples"] = [
                dict(row) for row in missing_rows
            ]
            stats["data_quality_incident_examples"] = [
                dict(row)
                for row in await pool.fetch(
                    """
                    SELECT
                        incident_id::text AS incident_id,
                        venue_code,
                        market_id::text AS market_id,
                        incident_type,
                        severity,
                        started_at,
                        summary
                    FROM v_open_data_quality_incidents
                    ORDER BY started_at DESC, severity DESC
                    LIMIT 5
                    """
                )
            ]
            stats["ingestion_connections"] = [
                dict(row)
                for row in await pool.fetch(
                    """
                    SELECT
                        connection_id::text AS connection_id,
                        venue_code,
                        source_channel,
                        status,
                        connected_at,
                        disconnected_at,
                        last_message_at,
                        reconnect_count,
                        last_error,
                        updated_at
                    FROM ingestion_connections
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT 5
                    """
                )
            ]
            stats["system_heartbeats"] = [
                dict(row)
                for row in await pool.fetch(
                    """
                    SELECT
                        worker_name,
                        worker_type,
                        status,
                        last_heartbeat_at
                    FROM system_heartbeats
                    ORDER BY last_heartbeat_at DESC, worker_name
                    LIMIT 5
                    """
                )
            ]
            stats["db_integrity"] = integrity
            return stats, None
        except Exception as exc:
            return None, str(exc)
        finally:
            await close_pool(pool)

    stats, db_error = asyncio.run(_query_health())
    if db_error:
        fatal = True
        checks.append(
            _health_check(
                "db",
                "fail",
                f"DB connection or schema query failed: {db_error}",
                database=str(getattr(cfg.database, "url", "")).split("@")[-1],
            )
        )
        checks.append(_health_check("watched_markets", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("polymarket_token_mappings", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("kalshi_tickers", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("raw_events", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("normalized_trades", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("dead_letters", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("data_quality_incidents", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("alerts", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("market_baselines", "warn", "not checked because DB is unavailable"))
        checks.append(_health_check("ingest_runtime", "warn", "not checked because DB is unavailable"))
        next_actions.append("Start local Postgres with 'python scripts\\db_local.py up' and run 'python scripts\\db_local.py verify'.")
    else:
        checks.append(_health_check("db", "pass", "DB connected and read-only schema queries completed"))
        integrity = stats["db_integrity"]
        failed_integrity = [check.as_dict() for check in integrity.checks if check.status != "pass"]
        if integrity.ok:
            checks.append(
                _health_check(
                    "db_integrity",
                    "pass",
                    "DB schema integrity contract passed",
                    checks=len(integrity.checks),
                )
            )
        else:
            fatal = True
            checks.append(
                _health_check(
                    "db_integrity",
                    "fail",
                    "DB schema integrity contract failed",
                    failed_checks=failed_integrity,
                )
            )
            next_actions.append("Run 'pmfi db-verify --format json' for exact schema drift details.")
            for skipped in [
                "watched_markets",
                "polymarket_token_mappings",
                "kalshi_tickers",
                "raw_events",
                "normalized_trades",
                "dead_letters",
                "data_quality_incidents",
                "alerts",
                "market_baselines",
                "ingest_runtime",
            ]:
                checks.append(_health_check(skipped, "warn", "not checked because DB integrity failed"))

            result = {
                "ok": False,
                "status": "blocked",
                "checks": checks,
                "next_actions": next_actions,
            }
            _print_health_json(result) if fmt == "json" else _print_health_table(result)
            return 1

        watched_count = int(stats["watched_markets"] or 0)
        missing_poly = int(stats["polymarket_incomplete_outcomes"] or 0)

        if watched_count:
            checks.append(
                _health_check(
                    "watched_markets",
                    "pass",
                    f"{watched_count} watched market(s) configured",
                    count=watched_count,
                    total_markets=stats["markets"],
                )
            )
        else:
            fatal = True
            checks.append(
                _health_check(
                    "watched_markets",
                    "fail",
                    "no watched markets configured",
                    count=0,
                    total_markets=stats["markets"],
                )
            )
            next_actions.append("Run 'pmfi markets discover' then 'pmfi markets watch <market_id>'.")

        if missing_poly:
            fatal = True
            checks.append(
                _health_check(
                    "polymarket_token_mappings",
                    "fail",
                    f"{missing_poly} watched Polymarket market(s) have fewer than two active token mappings",
                    incomplete_count=missing_poly,
                    examples=stats["polymarket_missing_outcome_examples"],
                )
            )
            next_actions.append("Refresh market discovery so watched Polymarket markets have both active outcome token IDs.")
        else:
            checks.append(
                _health_check(
                    "polymarket_token_mappings",
                    "pass",
                    "watched Polymarket markets have token mappings or none are watched",
                    watched_polymarket=stats["watched_polymarket"],
                    market_outcomes=stats["market_outcomes"],
                )
            )

        checks.append(
            _health_check(
                "kalshi_tickers",
                "pass" if int(stats["watched_kalshi"] or 0) else "warn",
                f"{int(stats['watched_kalshi'] or 0)} watched Kalshi ticker(s)",
                count=stats["watched_kalshi"],
            )
        )

        raw_status = "pass" if int(stats["raw_events"] or 0) else "warn"
        trade_status = "pass" if int(stats["normalized_trades"] or 0) else "warn"
        baseline_status = "pass" if int(stats["market_baselines"] or 0) else "warn"
        alert_status = "pass" if int(stats["alerts"] or 0) else "warn"
        dead_letter_status = "warn" if int(stats["dead_letters"] or 0) else "pass"
        incident_count = int(stats["data_quality_incidents"] or 0)
        incident_status = "warn" if incident_count else "pass"
        latest_connection = _health_row((stats["ingestion_connections"] or [None])[0])
        latest_heartbeat = _health_row((stats["system_heartbeats"] or [None])[0])
        runtime_details = {
            "connection_count": int(stats["ingestion_connection_count"] or 0),
            "heartbeat_count": int(stats["system_heartbeat_count"] or 0),
            "latest_connection": latest_connection,
            "latest_heartbeat": latest_heartbeat,
        }
        runtime_error = (
            latest_connection
            and str(latest_connection.get("status", "")).lower() == "error"
        ) or (
            latest_heartbeat
            and str(latest_heartbeat.get("status", "")).lower() == "error"
        )
        if not latest_connection and not latest_heartbeat:
            checks.append(
                _health_check(
                    "ingest_runtime",
                    "warn",
                    "no ingest runtime state recorded yet",
                    **runtime_details,
                )
            )
        elif runtime_error:
            checks.append(
                _health_check(
                    "ingest_runtime",
                    "warn",
                    "latest ingest runtime status is error",
                    **runtime_details,
                )
            )
        else:
            checks.append(
                _health_check(
                    "ingest_runtime",
                    "pass",
                    "ingest runtime state recorded",
                    **runtime_details,
                )
            )
        checks.append(
            _health_check(
                "raw_events",
                raw_status,
                f"{int(stats['raw_events'] or 0)} raw event(s)",
                count=stats["raw_events"],
                latest=stats["last_raw_event_at"],
            )
        )
        checks.append(
            _health_check(
                "normalized_trades",
                trade_status,
                f"{int(stats['normalized_trades'] or 0)} normalized trade(s)",
                count=stats["normalized_trades"],
                latest=stats["last_trade_at"],
            )
        )
        checks.append(
            _health_check(
                "dead_letters",
                dead_letter_status,
                f"{int(stats['dead_letters'] or 0)} dead letter(s)",
                count=stats["dead_letters"],
            )
        )
        checks.append(
            _health_check(
                "data_quality_incidents",
                incident_status,
                (
                    f"{incident_count} open data-quality incident(s)"
                    if incident_count
                    else "no open data-quality incidents"
                ),
                count=incident_count,
                examples=stats["data_quality_incident_examples"][:5],
            )
        )
        checks.append(
            _health_check(
                "alerts",
                alert_status,
                f"{int(stats['alerts'] or 0)} alert(s)",
                count=stats["alerts"],
                latest=stats["last_alert_at"],
            )
        )
        checks.append(
            _health_check(
                "market_baselines",
                baseline_status,
                f"{int(stats['market_baselines'] or 0)} baseline row(s)",
                count=stats["market_baselines"],
                latest=stats["last_baseline_at"],
            )
        )

    ok = not fatal
    has_warnings = any(check["status"] == "warn" for check in checks)
    result = {
        "ok": ok,
        "status": "ready_with_warnings" if ok and has_warnings else ("ready" if ok else "blocked"),
        "checks": checks,
        "next_actions": next_actions,
    }
    _print_health_json(result) if fmt == "json" else _print_health_table(result)
    return 0 if ok else 1


def cmd_db_verify(args: argparse.Namespace) -> int:
    return cmd_db_verify_integrity(args)


def cmd_db_verify_integrity(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool
    from pmfi.db.verify import verify_database_integrity

    fmt = getattr(args, "format", "table")

    try:
        cfg = load_config()

        async def _check():
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
            try:
                return await verify_database_integrity(pool)
            finally:
                await close_pool(pool)

        result = asyncio.run(_check())
        payload = result.as_dict()
        if not result.ok:
            payload["next_actions"] = [
                "Run 'python scripts\\db_local.py init' if this is a fresh local DB.",
                "Run 'pmfi db-maintenance --create-partitions' to apply startup schema migrations and ensure partition horizon.",
            ]
    except Exception as exc:
        payload = {
            "ok": False,
            "status": "blocked",
            "checks": [
                {
                    "name": "db",
                    "status": "fail",
                    "message": f"DB verification failed: {exc}",
                    "details": {"error": str(exc)},
                }
            ],
            "next_actions": ["Start local Postgres with 'python scripts\\db_local.py up' and retry."],
        }

    if fmt == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_db_verify_table(payload)
    return 0 if payload["ok"] else 1


def _print_db_verify_table(payload: dict) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title=f"PMFI DB Integrity: {payload['status']}")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Message")
        for check in payload["checks"]:
            table.add_row(check["name"], check["status"], check["message"])
        Console().print(table)
    except Exception:
        print(f"PMFI DB Integrity: {payload['status']} (ok={str(payload['ok']).lower()})")
        for check in payload["checks"]:
            print(f"- {check['name']}: {check['status']} - {check['message']}")
    if payload.get("next_actions"):
        print("Next actions:")
        for action in payload["next_actions"]:
            print(f"  - {action}")


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
            from pmfi.normalization import NormalizationError
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
            try:
                engine = AlertEngine(baselines=baselines)
                fixtures = sorted(fixture_dir.glob("*.json"))
                print(f"Streaming {len(fixtures)} fixture(s) (delay={delay}s). Press Ctrl+C to stop.")
                total_alerts = 0
                for path in fixtures:
                    try:
                        raw = load_raw_event(path)
                    except Exception as exc:
                        print(f"\n[{path.name}] load skipped: {exc}")
                        continue
                    print(f"\n[{path.name}] venue={raw.venue_code} market={raw.venue_market_id}")
                    await asyncio.sleep(delay)
                    try:
                        trade = normalize_event(raw)
                    except NormalizationError as exc:
                        print(f"  normalization skipped: {exc}")
                        continue
                    if trade is None:
                        print("  normalization skipped: benign non-trade")
                        continue
                    decisions = engine.evaluate(trade)
                    if decisions:
                        for d in decisions:
                            await deliver_stdout(d, venue_code=trade.venue_code, market_id=trade.venue_market_id)
                            total_alerts += 1
                    else:
                        print("  no alert")
                print(f"\nStream complete: {total_alerts} alert(s) from {len(fixtures)} fixture(s).")
            finally:
                if pool:
                    await close_pool(pool)

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
                if fmt == "json":
                    print(json.dumps(
                        {
                            "ok": False,
                            "error": f"Invalid --since value: {since_raw!r}",
                            "next_actions": ["Use an ISO datetime or relative value like '1h', '24h', or '7d'."],
                        },
                        indent=2,
                        default=_health_json_default,
                    ))
                    return 1
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
                f"SELECT a.alert_id::text AS alert_id, a.fired_at, a.rule_key, a.severity, a.confidence, a.score, "
                f"a.venue_code, a.outcome_key, LEFT(m.title, 60) AS market_title{ev_col} "
                f"FROM alerts a LEFT JOIN markets m ON m.market_id = a.market_id "
                f"{where} ORDER BY a.fired_at DESC LIMIT ${idx}",
                *params,
            )
            return rows, None
        finally:
            await pool.close()

    rows, err = asyncio.run(_query())
    if err:
        if fmt == "json":
            print(json.dumps(
                {
                    "ok": False,
                    "error": err,
                    "next_actions": [
                        "Start local Postgres with 'python scripts\\db_local.py up' and run 'python scripts\\db_local.py verify'."
                    ],
                },
                indent=2,
                default=_health_json_default,
            ))
            return 1
        print(f"DB query failed: {err}\nRun 'pmfi db-verify' to check connectivity.")
        return 1
    if not rows:
        if fmt == "json":
            print("[]")
            return 0
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
        table.add_column("ID", style="magenta", no_wrap=True, min_width=8)
        table.add_column("When", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Rule", style="yellow", min_width=32)
        table.add_column("Sev", style="red", min_width=4)
        table.add_column("Conf", min_width=6)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Outcome", min_width=3)
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
                ev_cell = "\n".join(f"{k}={v}" for k, v in ev.items()) if isinstance(ev, dict) else str(ev)
            title = row["market_title"] or "—"
            cells = [
                str(row["alert_id"])[:8],
                when,
                row["rule_key"],
                row["severity"],
                row["confidence"],
                row["venue_code"],
                row["outcome_key"] or "—",
                str(row["score"])[:6],
                title,
            ]
            if show_evidence:
                cells.append(ev_cell)
            table.add_row(*cells)
        console.print(table)
    except ImportError:
        for row in rows:
            print(
                f"{str(row['alert_id'])[:8]}  {str(row['fired_at'])[5:16]}  "
                f"{row['rule_key']}  {row['severity']}  {row['venue_code']}  {row['outcome_key']}"
            )
    return 0


def _serialize_alert_review_row(row: dict) -> dict:
    data = dict(row)
    for key in (
        "reviewed_at",
        "fired_at",
        "acknowledged_at",
        "resolved_at",
    ):
        value = data.get(key)
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


def _alert_not_found_payload(alert_id: str) -> dict:
    return _json_error_payload(
        f"alert not found: {alert_id}",
        alert_id=alert_id,
        next_actions=["Run 'pmfi alerts list --format json' to find a valid alert_id."],
    )


def cmd_alerts_review(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool
    from pmfi.db.repos.alert_reviews import fetch_alert_context, insert_alert_review

    fmt = getattr(args, "format", "table")
    try:
        alert_id = _parse_alert_uuid(args.alert_id)
        label = _normalize_alert_review_label(args.label)
    except ValueError as exc:
        if fmt == "json":
            print(json.dumps(
                _json_error_payload(str(exc)),
                indent=2,
                default=_json_default,
            ))
            return 1
        print(f"[alerts review] {exc}")
        return 1

    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps(_db_unavailable_payload(exc), indent=2, default=_json_default))
            return 1
        _print_db_unavailable("alerts review", exc)
        return 1

    async def _run():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, None, str(exc), True
        review = None
        alert = None
        err = None
        try:
            alert = await fetch_alert_context(pool, alert_id)
            if alert is None:
                return None, None, None, False
            review = await insert_alert_review(
                pool,
                alert_id=alert_id,
                label=label,
                false_positive_category=getattr(args, "category", None),
                notes=getattr(args, "notes", None),
                reviewed_by=getattr(args, "reviewer", None),
            )
        except Exception as exc:
            err = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if err is None:
                    err = str(exc)
        return review, alert, err, False

    review, alert, err, db_unavailable = asyncio.run(_run())
    if err:
        if db_unavailable:
            if fmt == "json":
                print(json.dumps(_db_unavailable_payload(err), indent=2, default=_json_default))
                return 1
            _print_db_unavailable("alerts review", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps(_json_error_payload(err), indent=2, default=_json_default))
            return 1
        print(f"[alerts review] DB query failed: {err}")
        return 1
    if review is None or alert is None:
        if fmt == "json":
            print(json.dumps(_alert_not_found_payload(alert_id), indent=2, default=_json_default))
            return 1
        print(f"[alerts review] alert not found: {alert_id}")
        print("Run 'pmfi alerts list --format json' to find a valid alert_id.")
        return 1

    payload = {
        "ok": True,
        "review": _serialize_alert_review_row(review),
        "alert": _serialize_alert_review_row(alert),
    }
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_default))
        return 0

    print(
        f"[alerts review] recorded {payload['review']['label']} review "
        f"{payload['review']['review_id']} for alert {alert_id}"
    )
    print(f"rule={payload['alert'].get('rule_key')} severity={payload['alert'].get('severity')} title={payload['alert'].get('title')}")
    return 0


def cmd_alerts_reviews(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool
    from pmfi.db.repos.alert_reviews import list_alert_reviews

    fmt = getattr(args, "format", "table")
    limit = getattr(args, "limit", 20)
    if limit <= 0:
        error = "invalid --limit; expected a positive integer"
        if fmt == "json":
            print(json.dumps(_json_error_payload(error), indent=2, default=_json_default))
            return 1
        print(f"[alerts reviews] {error}")
        return 1
    try:
        alert_id = _parse_alert_uuid(args.alert_id) if getattr(args, "alert_id", None) else None
        label = _normalize_alert_review_label(args.label) if getattr(args, "label", None) else None
    except ValueError as exc:
        if fmt == "json":
            print(json.dumps(_json_error_payload(str(exc)), indent=2, default=_json_default))
            return 1
        print(f"[alerts reviews] {exc}")
        return 1

    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps(_db_unavailable_payload(exc), indent=2, default=_json_default))
            return 1
        _print_db_unavailable("alerts reviews", exc)
        return 1

    async def _run():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc), True
        rows = None
        err = None
        try:
            rows = await list_alert_reviews(pool, limit=limit, alert_id=alert_id, label=label)
        except Exception as exc:
            err = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if err is None:
                    err = str(exc)
        return rows, err, False

    rows, err, db_unavailable = asyncio.run(_run())
    if err:
        if db_unavailable:
            if fmt == "json":
                print(json.dumps(_db_unavailable_payload(err), indent=2, default=_json_default))
                return 1
            _print_db_unavailable("alerts reviews", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps(_json_error_payload(err), indent=2, default=_json_default))
            return 1
        print(f"[alerts reviews] DB query failed: {err}")
        return 1

    reviews = [_serialize_alert_review_row(row) for row in rows or []]
    payload = {"ok": True, "count": len(reviews), "reviews": reviews}
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_default))
        return 0

    if not reviews:
        print("No alert reviews in DB.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(width=160)
        table = Table(title=f"Alert Reviews ({len(reviews)} recent)", show_lines=True)
        table.add_column("Review", style="magenta", no_wrap=True)
        table.add_column("Alert", style="cyan", no_wrap=True)
        table.add_column("Label", style="green")
        table.add_column("Rule", style="yellow")
        table.add_column("Severity", style="red")
        table.add_column("Reviewer")
        table.add_column("Notes")
        for row in reviews:
            table.add_row(
                str(row.get("review_id", ""))[:8],
                str(row.get("alert_id", ""))[:8],
                row.get("label") or "",
                row.get("rule_key") or "",
                row.get("severity") or "",
                row.get("reviewed_by") or "",
                row.get("notes") or "",
            )
        console.print(table)
    except ImportError:
        for row in reviews:
            print(
                f"{str(row.get('review_id', ''))[:8]}  {str(row.get('alert_id', ''))[:8]}  "
                f"{row.get('label')}  {row.get('rule_key')}  {row.get('severity')}"
            )
    return 0


def _serialize_alert_fp_rate_row(row: dict) -> dict:
    bucket = row.get("bucket_start")
    if hasattr(bucket, "isoformat"):
        bucket_value = bucket.isoformat()
    elif bucket is None:
        bucket_value = None
    else:
        bucket_value = str(bucket)
    return {
        "rule_key": row.get("rule_key"),
        "bucket": bucket_value,
        "reviewed_count": int(row.get("reviewed_count") or 0),
        "false_positive_count": int(row.get("false_positive_count") or 0),
        "true_positive_count": int(row.get("true_positive_count") or 0),
        "noise_count": int(row.get("noise_count") or 0),
        "unsure_count": int(row.get("unsure_count") or 0),
        "false_positive_rate": float(row.get("false_positive_rate") or 0),
    }


def cmd_alerts_fp_rate(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool
    from pmfi.db.repos.alert_reviews import summarize_false_positive_rate

    fmt = getattr(args, "format", "table")
    since_raw = getattr(args, "since", "30d")
    bucket = getattr(args, "bucket", "day")
    rule_key = getattr(args, "rule", None)
    limit = getattr(args, "limit", 20)
    if limit <= 0:
        error = "invalid --limit; expected a positive integer"
        if fmt == "json":
            print(json.dumps(_json_error_payload(error), indent=2, default=_json_default))
            return 1
        print(f"[alerts fp-rate] {error}")
        return 1
    try:
        since_dt = _parse_relative_or_iso_since(since_raw)
    except ValueError as exc:
        if fmt == "json":
            print(json.dumps(
                _json_error_payload(str(exc), next_actions=["Use an ISO datetime or relative value like '24h' or '30d'."]),
                indent=2,
                default=_json_default,
            ))
            return 1
        print(f"[alerts fp-rate] {exc}")
        return 1

    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps(_db_unavailable_payload(exc), indent=2, default=_json_default))
            return 1
        _print_db_unavailable("alerts fp-rate", exc)
        return 1

    async def _run():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc), True
        rows = None
        err = None
        try:
            rows = await summarize_false_positive_rate(
                pool,
                since=since_dt,
                bucket=bucket,
                rule_key=rule_key,
                limit=limit,
            )
        except Exception as exc:
            err = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if err is None:
                    err = str(exc)
        return rows, err, False

    rows, err, db_unavailable = asyncio.run(_run())
    if err:
        if db_unavailable:
            if fmt == "json":
                print(json.dumps(_db_unavailable_payload(err), indent=2, default=_json_default))
                return 1
            _print_db_unavailable("alerts fp-rate", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps(_json_error_payload(err), indent=2, default=_json_default))
            return 1
        print(f"[alerts fp-rate] DB query failed: {err}")
        return 1

    summaries = [_serialize_alert_fp_rate_row(row) for row in rows or []]
    payload = {
        "ok": True,
        "since": since_raw,
        "since_start": since_dt.isoformat() if hasattr(since_dt, "isoformat") else str(since_dt),
        "bucket": bucket,
        "rule": rule_key,
        "count": len(summaries),
        "summaries": summaries,
    }
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_json_default))
        return 0

    if not summaries:
        print("No reviewed alerts found for the selected window.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console(width=140)
        table = Table(title=f"False Positive Rate ({since_raw}, bucket={bucket})")
        table.add_column("Rule", style="yellow")
        table.add_column("Bucket", style="cyan")
        table.add_column("Reviewed", justify="right")
        table.add_column("FP", justify="right", style="red")
        table.add_column("TP", justify="right", style="green")
        table.add_column("Noise", justify="right")
        table.add_column("Unsure", justify="right")
        table.add_column("FP Rate", justify="right")
        for row in summaries:
            table.add_row(
                row["rule_key"] or "",
                row["bucket"] or "all",
                str(row["reviewed_count"]),
                str(row["false_positive_count"]),
                str(row["true_positive_count"]),
                str(row["noise_count"]),
                str(row["unsure_count"]),
                f"{row['false_positive_rate']:.3f}",
            )
        console.print(table)
    except ImportError:
        for row in summaries:
            print(
                f"{row['rule_key']} {row['bucket'] or 'all'} "
                f"reviewed={row['reviewed_count']} fp={row['false_positive_count']} "
                f"rate={row['false_positive_rate']:.3f}"
            )
    return 0


def cmd_alerts_serve(args: argparse.Namespace) -> int:
    """Run a local HTTP receiver for alert delivery testing."""
    port = getattr(args, "port", 8765)
    host = getattr(args, "host", "127.0.0.1")
    from pmfi.delivery.server import run_alert_receiver, validate_local_bind_host
    try:
        host = validate_local_bind_host(host)
    except ValueError as exc:
        print(f"[alerts serve] invalid host: {exc}")
        return 1
    try:
        asyncio.run(run_alert_receiver(host=host, port=port))
    except KeyboardInterrupt:
        print("\n[alerts serve] stopped.")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    alerts_cmd = getattr(args, "alerts_cmd", None)
    if alerts_cmd == "serve":
        return cmd_alerts_serve(args)
    if alerts_cmd == "review":
        return cmd_alerts_review(args)
    if alerts_cmd == "reviews":
        return cmd_alerts_reviews(args)
    if alerts_cmd == "fp-rate":
        return cmd_alerts_fp_rate(args)
    # Default: list behavior (alerts_cmd is None or "list")
    return cmd_alerts_list(args)


def cmd_dead_letters(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool

    limit = getattr(args, "limit", 20)
    fmt = getattr(args, "format", "table")
    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, default=_health_json_default))
            return 1
        print(f"Config load failed: {exc}")
        return 1

    async def _query():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc), True
        rows = None
        query_error = None
        try:
            try:
                rows = await pool.fetch(
                    """
                    SELECT
                        dl.created_at,
                        dl.venue_code,
                        dl.raw_event_id,
                        dl.failure_stage,
                        dl.error_class,
                        dl.error_message,
                        dl.source_channel,
                        re.source_event_id,
                        re.venue_market_id,
                        LEFT(dl.payload::text, 120) AS payload_preview
                    FROM dead_letters dl
                    LEFT JOIN LATERAL (
                        SELECT source_event_id, venue_market_id
                        FROM raw_events re
                        WHERE re.raw_event_id = dl.raw_event_id
                        ORDER BY re.received_at DESC
                        LIMIT 1
                    ) re ON true
                    ORDER BY dl.created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            except Exception as exc:
                query_error = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if query_error is None:
                    query_error = str(exc)
        if query_error is not None:
            return None, query_error, False
        return rows, None, False

    def _row_dict(row) -> dict:
        data = dict(row)
        created_at = data.get("created_at")
        if hasattr(created_at, "isoformat"):
            data["created_at"] = created_at.isoformat()
        return {
            "created_at": data.get("created_at"),
            "venue_code": data.get("venue_code"),
            "raw_event_id": data.get("raw_event_id"),
            "source_channel": data.get("source_channel"),
            "source_event_id": data.get("source_event_id"),
            "venue_market_id": data.get("venue_market_id"),
            "failure_stage": data.get("failure_stage"),
            "error_class": data.get("error_class"),
            "error_message": data.get("error_message"),
            "payload_preview": data.get("payload_preview"),
        }

    rows, err, db_unavailable = asyncio.run(_query())
    if err:
        if db_unavailable:
            if fmt == "json":
                _print_json_db_unavailable(err)
                return 1
            _print_db_unavailable("dead-letters", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps({"ok": False, "error": err}, indent=2, default=_health_json_default))
            return 1
        print(f"DB query failed: {err}")
        return 1

    payload = {"ok": True, "count": len(rows or []), "dead_letters": [_row_dict(row) for row in rows or []]}
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_health_json_default))
        return 0

    if not rows:
        print("No dead letters - all events normalized successfully.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console(width=160)
        table = Table(title=f"Dead Letters ({len(rows)} recent)", show_lines=True)
        table.add_column("When", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Raw", style="magenta", min_width=8)
        table.add_column("Source / Market", style="yellow", min_width=18)
        table.add_column("Stage", min_width=14)
        table.add_column("Error", style="red", min_width=20)
        table.add_column("Payload (120 chars)", style="dim")
        for r in rows:
            source_bits = [str(r["source_event_id"] or "")]
            if r["venue_market_id"]:
                source_bits.append(str(r["venue_market_id"]))
            source = " / ".join(bit for bit in source_bits if bit) or "-"
            table.add_row(
                str(r["created_at"])[5:16],
                r["venue_code"],
                str(r["raw_event_id"] or "-"),
                source,
                r["failure_stage"],
                r["error_class"] or r["error_message"] or "-",
                r["payload_preview"] or "-",
            )
        console.print(table)
    except ImportError:
        for r in rows:
            print(
                f"{str(r['created_at'])[5:16]}  {r['venue_code']}  raw={r['raw_event_id']}  "
                f"{r['failure_stage']}  {r['error_class']}"
            )
    return 0


def cmd_data_quality_incidents(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool

    limit = getattr(args, "limit", 20)
    fmt = getattr(args, "format", "table")
    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, default=_health_json_default))
            return 1
        print(f"Config load failed: {exc}")
        return 1

    async def _query():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc), True
        rows = None
        query_error = None
        try:
            try:
                rows = await pool.fetch(
                    """
                    SELECT
                        incident_id::text AS incident_id,
                        venue_code,
                        market_id::text AS market_id,
                        incident_type,
                        severity,
                        status,
                        started_at,
                        ended_at,
                        summary,
                        details
                    FROM v_open_data_quality_incidents
                    ORDER BY started_at DESC, severity DESC
                    LIMIT $1
                    """,
                    limit,
                )
            except Exception as exc:
                query_error = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if query_error is None:
                    query_error = str(exc)
        if query_error is not None:
            return None, query_error, False
        return rows, None, False

    def _row_dict(row) -> dict:
        data = dict(row)
        for key in ("started_at", "ended_at"):
            value = data.get(key)
            if hasattr(value, "isoformat"):
                data[key] = value.isoformat()
        return {
            "incident_id": data.get("incident_id"),
            "venue_code": data.get("venue_code"),
            "market_id": data.get("market_id"),
            "incident_type": data.get("incident_type"),
            "severity": data.get("severity"),
            "status": data.get("status"),
            "started_at": data.get("started_at"),
            "ended_at": data.get("ended_at"),
            "summary": data.get("summary"),
            "details": data.get("details"),
        }

    rows, err, db_unavailable = asyncio.run(_query())
    if err:
        if db_unavailable:
            if fmt == "json":
                _print_json_db_unavailable(err)
                return 1
            _print_db_unavailable("data-quality-incidents", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps({"ok": False, "error": err}, indent=2, default=_health_json_default))
            return 1
        print(f"DB query failed: {err}")
        return 1

    incident_rows = [_row_dict(row) for row in rows or []]
    payload = {"ok": True, "count": len(incident_rows), "data_quality_incidents": incident_rows}
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_health_json_default))
        return 0

    if not incident_rows:
        print("No open data-quality incidents.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console(width=160)
        table = Table(title=f"Open Data-Quality Incidents ({len(incident_rows)} recent)", show_lines=True)
        table.add_column("Started", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Market", style="yellow", min_width=14)
        table.add_column("Type", min_width=18)
        table.add_column("Severity", style="red", min_width=8)
        table.add_column("Status", min_width=8)
        table.add_column("Summary", style="dim")
        for row in incident_rows:
            table.add_row(
                str(row["started_at"] or "-")[5:16],
                str(row["venue_code"] or "-"),
                str(row["market_id"] or "-"),
                str(row["incident_type"] or "-"),
                str(row["severity"] or "-"),
                str(row["status"] or "-"),
                str(row["summary"] or "-"),
            )
        console.print(table)
    except ImportError:
        for row in incident_rows:
            print(
                f"{str(row['started_at'] or '-')[5:16]}  {row['venue_code'] or '-'}  "
                f"{row['market_id'] or '-'}  {row['incident_type'] or '-'}  "
                f"{row['severity'] or '-'}  {row['status'] or '-'}  {row['summary'] or '-'}"
            )
    return 0


def cmd_delivery_failures(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import close_pool, create_pool

    limit = getattr(args, "limit", 20)
    fmt = getattr(args, "format", "table")
    try:
        cfg = load_config()
    except Exception as exc:
        if fmt == "json":
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, default=_health_json_default))
            return 1
        print(f"Config load failed: {exc}")
        return 1

    async def _query():
        try:
            pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
        except Exception as exc:
            return None, str(exc), True
        rows = None
        query_error = None
        try:
            try:
                rows = await pool.fetch(
                    """
                    SELECT
                        ad.delivery_id::text AS delivery_id,
                        ad.alert_id::text AS alert_id,
                        ad.channel,
                        ad.destination,
                        ad.status,
                        ad.attempt_count,
                        ad.last_attempt_at,
                        ad.delivered_at,
                        ad.last_error,
                        ad.created_at,
                        a.rule_key,
                        a.severity,
                        a.confidence,
                        a.venue_code,
                        a.market_id::text AS market_id,
                        m.title AS market_title,
                        a.summary,
                        LEFT(ad.payload::text, 240) AS payload_preview
                    FROM alert_deliveries ad
                    JOIN alerts a ON a.alert_id = ad.alert_id
                    LEFT JOIN markets m ON m.market_id = a.market_id
                    WHERE COALESCE(lower(ad.status), '') NOT IN ('delivered', 'succeeded', 'success')
                    ORDER BY COALESCE(ad.last_attempt_at, ad.created_at) DESC, ad.created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            except Exception as exc:
                query_error = str(exc)
        finally:
            try:
                await close_pool(pool)
            except Exception as exc:
                if query_error is None:
                    query_error = str(exc)
        if query_error is not None:
            return None, query_error, False
        return rows, None, False

    def _row_dict(row) -> dict:
        data = dict(row)
        for key in ("last_attempt_at", "delivered_at", "created_at"):
            value = data.get(key)
            if hasattr(value, "isoformat"):
                data[key] = value.isoformat()
        return {
            "delivery_id": data.get("delivery_id"),
            "alert_id": data.get("alert_id"),
            "channel": data.get("channel"),
            "destination": data.get("destination"),
            "status": data.get("status"),
            "attempt_count": data.get("attempt_count"),
            "last_attempt_at": data.get("last_attempt_at"),
            "delivered_at": data.get("delivered_at"),
            "last_error": data.get("last_error"),
            "created_at": data.get("created_at"),
            "rule_key": data.get("rule_key"),
            "severity": data.get("severity"),
            "confidence": data.get("confidence"),
            "venue_code": data.get("venue_code"),
            "market_id": data.get("market_id"),
            "market_title": data.get("market_title"),
            "summary": data.get("summary"),
            "payload_preview": data.get("payload_preview"),
        }

    rows, err, db_unavailable = asyncio.run(_query())
    if err:
        if db_unavailable:
            if fmt == "json":
                _print_json_db_unavailable(err)
                return 1
            _print_db_unavailable("delivery-failures", Exception(err))
            return 1
        if fmt == "json":
            print(json.dumps({"ok": False, "error": err}, indent=2, default=_health_json_default))
            return 1
        print(f"DB query failed: {err}")
        return 1

    failure_rows = [_row_dict(row) for row in rows or []]
    payload = {"ok": True, "count": len(failure_rows), "delivery_failures": failure_rows}
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=_health_json_default))
        return 0

    if not failure_rows:
        print("No non-delivered alert deliveries.")
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console(width=180)
        table = Table(title=f"Alert Delivery Failures ({len(failure_rows)} recent)", show_lines=True)
        table.add_column("Created", style="cyan", no_wrap=True, min_width=11)
        table.add_column("Status", style="red", min_width=9)
        table.add_column("Attempts", justify="right", min_width=8)
        table.add_column("Channel", style="green", min_width=10)
        table.add_column("Destination", min_width=14)
        table.add_column("Rule", style="magenta", min_width=16)
        table.add_column("Severity", min_width=8)
        table.add_column("Venue / Market", style="yellow", min_width=18)
        table.add_column("Error / Summary", style="dim")
        for row in failure_rows:
            market_bits = [str(row["venue_code"] or "")]
            if row["market_title"] or row["market_id"]:
                market_bits.append(str(row["market_title"] or row["market_id"]))
            market_context = " / ".join(bit for bit in market_bits if bit) or "-"
            error_summary = row["last_error"] or row["summary"] or "-"
            table.add_row(
                str(row["created_at"] or "-")[5:16],
                str(row["status"] or "-"),
                str(row["attempt_count"] if row["attempt_count"] is not None else "-"),
                str(row["channel"] or "-"),
                str(row["destination"] or "-"),
                str(row["rule_key"] or "-"),
                str(row["severity"] or "-"),
                market_context,
                str(error_summary),
            )
        console.print(table)
    except ImportError:
        for row in failure_rows:
            print(
                f"{str(row['created_at'] or '-')[5:16]}  {row['status'] or '-'}  "
                f"{row['channel'] or '-'}  attempts={row['attempt_count']}  "
                f"{row['rule_key'] or '-'}  {row['last_error'] or row['summary'] or '-'}"
            )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool

    try:
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
                await close_pool(pool)

        result = asyncio.run(_query())
    except Exception as exc:
        _print_db_unavailable("stats", exc)
        return 1
    if isinstance(result, tuple) and result[0] is None:
        _print_db_unavailable("stats", Exception(result[1]))
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
            f"a.venue_code, a.outcome_key, LEFT(m.title, 50) AS market_title "
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
                _print_db_unavailable("watch", exc)
                return 1

            def _build_table(rows, meta):
                table = Table(title=f"Recent Alerts (refresh every {interval}s, limit {limit})", width=160)
                table.add_column("When", style="cyan", no_wrap=True, min_width=11)
                table.add_column("Rule", style="yellow", min_width=32)
                table.add_column("Sev", style="red", min_width=4)
                table.add_column("Conf", min_width=6)
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
            return 0

        return asyncio.run(_run())
    except ImportError:
        print("rich is required for pmfi watch. Run: pip install rich")
        return 1


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
    limit = getattr(args, "limit", 20)
    watched_only = getattr(args, "watched", False)
    search = getattr(args, "search", None)
    venue = getattr(args, "venue", None)
    output_format = getattr(args, "format", "table")

    try:
        cfg = load_config()

        async def _query():
            pool = await create_pool(cfg.database.url)
            try:
                conditions: list[str] = []
                params: list = []
                idx = 1
                if venue:
                    conditions.append(f"m.venue_code=${idx}")
                    params.append(venue)
                    idx += 1
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
                           COUNT(DISTINCT t.trade_id) AS trade_count,
                           COUNT(DISTINCT mo.venue_outcome_id) FILTER (
                             WHERE mo.is_active=true
                               AND mo.venue_outcome_id IS NOT NULL
                               AND mo.venue_outcome_id <> ''
                           ) AS active_outcomes,
                           MAX(t.received_at) AS last_trade_at
                    FROM markets m
                    LEFT JOIN normalized_trades t ON t.market_id = m.market_id
                    LEFT JOIN market_outcomes mo ON mo.market_id = m.market_id
                    {where}
                    GROUP BY m.market_id, m.venue_code, m.venue_market_id, m.title, m.status, m.watched
                    ORDER BY m.watched DESC, active_outcomes DESC, last_trade_at DESC NULLS LAST, m.venue_market_id
                    LIMIT ${idx}
                    """,
                    *params,
                )
                return rows, None
            except Exception as exc:
                return None, str(exc)
            finally:
                await close_pool(pool)

        rows, err = asyncio.run(_query())
    except Exception as exc:
        if output_format == "json":
            _print_markets_list_json_db_unavailable(exc)
        else:
            _print_db_unavailable("markets", exc)
        return 1
    if err:
        if output_format == "json":
            _print_markets_list_json_db_unavailable(Exception(err))
        else:
            _print_db_unavailable("markets", Exception(err))
        return 1
    if not rows:
        if output_format == "json":
            print(json.dumps({"ok": True, "count": 0, "markets": []}, indent=2))
        elif watched_only:
            print("No watched markets. Use 'pmfi markets list' to see all markets, then 'pmfi markets watch <market_id>'.")
        else:
            print("No markets in DB. Run 'pmfi replay --persist' or 'pmfi markets discover' to populate.")
        return 0

    if output_format == "json":
        payload = {
            "ok": True,
            "count": len(rows),
            "markets": [
                {
                    "venue_code": row["venue_code"],
                    "venue_market_id": row["venue_market_id"],
                    "title": row["title"],
                    "status": row["status"] or "active",
                    "watched": bool(row["watched"]),
                    "trade_count": int(row["trade_count"] or 0),
                    "active_outcomes": int(row["active_outcomes"] or 0),
                    "last_trade_at": row["last_trade_at"].isoformat() if row["last_trade_at"] else None,
                }
                for row in rows
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        tbl_title = f"Watched Markets ({len(rows)})" if watched_only else f"Markets ({len(rows)})"
        table = Table(title=tbl_title, width=160)
        table.add_column("Venue", style="green", min_width=10)
        table.add_column("Market ID", style="magenta", min_width=18, overflow="fold")
        table.add_column("Question / Title", style="cyan", min_width=40)
        table.add_column("Status", min_width=6)
        table.add_column("W", min_width=1)
        table.add_column("Tokens", justify="right", style="blue", min_width=6)
        table.add_column("Trades", justify="right", style="yellow", min_width=5)
        table.add_column("Last Trade", style="dim", min_width=10, no_wrap=True)
        for r in rows:
            w = "[green]y[/green]" if r["watched"] else "n"
            display_title = (r.get("title") or r["venue_market_id"])[:80]
            table.add_row(
                r["venue_code"], r["venue_market_id"], display_title,
                r["status"] or "active", w,
                str(r["active_outcomes"]),
                str(r["trade_count"]),
                str(r["last_trade_at"])[5:16] if r["last_trade_at"] else "—",
            )
        console.print(table)
    except ImportError:
        for r in rows:
            w = "watched" if r.get("watched") else ""
            print(f"{r['venue_code']}:{r['venue_market_id']} tokens={r['active_outcomes']} {w}")
    return 0


def _cmd_markets_discover(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool
    cfg = load_config()
    limit = getattr(args, "limit", 100)
    min_volume = getattr(args, "min_volume", None)
    venue = getattr(args, "venue", "polymarket")

    class _MarketsDiscoverDbUnavailable(RuntimeError):
        pass

    async def _run():
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            raise _MarketsDiscoverDbUnavailable(str(exc)) from exc
        try:
            venue_label = "Kalshi" if venue == "kalshi" else "Polymarket"
            print(f"Fetching up to {limit} active {venue_label} markets...")
            if venue == "kalshi":
                from pmfi.markets import sync_kalshi_markets
                return await sync_kalshi_markets(pool, limit=limit, min_volume=min_volume)
            else:
                from pmfi.markets import sync_polymarket_markets
                return await sync_polymarket_markets(pool, limit=limit, min_volume=min_volume)
        finally:
            await close_pool(pool)

    try:
        count = asyncio.run(_run())
        print(f"Synced {count} market(s) to DB. Run 'pmfi markets list' to review.")
    except _MarketsDiscoverDbUnavailable as exc:
        _print_db_unavailable("markets discover", exc)
        return 1
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
    venue_market_id = args.market_id
    venue = getattr(args, "venue", "polymarket")

    try:
        cfg = load_config()

        async def _run():
            pool = await create_pool(cfg.database.url)
            try:
                async with pool.acquire() as conn:
                    found = await set_market_watched(conn, venue_code=venue, venue_market_id=venue_market_id, watched=watched)
                return found
            finally:
                await close_pool(pool)

        found = asyncio.run(_run())
    except Exception as exc:
        _print_db_unavailable("markets", exc)
        return 1
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
    from pmfi.db.migrations import apply_schema_migrations, ensure_current_partitions, drop_old_partitions

    do_create = getattr(args, "create_partitions", False)
    do_prune = getattr(args, "prune_old_partitions", False)
    months_ahead = getattr(args, "months_ahead", 3)

    if not do_create and not do_prune:
        print("Specify --create-partitions and/or --prune-old-partitions")
        return 1

    try:
        cfg = load_config()
        before_days = getattr(args, "before_days", cfg.ingestion.raw_retention_days)

        async def _run():
            pool = await create_pool(cfg.database.url)
            try:
                if do_create:
                    await apply_schema_migrations(pool)
                    await ensure_current_partitions(pool, months_ahead=months_ahead)
                    print(f"Schema migrations and partitions created/verified for current + {months_ahead} months ahead.")
                if do_prune:
                    dropped = await drop_old_partitions(pool, before_days=before_days)
                    if dropped:
                        print(f"Dropped {len(dropped)} old partition(s): {', '.join(dropped)}")
                    else:
                        print(f"No partitions older than {before_days} days found.")
            finally:
                await close_pool(pool)

        asyncio.run(_run())
    except Exception as exc:
        _print_db_unavailable("db-maintenance", exc)
        return 1
    return 0


def _report_summary_payload(summary, *, source: str) -> dict:
    return {
        "ok": True,
        "source": source,
        "generated_at": summary.generated_at,
        "fixture_count": summary.fixture_count,
        "trade_count": summary.trade_count,
        "alert_count": summary.alert_count,
        "alerts_by_rule": dict(sorted(summary.alerts_by_rule.items())),
        "alerts_by_venue": dict(sorted(summary.alerts_by_venue.items())),
        "alerts_by_severity": dict(sorted(summary.alerts_by_severity.items())),
        "alerts_by_confidence": dict(sorted(summary.alerts_by_confidence.items())),
        "cluster_events": summary.cluster_events,
    }


def _print_report_error(message: str, *, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps({"ok": False, "error": message}, indent=2, sort_keys=True))
    else:
        print(f"[report] {message}", file=sys.stderr)


def _print_report_db_unavailable(exc: Exception, *, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(
            {
                "ok": False,
                "source": "db",
                "error": f"DB unavailable: {exc}",
                "next_actions": [
                    "Start local Postgres with 'python scripts\\db_local.py up'.",
                    "Verify local Postgres with 'python scripts\\db_local.py verify'.",
                    "Seed report data with 'pmfi replay --persist'.",
                ],
            },
            indent=2,
            sort_keys=True,
        ))
        return

    print(f"[report] DB unavailable: {exc}")
    print("  Run 'python scripts/db_local.py up' and 'pmfi replay --persist' first.")


def _cmd_fixture_report(args: argparse.Namespace, *, fmt: str) -> int:
    fixture_dir = Path(getattr(args, "fixture_dir", None) or ROOT / "tests" / "fixtures" / "raw")
    if not fixture_dir.exists() or not fixture_dir.is_dir():
        _print_report_error(f"fixture directory not found: {fixture_dir}", fmt=fmt)
        return 1

    from pmfi.replay import replay_fixtures
    from pmfi.reporting import build_report, write_report

    try:
        results = replay_fixtures(fixture_dir)
        if not results:
            _print_report_error(f"fixture replay produced no normalized trades: {fixture_dir}", fmt=fmt)
            return 1
        summary = build_report(results, title="PMFI Fixture Report")
    except Exception as exc:
        _print_report_error(f"fixture replay failed: {exc}", fmt=fmt)
        return 1

    if fmt == "json":
        print(json.dumps(_report_summary_payload(summary, source="fixtures"), indent=2, sort_keys=True))
        return 0

    output_dir = Path(getattr(args, "output_dir", None) or ROOT / "reports")
    try:
        report_path = write_report(summary, output_dir)
    except Exception as exc:
        print(f"[report] failed to write fixture report: {exc}", file=sys.stderr)
        return 1

    print(f"[report] wrote fixture report: {report_path}")
    print(
        f"[report] fixtures={summary.fixture_count} "
        f"trades={summary.trade_count} alerts={summary.alert_count}"
    )
    return 0


def _review_check(name: str, status: str, message: str, **details) -> dict:
    check = {"name": name, "status": status, "message": message}
    if details:
        check["details"] = details
    return check


def _print_review_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=_health_json_default))


def _print_review_table(payload: dict) -> None:
    print(f"PMFI Review Pass: {payload['status']} (ok={str(payload['ok']).lower()})")
    print(
        f"Source: {payload['source']}  fixtures={payload['fixture_files']}  "
        f"trades={payload['normalized_trades']}  alerts={payload['alerts']}"
    )
    for check in payload["checks"]:
        print(f"{check['status'].upper():5} {check['name']}: {check['message']}")
        for key, value in sorted((check.get("details") or {}).items()):
            print(f"      {key}: {value}")
    if payload.get("next_actions"):
        print("Next actions:")
        for action in payload["next_actions"]:
            print(f"  - {action}")


def _normalization_dead_letter_error_class(error_message: str) -> str:
    """Mirror runner dead-letter classes without importing DB-backed runner code."""
    if "unsupported venue:" in error_message:
        return "unsupported_venue"
    if "normalizer_exception" in error_message:
        return "normalizer_exception"
    if any(key in error_message for key in ("price", "size", "count", "contracts")):
        return "invalid_price_or_size"
    if any(key in error_message for key in ("timestamp", "decimal", "invalid")):
        return "payload_schema_mismatch"
    return "normalization_error"


def _skipped_fixture_identity(path: Path, raw) -> dict:
    return {
        "path": str(path),
        "venue_code": raw.venue_code,
        "source_channel": raw.source_channel,
        "source_event_type": raw.source_event_type,
        "source_event_id": raw.source_event_id,
        "venue_market_id": raw.venue_market_id,
    }


def _classify_skipped_fixture(path: Path) -> dict:
    from pmfi.fixtures import load_raw_event
    from pmfi.normalization import NormalizationError
    from pmfi.pipeline.normalize import normalize_event

    try:
        raw = load_raw_event(path)
    except Exception as exc:
        return {"path": str(path), "classification": "load_error", "error": str(exc)}
    try:
        trade = normalize_event(raw)
    except NormalizationError as exc:
        return {
            **_skipped_fixture_identity(path, raw),
            "classification": "expected_dead_letter",
            "dead_letter_expected": True,
            "dead_letter_stage": "normalization",
            "dead_letter_error_class": _normalization_dead_letter_error_class(str(exc)),
            "no_derived_records_expected": True,
            "raw_event_expected": True,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            **_skipped_fixture_identity(path, raw),
            "classification": "normalizer_exception",
            "dead_letter_expected": True,
            "dead_letter_stage": "normalization",
            "dead_letter_error_class": "normalizer_exception",
            "no_derived_records_expected": True,
            "raw_event_expected": True,
            "error": str(exc),
        }
    if trade is None:
        return {
            **_skipped_fixture_identity(path, raw),
            "classification": "benign_non_trade",
            "reason": "benign_non_trade",
            "dead_letter_expected": False,
            "no_derived_records_expected": True,
            "raw_event_expected": True,
        }
    return {
        **_skipped_fixture_identity(path, raw),
        "classification": "unexpected_skip",
        "dead_letter_expected": False,
        "no_derived_records_expected": False,
        "raw_event_expected": True,
    }


def cmd_review_pass(args: argparse.Namespace) -> int:
    """Run a validate-only fixture-backed coherence pass."""
    fmt = getattr(args, "format", "table")
    fixture_dir = Path(getattr(args, "fixture_dir", None) or ROOT / "tests" / "fixtures" / "raw")
    checks: list[dict] = []
    next_actions = [
        r"Run 'python scripts\verify.py' before handoff.",
        r"Run 'python scripts\db_local.py verify' once local Postgres is available.",
    ]

    if not fixture_dir.exists() or not fixture_dir.is_dir():
        checks.append(_review_check("fixture_directory", "fail", f"fixture directory not found: {fixture_dir}"))
        payload = {
            "ok": False,
            "status": "fail",
            "source": "fixtures",
            "fixture_dir": str(fixture_dir),
            "fixture_files": 0,
            "normalized_trades": 0,
            "alerts": 0,
            "checks": checks,
            "next_actions": next_actions,
        }
        _print_review_json(payload) if fmt == "json" else _print_review_table(payload)
        return 1

    from pmfi.replay import replay_fixtures

    fixture_files = sorted(fixture_dir.glob("*.json"))
    try:
        results = replay_fixtures(fixture_dir)
    except Exception as exc:
        checks.append(_review_check("fixture_replay", "fail", f"fixture replay failed: {exc}"))
        payload = {
            "ok": False,
            "status": "fail",
            "source": "fixtures",
            "fixture_dir": str(fixture_dir),
            "fixture_files": len(fixture_files),
            "normalized_trades": 0,
            "alerts": 0,
            "checks": checks,
            "next_actions": next_actions,
        }
        _print_review_json(payload) if fmt == "json" else _print_review_table(payload)
        return 1

    alert_count = sum(len(result.alerts) for result in results)
    failures = False
    warnings = False

    if results:
        checks.append(_review_check("fixture_runtime", "pass", f"{len(results)} normalized fixture trade(s) replayed"))
    else:
        failures = True
        checks.append(_review_check("fixture_runtime", "fail", "fixture replay produced no normalized trades"))

    if alert_count:
        checks.append(_review_check("alert_runtime", "pass", f"{alert_count} alert decision(s) emitted"))
    else:
        failures = True
        checks.append(_review_check("alert_runtime", "fail", "fixture replay emitted no alert decisions"))

    normalized_paths = {Path(result.fixture_path).resolve() for result in results}
    skipped_files = [path for path in fixture_files if path.resolve() not in normalized_paths]
    if skipped_files:
        skipped_classifications = [_classify_skipped_fixture(path) for path in skipped_files]
        expected_skips = [
            item
            for item in skipped_classifications
            if item["classification"] in {"expected_dead_letter", "benign_non_trade"}
        ]
        unexpected_skips = [
            item
            for item in skipped_classifications
            if item["classification"] not in {"expected_dead_letter", "benign_non_trade"}
        ]
        if unexpected_skips:
            failures = True
            checks.append(
                _review_check(
                    "fixture_skips",
                    "fail",
                    f"{len(unexpected_skips)} skipped fixture file(s) were not classified as expected malformed/non-trade evidence",
                    unexpected=unexpected_skips[:5],
                    expected=expected_skips[:5],
                    expected_count=len(expected_skips),
                    unexpected_count=len(unexpected_skips),
                    fixture_files=len(fixture_files),
                    normalized_trades=len(results),
                )
            )
        else:
            checks.append(
                _review_check(
                    "fixture_skips",
                    "pass",
                    f"{len(expected_skips)} skipped fixture file(s) classified as expected malformed/non-trade evidence",
                    expected=expected_skips[:5],
                    expected_dead_letter_count=sum(1 for item in expected_skips if item["classification"] == "expected_dead_letter"),
                    benign_non_trade_count=sum(1 for item in expected_skips if item["classification"] == "benign_non_trade"),
                    fixture_files=len(fixture_files),
                    normalized_trades=len(results),
                )
            )
    else:
        checks.append(_review_check("fixture_skips", "pass", "all fixture files produced normalized trades"))

    missing_payload = [
        result.fixture_path
        for result in results
        if not getattr(result.trade, "source_payload", None)
    ][:5]
    if missing_payload:
        failures = True
        checks.append(
            _review_check(
                "raw_to_normalized_evidence",
                "fail",
                "normalized trade(s) missing source payload evidence",
                examples=missing_payload,
            )
        )
    else:
        checks.append(_review_check("raw_to_normalized_evidence", "pass", "normalized trades retain source payload evidence"))

    missing_fields: dict[str, int] = {}
    missing_examples: list[dict] = []
    unverified_count = 0
    for result in results:
        for decision in result.alerts:
            missing_for_decision = []
            for field_name in ["rule_id", "rule_version", "severity", "confidence", "reason_codes", "evidence", "data_quality"]:
                value = getattr(decision, field_name)
                if not value:
                    missing_fields[field_name] = missing_fields.get(field_name, 0) + 1
                    missing_for_decision.append(field_name)
            if getattr(decision, "data_quality", None) == "unverified":
                unverified_count += 1
            if missing_for_decision and len(missing_examples) < 5:
                missing_examples.append(
                    {
                        "fixture": result.fixture_path,
                        "rule_id": getattr(decision, "rule_id", None),
                        "missing": missing_for_decision,
                    }
                )

    if missing_fields:
        failures = True
        checks.append(
            _review_check(
                "alert_required_fields",
                "fail",
                "alert decision(s) are missing required explainability fields",
                missing_fields=missing_fields,
                examples=missing_examples,
            )
        )
    else:
        checks.append(_review_check("alert_required_fields", "pass", "alerts include rule, severity, confidence, reasons, evidence, and data quality"))

    if unverified_count:
        warnings = True
        checks.append(
            _review_check(
                "alert_data_quality",
                "warn",
                f"{unverified_count} alert decision(s) use data_quality='unverified'",
                count=unverified_count,
            )
        )
    else:
        checks.append(_review_check("alert_data_quality", "pass", "all alert decisions use specific data-quality statuses"))

    checks.append(_review_check("local_only", "pass", "fixture review ran without DB or live venue calls"))

    ok = not failures
    status = "pass_with_warnings" if ok and warnings else ("pass" if ok else "fail")
    payload = {
        "ok": ok,
        "status": status,
        "source": "fixtures",
        "fixture_dir": str(fixture_dir),
        "fixture_files": len(fixture_files),
        "normalized_trades": len(results),
        "alerts": alert_count,
        "checks": checks,
        "next_actions": next_actions,
    }
    _print_review_json(payload) if fmt == "json" else _print_review_table(payload)
    return 0 if ok else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Generate a summary report of recent alert activity."""
    import re
    from datetime import datetime, timezone, timedelta

    fmt = getattr(args, "format", "table")
    source = getattr(args, "source", "db")
    if source == "fixtures":
        return _cmd_fixture_report(args, fmt=fmt)

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

    async def _run():
        from pmfi.config import load_config
        from pmfi.db import create_pool
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
        _print_report_db_unavailable(exc, fmt=fmt)
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
            print(f"  {ts}  [{r['severity']:<6}] {r['rule_key']:<30} {r['title'][:40]}")

    # DB context
    if "raw_events" in summary:
        print(f"\nDB totals: raw_events={summary['raw_events']}  trades={summary.get('normalized_trades', '?')}  dead_letters={summary.get('dead_letters', '?')}")

    print()
    return 0


def _cmd_baselines_compute(args: argparse.Namespace) -> int:
    """Compute baselines from DB trades and optionally save to JSON."""
    # NOTE: This command uses normalized_trades.capital_at_risk_usd (per-trade percentiles)
    # which is more accurate than the older 'pmfi baseline compute' path that uses
    # metric_windows.max_trade_capital_at_risk_usd window aggregates. Prefer this command.
    # The AlertEngine loads baselines from config/baselines.json when present.
    import asyncio
    import json as _json
    from pmfi.config import load_config

    cfg = load_config()
    days = getattr(args, "days", 30)
    min_samples = getattr(args, "min_samples", 10)
    save = getattr(args, "save", False)

    class _PoolCreationFailed(Exception):
        pass

    async def _run():
        from pmfi.db import create_pool, close_pool
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            raise _PoolCreationFailed(str(exc)) from exc
        try:
            from pmfi.db.repos.metrics import compute_baselines
            async with pool.acquire() as conn:
                baselines = await compute_baselines(conn, window_days=days, min_samples=min_samples)
        finally:
            await close_pool(pool)
        return baselines

    try:
        baselines = asyncio.run(_run())
    except _PoolCreationFailed as exc:
        _print_db_unavailable("baselines compute", exc)
        return 1
    except Exception as exc:
        print(f"[baselines compute] Failed: {exc}")
        return 1

    if not baselines:
        print(f"[baselines compute] No markets with >= {min_samples} trades in last {days} days.")
        print("  Run 'pmfi replay --persist' first to populate normalized_trades.")
        return 0

    print(f"[baselines compute] Computed baselines for {len(baselines)} market(s):")
    for key, vals in sorted(baselines.items())[:20]:
        print(f"  {key}: p99=${vals['p99_trade_usd']:.0f} p99.5=${vals['p995_trade_usd']:.0f} n={vals['sample_size']}")
    if len(baselines) > 20:
        print(f"  ... and {len(baselines) - 20} more")

    if save:
        out_path = ROOT / "config" / "baselines.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_json.dumps(baselines, indent=2), encoding="utf-8")
        print(f"[baselines compute] Saved to {out_path}")
        print("  The alert engine will load this on next 'pmfi replay' or 'pmfi live-smoke'.")

    return 0


def _cmd_baselines_show(args: argparse.Namespace) -> int:
    """Show currently saved baselines from config/baselines.json."""
    import json as _json
    path = ROOT / "config" / "baselines.json"
    if not path.exists():
        print("[baselines show] No baselines file found at config/baselines.json")
        print("  Run 'pmfi baselines compute --save' to generate one.")
        return 1
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[baselines show] Failed to read baselines: {exc}")
        return 1
    print(f"[baselines show] {len(data)} market baseline(s):")
    for key, vals in sorted(data.items()):
        print(f"  {key}: p99=${vals.get('p99_trade_usd', 0):.0f}  p99.5=${vals.get('p995_trade_usd', 0):.0f}  n={vals.get('sample_size', 0)}")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from pmfi.config import load_config
    from pmfi.db import create_pool, close_pool

    if args.baseline_cmd == "compute":
        lookback = getattr(args, "lookback_days", 7) * 86400
        min_samples = getattr(args, "min_samples", 2)
        if min_samples < 1:
            print("--min-samples must be at least 1")
            return 1

        try:
            cfg = load_config()

            async def _compute():
                from pmfi.baseline import compute_market_baselines
                from pmfi.db.migrations import apply_schema_migrations
                pool = await create_pool(cfg.database.url)
                try:
                    await apply_schema_migrations(pool)
                    results = await compute_market_baselines(
                        pool,
                        lookback_seconds=lookback,
                        min_samples=min_samples,
                    )
                    return results
                finally:
                    await close_pool(pool)

            results = asyncio.run(_compute())
        except Exception as exc:
            _print_db_unavailable("baseline", exc)
            return 1
        if not results:
            print(f"No baseline data computed. Need at least {min_samples} normalized trade(s) per market.")
            print("Run 'pmfi replay --persist' first to populate normalized_trades.")
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
        try:
            cfg = load_config()

            async def _list():
                from pmfi.baseline import load_baselines
                pool = await create_pool(cfg.database.url)
                try:
                    return await load_baselines(pool)
                finally:
                    await close_pool(pool)

            baselines = asyncio.run(_list())
        except Exception as exc:
            _print_db_unavailable("baseline", exc)
            return 1
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


async def _load_live_smoke_polymarket_asset_ids(database_url: str) -> list[str]:
    from pmfi.db import close_pool, create_pool

    pool = await create_pool(database_url)
    try:
        rows = await pool.fetch(
            """
            SELECT DISTINCT mo.venue_outcome_id
            FROM market_outcomes mo
            JOIN markets m ON m.market_id = mo.market_id
            WHERE m.watched=true
              AND m.venue_code='polymarket'
              AND mo.venue_code='polymarket'
              AND mo.is_active=true
              AND mo.venue_outcome_id IS NOT NULL
              AND mo.venue_outcome_id <> ''
            ORDER BY mo.venue_outcome_id
            """
        )
        return [str(row["venue_outcome_id"]) for row in rows]
    finally:
        await close_pool(pool)


async def _load_live_smoke_kalshi_tickers(database_url: str) -> list[str]:
    from pmfi.db import close_pool, create_pool

    pool = await create_pool(database_url)
    try:
        rows = await pool.fetch(
            """
            SELECT venue_market_id
            FROM markets
            WHERE watched=true
              AND venue_code='kalshi'
              AND venue_market_id IS NOT NULL
              AND venue_market_id <> ''
            ORDER BY venue_market_id
            """
        )
        return [str(row["venue_market_id"]) for row in rows]
    finally:
        await close_pool(pool)


def _resolve_live_smoke_fixture_source(source: str) -> list[Path]:
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    source_path = source_path.resolve()
    if source_path.is_file():
        return [source_path]
    if source_path.is_dir():
        return sorted(source_path.glob("*.json"))
    raise FileNotFoundError(f"fixture source not found: {source_path}")


def _live_adapter_diagnostics(adapter: object | None) -> dict[str, object]:
    if adapter is None:
        return {}
    diagnostics = getattr(adapter, "diagnostics", None)
    if not callable(diagnostics):
        return {}
    try:
        payload = diagnostics()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _print_pipeline_stats_summary(label: str, stats) -> None:  # noqa: ANN001
    print(
        f"[{label}] persisted summary: "
        f"raw_events_seen={stats.raw_events_seen} "
        f"raw_events_inserted={stats.raw_events_inserted} "
        f"raw_event_duplicates={stats.raw_event_duplicates} "
        f"normalized_trades_inserted={stats.normalized_trades_inserted} "
        f"duplicate_trades={stats.duplicate_trades} "
        f"non_trade_skips={stats.non_trade_skips} "
        f"dead_letters_inserted={stats.dead_letters_inserted} "
        f"alerts_inserted={stats.alerts_inserted} "
        f"alerts_delivered={stats.alerts_delivered} "
        f"alerts_suppressed={stats.alerts_suppressed} "
        f"processing_errors={stats.processing_errors}"
    )


def cmd_live_smoke(args: argparse.Namespace) -> int:
    """Bounded opt-in live smoke: connect to venue WS, capture N events in T seconds.

    Requires PMFI_ENABLE_LIVE=1 env var or --force.
    """
    enable_live = os.environ.get("PMFI_ENABLE_LIVE") == "1"
    force = getattr(args, "force", False)
    fixture_source_raw = getattr(args, "fixture_source", None)
    fixture_paths: list[Path] = []
    if fixture_source_raw:
        try:
            fixture_paths = _resolve_live_smoke_fixture_source(str(fixture_source_raw))
        except Exception as exc:
            print(f"[live-smoke] invalid --fixture-source: {exc}")
            return 1
        if not fixture_paths:
            print(f"[live-smoke] invalid --fixture-source: no JSON fixture files in {fixture_source_raw}")
            return 1

    if not enable_live and not force and not fixture_paths:
        print("Live smoke requires: $env:PMFI_ENABLE_LIVE = '1'")
        print("Or use --force to skip the safety gate.")
        print("Example: $env:PMFI_ENABLE_LIVE = '1'; python -m pmfi.cli live-smoke --venue polymarket --max-events 50 --max-seconds 120 --save-fixtures --persist-raw")
        return 1

    from pmfi.config import load_config
    from pmfi.delivery.stdout import deliver_stdout

    cfg = load_config()
    venue = getattr(args, "venue", "polymarket")
    max_events = getattr(args, "max_events", 50)
    max_seconds = getattr(args, "max_seconds", 120)
    save_fixtures = getattr(args, "save_fixtures", False)
    persist_raw = getattr(args, "persist_raw", False)

    raw_asset_ids = getattr(args, "asset_ids", None) or ""
    asset_ids = [a.strip() for a in raw_asset_ids.split(",") if a.strip()] if raw_asset_ids else []
    raw_tickers = getattr(args, "tickers", None) or ""
    tickers = [t.strip() for t in raw_tickers.split(",") if t.strip()] if raw_tickers else []

    if not fixture_paths and not asset_ids and venue == "polymarket":
        try:
            asset_ids = asyncio.run(_load_live_smoke_polymarket_asset_ids(cfg.database.url))
        except Exception as exc:
            _print_db_unavailable("live-smoke", exc)
            return 1

    if not fixture_paths and not tickers and venue == "kalshi":
        try:
            tickers = asyncio.run(_load_live_smoke_kalshi_tickers(cfg.database.url))
        except Exception as exc:
            _print_db_unavailable("live-smoke", exc)
            return 1

    if fixture_paths:
        subscription_desc = f"fixture_source={len(fixture_paths)} file(s)"
    elif venue == "polymarket":
        subscription_desc = f"asset_ids={asset_ids[:3]}{'...' if len(asset_ids) > 3 else ''}" if asset_ids else "no asset_ids"
    else:
        subscription_desc = f"tickers={tickers[:3]}{'...' if len(tickers) > 3 else ''}" if tickers else "no tickers"
    venue_label = "fixture" if fixture_paths else venue
    print(f"[live-smoke] venue={venue_label} max_events={max_events} max_seconds={max_seconds}")
    print(f"[live-smoke] subscription: {subscription_desc}")
    if not fixture_paths and not asset_ids and venue == "polymarket":
        print("[live-smoke] ERROR: Polymarket live smoke requires asset IDs (token IDs) to subscribe.")
        print("  Without asset IDs, PolymarketAdapter connects but receives no trade events.")
        print("  Options:")
        print("    1. Run 'pmfi markets discover' then 'pmfi markets watch <id>' to populate the DB")
        print("    2. Pass --asset-ids <token_id1,token_id2,...> directly")
        return 1
    if not fixture_paths and not tickers and venue == "kalshi":
        print("[live-smoke] ERROR: Kalshi live smoke requires market tickers to subscribe.")
        print("  Options:")
        print("    1. Run 'pmfi markets discover --venue kalshi' then 'pmfi markets watch <ticker> --venue kalshi'")
        print("    2. Pass --tickers <ticker1,ticker2,...> directly")
        return 1

    captured_events: list = []
    adapter_diagnostics: dict[str, object] = {}
    pipeline_stats = None

    class _LiveSmokeDbUnavailable(Exception):
        def __init__(self, exc: Exception) -> None:
            super().__init__(str(exc))
            self.exc = exc

    async def _run() -> int:
        nonlocal adapter_diagnostics, pipeline_stats
        pool = None
        engine = None
        adapter = None

        if persist_raw:
            from pmfi.db import create_pool

            try:
                pool = await create_pool(cfg.database.url)
            except Exception as exc:
                raise _LiveSmokeDbUnavailable(exc) from exc

            from pmfi.db.migrations import ensure_current_partitions
            from pmfi.pipeline.engine import AlertEngine
            from pmfi.pipeline.runner import PipelineStats, run_adapter_pipeline
            from pmfi.baseline import load_baselines

            pipeline_stats = PipelineStats()
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
            if not fixture_paths and venue == "polymarket":
                from pmfi.adapters.polymarket import PolymarketAdapter

                adapter = PolymarketAdapter(
                    asset_ids=asset_ids,
                    timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                )
            elif not fixture_paths:
                from pmfi.adapters.kalshi import KalshiAdapter

                adapter = KalshiAdapter(
                    tickers=tickers,
                    api_key_id=os.environ.get("KALSHI_API_KEY"),
                    timeout_seconds=cfg.ingestion.live_api_timeout_seconds,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                )

            async def _fixture_events():
                from pmfi.fixtures import load_raw_event

                for path in fixture_paths:
                    yield load_raw_event(path)

            # Intercept events to capture them for fixtures, then yield on.
            async def _capturing_events(source):
                async for raw in source:
                    captured_events.append(raw)
                    event_type = raw.source_event_type or "?"
                    market = (raw.venue_market_id or "?")[:40]
                    print(f"  [#{len(captured_events)}] type={event_type} market={market}")
                    yield raw

            events_source = _capturing_events(_fixture_events() if fixture_paths else adapter.events())

            if persist_raw and pool and engine:
                from pmfi.pipeline.runner import run_adapter_pipeline

                async def _deliver(decision, vc, mid):
                    await deliver_stdout(decision, venue_code=vc, market_id=mid)

                processed = 0
                if fixture_paths:
                    try:
                        processed = await asyncio.wait_for(
                            run_adapter_pipeline(
                                events_source, pool, engine, _deliver,
                                max_events=max_events,
                                suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                                asset_id_map=_live_smoke_asset_id_map,
                                stats=pipeline_stats,
                            ),
                            timeout=max_seconds,
                        )
                    except asyncio.TimeoutError:
                        print(f"[live-smoke] reached max_seconds={max_seconds}")
                        processed = pipeline_stats.raw_events_seen if pipeline_stats else 0
                else:
                    async with adapter:
                        try:
                            processed = await asyncio.wait_for(
                                run_adapter_pipeline(
                                    events_source, pool, engine, _deliver,
                                    max_events=max_events,
                                    suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                                    asset_id_map=_live_smoke_asset_id_map,
                                    stats=pipeline_stats,
                                ),
                                timeout=max_seconds,
                            )
                        except asyncio.TimeoutError:
                            print(f"[live-smoke] reached max_seconds={max_seconds}")
                            processed = pipeline_stats.raw_events_seen if pipeline_stats else 0
                return processed
            else:
                # Capture only — no DB writes
                async def _capture_only():
                    async for _ in events_source:
                        if len(captured_events) >= max_events:
                            break

                if fixture_paths:
                    try:
                        await asyncio.wait_for(_capture_only(), timeout=max_seconds)
                    except asyncio.TimeoutError:
                        print(f"[live-smoke] reached max_seconds={max_seconds}")
                else:
                    await adapter.connect()
                    try:
                        try:
                            await asyncio.wait_for(_capture_only(), timeout=max_seconds)
                        except asyncio.TimeoutError:
                            print(f"[live-smoke] reached max_seconds={max_seconds}")
                    finally:
                        await adapter.disconnect()
                return len(captured_events)

        finally:
            adapter_diagnostics = _live_adapter_diagnostics(adapter)
            if pool:
                from pmfi.db import close_pool
                await close_pool(pool)

    try:
        total = asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[live-smoke] stopped by user.")
        total = len(captured_events)
    except _LiveSmokeDbUnavailable as exc:
        _print_db_unavailable("live-smoke", exc.exc)
        return 1
    except Exception as exc:
        print(f"\n[live-smoke] fatal error: {exc}")
        print("Check the opt-in live gate, subscriptions, DB readiness, and venue connectivity.")
        return 1

    # Save fixtures if requested
    if save_fixtures and captured_events:
        import json as _json
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        fix_dir = ROOT / "tests" / "fixtures" / "live"
        fix_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, raw in enumerate(captured_events):
            path = fix_dir / f"{raw.venue_code}_smoke_{ts}_{i:03d}.json"
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

    if persist_raw and pipeline_stats is not None:
        _print_pipeline_stats_summary("live-smoke", pipeline_stats)

    if not fixture_paths and total == 0 and not captured_events:
        print("[live-smoke] ERROR: no live events were captured; live ingest proof is empty.")
        if adapter_diagnostics:
            print(
                "[live-smoke] adapter diagnostics: "
                f"connect_attempts={adapter_diagnostics.get('connect_attempts', 0)} "
                f"connection_errors={adapter_diagnostics.get('connection_error_count', 0)} "
                f"connected_once={adapter_diagnostics.get('connected_once', False)}"
            )
            last_error = adapter_diagnostics.get("last_connection_error")
            if last_error:
                print(f"[live-smoke] last adapter error: {last_error}")
        print("[live-smoke] Try a more active subscription, increase --max-seconds, or provide required venue credentials.")
        return 1

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

    class _LivePoolUnavailable(RuntimeError):
        pass

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
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            raise _LivePoolUnavailable(str(exc)) from exc

        from pmfi.adapters.polymarket import PolymarketAdapter
        from pmfi.pipeline.engine import AlertEngine
        from pmfi.pipeline.runner import run_adapter_pipeline
        from pmfi.markets import load_asset_id_mapping

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

        engine = AlertEngine(baselines=_baselines)
        asset_id_map = await load_asset_id_mapping(pool)
        print(f"[live] Starting: venue=polymarket watched={len(condition_ids)} asset_ids={len(asset_ids)} baselines={len(_baselines or {})}")
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

    try:
        return asyncio.run(_run())
    except _LivePoolUnavailable as exc:
        _print_db_unavailable("live", exc)
        return 1


def cmd_ingest(args: argparse.Namespace) -> int:
    """Persistent live ingest daemon. Ctrl+C to stop."""
    from pmfi.config import load_config

    venues = getattr(args, "venue", []) or []
    dry_run = getattr(args, "dry_run", False)
    check_only = getattr(args, "check", False)
    max_events = getattr(args, "max_events", None)
    max_seconds = getattr(args, "max_seconds", None)
    fixture_source_raw = getattr(args, "fixture_source", None)
    fixture_paths: list[Path] = []
    if fixture_source_raw:
        try:
            fixture_paths = _resolve_live_smoke_fixture_source(str(fixture_source_raw))
        except Exception as exc:
            print(f"[ingest] invalid --fixture-source: {exc}")
            return 1
        if not fixture_paths:
            print(f"[ingest] invalid --fixture-source: no JSON fixture files in {fixture_source_raw}")
            return 1

    bounded_run = bool(fixture_paths) or max_events is not None or max_seconds is not None
    cfg = load_config()

    if max_events is not None and max_events <= 0:
        print("[ingest] invalid --max-events: value must be greater than zero")
        return 1
    if max_seconds is not None and max_seconds <= 0:
        print("[ingest] invalid --max-seconds: value must be greater than zero")
        return 1

    if fixture_paths and dry_run:
        print("[ingest] --fixture-source cannot be combined with --dry-run because ingest fixture mode persists to DB")
        return 1

    if not venues and not fixture_paths:
        if cfg.features.enable_polymarket_live:
            venues.append("polymarket")
        if cfg.features.enable_kalshi_live:
            venues.append("kalshi")

    if not venues and not fixture_paths:
        print("No live venues enabled. Set enable_polymarket_live=true in config/app.yaml.")
        print("Or pass --venue polymarket --venue kalshi explicitly.")
        return 1

    if check_only:
        from pmfi.db import close_pool, create_pool

        fmt = getattr(args, "format", "table")

        class _IngestReadinessPoolUnavailable(RuntimeError):
            pass

        def _ingest_readiness_db_unavailable_payload(exc: Exception) -> dict:
            return _db_unavailable_payload(
                exc,
                extra={
                    "status": "blocked",
                    "venues": venues,
                    "checks": [
                        {
                            "name": "db_connectivity",
                            "status": "fail",
                            "message": f"DB unavailable: {exc}",
                            "details": {"error": str(exc)},
                        }
                    ],
                },
            )

        async def _check():
            try:
                pool = await create_pool(cfg.database.url, min_size=1, max_size=1)
            except Exception as exc:
                raise _IngestReadinessPoolUnavailable(str(exc)) from exc
            try:
                from pmfi.baseline import load_baselines
                from pmfi.db.repos.markets import fetch_watched_markets
                from pmfi.db.verify import verify_database_integrity
                from pmfi.markets import load_asset_id_mapping

                integrity = await verify_database_integrity(pool)
                baselines = await load_baselines(pool)
                async with pool.acquire() as conn:
                    watched = await fetch_watched_markets(conn)
                asset_id_map = await load_asset_id_mapping(pool)
            finally:
                await close_pool(pool)

            delivery_mode = cfg.alerts.default_delivery
            allowed_delivery_modes = getattr(cfg.alerts, "allowed_delivery_modes", [])
            supported_delivery_modes = {"console", "file", "localhost_http_receiver"}
            watched_for_venues = [m for m in watched if m["venue_code"] in venues]
            watched_poly_market_ids = {m["market_id"] for m in watched_for_venues if m["venue_code"] == "polymarket"}
            poly_asset_ids_by_market = {str(market_id): [] for market_id in watched_poly_market_ids}
            for token_id, info in asset_id_map.items():
                if info["venue_code"] == "polymarket" and info["market_id"] in watched_poly_market_ids:
                    poly_asset_ids_by_market.setdefault(str(info["market_id"]), []).append(str(token_id))
            for asset_ids in poly_asset_ids_by_market.values():
                asset_ids.sort()
            polymarket_markets = [
                {
                    "market_id": str(m["market_id"]),
                    "venue_market_id": m["venue_market_id"],
                    "title": m.get("title"),
                    "status": m.get("status"),
                    "asset_id_count": len(poly_asset_ids_by_market.get(str(m["market_id"]), [])),
                    "asset_ids": poly_asset_ids_by_market.get(str(m["market_id"]), []),
                }
                for m in sorted(
                    (m for m in watched_for_venues if m["venue_code"] == "polymarket"),
                    key=lambda m: (str(m["venue_market_id"]), str(m["market_id"])),
                )
            ]
            poly_ids = [
                asset_id
                for market in polymarket_markets
                for asset_id in market["asset_ids"]
            ]
            kalshi_markets = [
                {
                    "market_id": str(m["market_id"]),
                    "venue_market_id": m["venue_market_id"],
                    "ticker": m["venue_market_id"],
                    "title": m.get("title"),
                    "status": m.get("status"),
                }
                for m in sorted(
                    (m for m in watched_for_venues if m["venue_code"] == "kalshi"),
                    key=lambda m: (str(m["venue_market_id"]), str(m["market_id"])),
                )
            ]
            kalshi_tickers = [m["ticker"] for m in kalshi_markets]

            checks = []

            def add(name: str, status: str, message: str, **details):
                check = {"name": name, "status": status, "message": message}
                if details:
                    check["details"] = details
                checks.append(check)

            add(
                "db_integrity",
                "pass" if integrity.ok else "fail",
                "DB schema integrity contract passed" if integrity.ok else "DB schema integrity failed",
                db_status=integrity.status,
            )
            if delivery_mode not in allowed_delivery_modes:
                add(
                    "delivery",
                    "fail",
                    "alerts.default_delivery is not listed in alerts.allowed_delivery_modes",
                    mode=delivery_mode,
                    allowed_modes=allowed_delivery_modes,
                )
            elif delivery_mode not in supported_delivery_modes:
                add(
                    "delivery",
                    "fail",
                    "alerts.default_delivery is not supported by ingest",
                    mode=delivery_mode,
                    supported_modes=sorted(supported_delivery_modes),
                )
            else:
                add("delivery", "pass", f"delivery mode {delivery_mode!r} is supported", mode=delivery_mode)

            add(
                "baselines",
                "pass" if baselines else "warn",
                f"{len(baselines)} persisted baseline(s) loaded",
                count=len(baselines),
            )
            add(
                "watched_markets",
                "pass" if watched_for_venues else "fail",
                f"{len(watched_for_venues)} watched market(s) for requested venue(s)",
                count=len(watched_for_venues),
                venues=venues,
            )
            if "polymarket" in venues:
                add(
                    "polymarket_subscriptions",
                    "pass" if poly_ids else "fail",
                    f"{len(poly_ids)} Polymarket token subscription id(s)",
                    count=len(poly_ids),
                    watched_markets=len(watched_poly_market_ids),
                )
            if "kalshi" in venues:
                add(
                    "kalshi_subscriptions",
                    "pass" if kalshi_tickers else "fail",
                    f"{len(kalshi_tickers)} Kalshi ticker subscription(s)",
                    count=len(kalshi_tickers),
                )
            add("live_connections", "pass", "readiness check did not import or connect live adapters")

            ok = all(check["status"] != "fail" for check in checks)
            return {
                "ok": ok,
                "status": "ready" if ok else "blocked",
                "venues": venues,
                "checks": checks,
                "subscriptions": {
                    "polymarket_asset_ids": len(poly_ids),
                    "kalshi_tickers": len(kalshi_tickers),
                    "polymarket_markets": polymarket_markets,
                    "kalshi_markets": kalshi_markets,
                },
                "next_actions": [] if ok else [
                    "Run 'pmfi markets discover' and 'pmfi markets watch <market_id>' for missing subscriptions.",
                    "Run 'pmfi db-verify --format json' for schema details if db_integrity failed.",
                ],
            }

        try:
            payload = asyncio.run(_check())
        except _IngestReadinessPoolUnavailable as exc:
            payload = _ingest_readiness_db_unavailable_payload(exc)
        except Exception as exc:
            payload = {
                "ok": False,
                "status": "blocked",
                "venues": venues,
                "checks": [
                    {
                        "name": "ingest_readiness",
                        "status": "fail",
                        "message": f"ingest readiness failed: {exc}",
                        "details": {"error": str(exc)},
                    }
                ],
                "next_actions": ["Start local Postgres and run 'pmfi db-verify --format json'."],
            }

        if fmt == "json":
            print(json.dumps(payload, indent=2, default=str, sort_keys=True))
        else:
            print(f"PMFI ingest readiness: {payload['status']}")
            for check in payload["checks"]:
                print(f"  [{check['status']}] {check['name']}: {check['message']}")
            if payload.get("next_actions"):
                print("Next actions:")
                for action in payload["next_actions"]:
                    print(f"  - {action}")
        return 0 if payload["ok"] else 1

    if dry_run:
        from pmfi.pipeline.normalize import normalize_event
        _events_seen = [0]

        async def _run_dry():
            tasks = []

            if "polymarket" in venues:
                from pmfi.adapters.polymarket import PolymarketAdapter
                adapter = PolymarketAdapter(
                    asset_ids=[],
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
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

            if "kalshi" in venues:
                from pmfi.adapters.kalshi import KalshiAdapter
                kalshi_key = os.environ.get("KALSHI_API_KEY")
                adapter_k = KalshiAdapter(
                    tickers=[],
                    api_key_id=kalshi_key,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
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

            print(f"[dry-run] started {len(tasks)} adapter(s) for venues={venues} — no DB writes. Ctrl+C to stop.")
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
    allowed_delivery_modes = getattr(cfg.alerts, "allowed_delivery_modes", [])
    if delivery_mode not in allowed_delivery_modes:
        print(
            "[ingest] invalid config: alerts.default_delivery must be listed in "
            f"alerts.allowed_delivery_modes (default_delivery={delivery_mode!r}, "
            f"allowed_delivery_modes={allowed_delivery_modes!r})"
        )
        return 1
    supported_delivery_modes = {"console", "file", "localhost_http_receiver"}
    if delivery_mode not in supported_delivery_modes:
        supported = ", ".join(sorted(supported_delivery_modes))
        print(f"[ingest] unsupported alerts.default_delivery {delivery_mode!r}; supported modes: {supported}")
        return 1

    from pmfi.baseline import load_baselines
    from pmfi.db import close_pool, create_pool
    from pmfi.db.migrations import startup_maintenance
    from pmfi.db.repos.ingestion_runtime import (
        record_connection_error,
        record_connection_message,
        record_connection_start,
        record_connection_stop,
        record_heartbeat,
    )
    from pmfi.db.repos.markets import fetch_watched_markets
    from pmfi.delivery.stdout import deliver_stdout
    from pmfi.pipeline.engine import AlertEngine
    from pmfi.pipeline.runner import PipelineStats, run_adapter_pipeline

    pipeline_stats = PipelineStats() if bounded_run else None

    class _IngestPoolUnavailable(RuntimeError):
        pass

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
    elif delivery_mode == "console":
        async def _deliver(decision, venue_code, market_id):
            await deliver_stdout(decision, venue_code=venue_code, market_id=market_id)

    async def _run():
        try:
            pool = await create_pool(cfg.database.url)
        except Exception as exc:
            raise _IngestPoolUnavailable(str(exc)) from exc
        try:
            await startup_maintenance(pool)
            baselines = {}
            try:
                baselines = await load_baselines(pool)
            except Exception:
                pass

            engine = AlertEngine(baselines=baselines)

            _events_seen = [0]
            _alerts_fired = [0]

            async def alert_handler(decision, venue_code, market_id):
                _alerts_fired[0] += 1
                await _deliver(decision, venue_code, market_id)

            def _worker_name(venue_code: str, source_channel: str) -> str:
                return f"pmfi-ingest:{venue_code}:{source_channel}"

            def _runtime_metadata(**extra):
                metadata = {
                    "command": "ingest",
                    "bounded_run": bounded_run,
                    "raw_events_seen": _events_seen[0],
                    "alerts_fired": _alerts_fired[0],
                }
                metadata.update(extra)
                return metadata

            async def _runtime_start(venue_code: str, source_channel: str, **metadata) -> tuple[str, str]:
                worker_name = _worker_name(venue_code, source_channel)
                connection_id = await record_connection_start(
                    pool,
                    venue_code=venue_code,
                    source_channel=source_channel,
                    metadata=_runtime_metadata(**metadata),
                )
                await record_heartbeat(
                    pool,
                    worker_name=worker_name,
                    worker_type="ingest",
                    status="running",
                    metadata=_runtime_metadata(
                        venue_code=venue_code,
                        source_channel=source_channel,
                        **metadata,
                    ),
                )
                return connection_id, worker_name

            async def _runtime_message(connection_id: str, worker_name: str, raw) -> None:
                await record_connection_message(pool, connection_id)
                await record_heartbeat(
                    pool,
                    worker_name=worker_name,
                    worker_type="ingest",
                    status="running",
                    metadata=_runtime_metadata(
                        last_venue_code=raw.venue_code,
                        last_source_channel=raw.source_channel,
                    ),
                )

            async def _runtime_stop(connection_id: str, worker_name: str, reason: str) -> None:
                metadata = _runtime_metadata(reason=reason)
                await record_connection_stop(pool, connection_id, metadata=metadata)
                await record_heartbeat(
                    pool,
                    worker_name=worker_name,
                    worker_type="ingest",
                    status="stopped",
                    metadata=metadata,
                )

            async def _runtime_error(connection_id: str, worker_name: str, exc: BaseException) -> None:
                metadata = _runtime_metadata(error=str(exc))
                await record_connection_error(pool, connection_id, exc, metadata=metadata)
                await record_heartbeat(
                    pool,
                    worker_name=worker_name,
                    worker_type="ingest",
                    status="error",
                    metadata=metadata,
                )

            async def _counted_events(source, connection_id: str | None = None, worker_name: str | None = None):
                async for raw in source:
                    _events_seen[0] += 1
                    if connection_id is not None and worker_name is not None:
                        await _runtime_message(connection_id, worker_name, raw)
                    yield raw

            if fixture_paths:
                from pmfi.fixtures import load_raw_event

                async def _fixture_events():
                    for path in fixture_paths:
                        yield load_raw_event(path)

                venue_desc = ",".join(venues) if venues else "from-fixture"
                print(
                    f"[ingest] fixture_source={len(fixture_paths)} file(s) "
                    f"venue={venue_desc} max_events={max_events} max_seconds={max_seconds}"
                )
                fixture_venue = venues[0] if len(venues) == 1 else load_raw_event(fixture_paths[0]).venue_code
                fixture_connection_id, fixture_worker_name = await _runtime_start(
                    fixture_venue,
                    "fixture_source",
                    fixture_count=len(fixture_paths),
                    max_events=max_events,
                    max_seconds=max_seconds,
                )
                fixture_error = None
                try:
                    pipeline_coro = run_adapter_pipeline(
                        _counted_events(_fixture_events(), fixture_connection_id, fixture_worker_name),
                        pool,
                        engine,
                        alert_handler,
                        max_events=max_events,
                        suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                        capture_orderbook=False,
                        stats=pipeline_stats,
                    )
                    if max_seconds is not None:
                        await asyncio.wait_for(pipeline_coro, timeout=max_seconds)
                    else:
                        await pipeline_coro
                except asyncio.TimeoutError:
                    print(f"[ingest] reached max_seconds={max_seconds}")
                except Exception as exc:
                    fixture_error = exc
                    await _runtime_error(fixture_connection_id, fixture_worker_name, exc)
                    raise
                finally:
                    if fixture_error is None:
                        await _runtime_stop(fixture_connection_id, fixture_worker_name, "completed")
                return pipeline_stats.raw_events_seen if pipeline_stats is not None else _events_seen[0]

            async with pool.acquire() as conn:
                watched = await fetch_watched_markets(conn)

            # Polymarket WS subscriptions require token IDs (asset_ids from market_outcomes),
            # not condition IDs (venue_market_id). Load the token→market mapping and filter to
            # watched markets only; fall back to condition IDs if outcomes not yet synced.
            from pmfi.markets import load_asset_id_mapping
            asset_id_map = await load_asset_id_mapping(pool)
            watched_poly_market_ids = {m["market_id"] for m in watched if m["venue_code"] == "polymarket"}
            poly_ids = [
                token_id for token_id, info in asset_id_map.items()
                if info["venue_code"] == "polymarket" and info["market_id"] in watched_poly_market_ids
            ]
            if not poly_ids:
                poly_ids = [m["venue_market_id"] for m in watched if m["venue_code"] == "polymarket"]
                if poly_ids:
                    print("[ingest] WARNING: no market_outcomes found — using condition IDs. Run 'pmfi markets discover' for accurate token subscriptions.")
            kalshi_tickers = [m["venue_market_id"] for m in watched if m["venue_code"] == "kalshi"]

            if not watched:
                print("No watched markets in DB. Run 'pmfi markets discover' then 'pmfi markets watch <id>'.")
                return 0

            async def _telemetry_loop(interval: int = 60):
                last = 0
                cycle = 0
                baseline_refresh_cycles = 10  # refresh baselines every ~10 min
                while True:
                    await asyncio.sleep(interval)
                    cycle += 1
                    total = _events_seen[0]
                    delta = total - last
                    last = total
                    print(f"[ingest] events_total={total} (+{delta}/{interval}s) alerts_total={_alerts_fired[0]}")
                    if cycle % baseline_refresh_cycles == 0:
                        try:
                            fresh = await load_baselines(pool)
                            engine.update_baselines(fresh)
                            print(f"[ingest] baselines refreshed ({len(fresh)} market(s))")
                        except Exception as _bl_exc:
                            print(f"[ingest] baseline refresh failed (non-fatal): {_bl_exc}")

            tasks = []

            if "polymarket" in venues:
                from pmfi.adapters.polymarket import PolymarketAdapter
                adapter = PolymarketAdapter(
                    asset_ids=poly_ids,
                    initial_backoff=cfg.ingestion.reconnect_initial_backoff,
                    max_backoff=cfg.ingestion.reconnect_max_backoff,
                )
                await adapter.connect()
                try:
                    poly_connection_id, poly_worker_name = await _runtime_start(
                        "polymarket",
                        "websocket",
                        subscription_count=len(poly_ids),
                        max_events=max_events,
                        max_seconds=max_seconds,
                    )
                except Exception:
                    await adapter.disconnect()
                    raise

                async def _run_poly():
                    try:
                        await run_adapter_pipeline(
                            _counted_events(adapter.events(), poly_connection_id, poly_worker_name),
                            pool, engine, alert_handler,
                            max_events=max_events,
                            suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                            capture_orderbook=cfg.features.enable_orderbook_reconstruction,
                            asset_id_map=asset_id_map,
                            stats=pipeline_stats,
                        )
                    except asyncio.CancelledError:
                        await _runtime_stop(poly_connection_id, poly_worker_name, "cancelled")
                        raise
                    except Exception as exc:
                        await _runtime_error(poly_connection_id, poly_worker_name, exc)
                        raise
                    else:
                        await _runtime_stop(poly_connection_id, poly_worker_name, "completed")
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
                try:
                    kalshi_connection_id, kalshi_worker_name = await _runtime_start(
                        "kalshi",
                        "websocket",
                        subscription_count=len(kalshi_tickers),
                        max_events=max_events,
                        max_seconds=max_seconds,
                    )
                except Exception:
                    await adapter_k.disconnect()
                    raise

                async def _run_kalshi():
                    try:
                        await run_adapter_pipeline(
                            _counted_events(adapter_k.events(), kalshi_connection_id, kalshi_worker_name),
                            pool, engine, alert_handler,
                            max_events=max_events,
                            suppression_window_seconds=cfg.alerts.suppression_window_seconds,
                            capture_orderbook=cfg.features.enable_orderbook_reconstruction,
                            stats=pipeline_stats,
                        )
                    except asyncio.CancelledError:
                        await _runtime_stop(kalshi_connection_id, kalshi_worker_name, "cancelled")
                        raise
                    except Exception as exc:
                        await _runtime_error(kalshi_connection_id, kalshi_worker_name, exc)
                        raise
                    else:
                        await _runtime_stop(kalshi_connection_id, kalshi_worker_name, "completed")
                    finally:
                        await adapter_k.disconnect()

                tasks.append(asyncio.create_task(_run_kalshi()))

            poly_sub_count = len(poly_ids) if "polymarket" in venues else 0
            kalshi_sub_count = len(kalshi_tickers) if "kalshi" in venues else 0
            print(
                f"[ingest] started {len(tasks)} adapter(s) for venues={venues}, "
                f"watching {len(watched)} market(s) "
                f"(poly_tokens={poly_sub_count}, kalshi_tickers={kalshi_sub_count}). "
                f"Ctrl+C to stop."
            )
            for _m in watched:
                _title = (_m["title"] or _m["venue_market_id"])[:70]
                print(f"[ingest]   [{_m['venue_code']}] {_title}")
            if tasks:
                if bounded_run:
                    try:
                        if max_seconds is not None:
                            await asyncio.wait_for(asyncio.gather(*tasks), timeout=max_seconds)
                        else:
                            await asyncio.gather(*tasks)
                    except asyncio.TimeoutError:
                        print(f"[ingest] reached max_seconds={max_seconds}")
                    finally:
                        pending = [task for task in tasks if not task.done()]
                        for task in pending:
                            task.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                else:
                    tasks.append(asyncio.create_task(_telemetry_loop()))
                    try:
                        await asyncio.gather(*tasks)
                    except asyncio.CancelledError:
                        pass
            return pipeline_stats.raw_events_seen if pipeline_stats is not None else _events_seen[0]
        finally:
            await close_pool(pool)

    try:
        total = asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n[ingest] stopped.")
        return 0
    except _IngestPoolUnavailable as exc:
        _print_db_unavailable("ingest", exc)
        return 1
    except Exception as exc:
        print(f"[ingest] fatal error: {exc}")
        print("Check DB connectivity with 'pmfi db-verify' and config with 'pmfi status'.")
        return 1
    if bounded_run and pipeline_stats is not None:
        print(f"[ingest] bounded run complete: raw_events_seen={total}")
        _print_pipeline_stats_summary("ingest", pipeline_stats)
        if pipeline_stats.raw_events_seen == 0:
            print("[ingest] ERROR: bounded ingest proof saw zero raw events.")
            print("[ingest] Try a more active subscription, increase --max-seconds, or run 'pmfi ingest --check --format json'.")
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
    p_replay.add_argument("--limit", type=int, default=100, help="Max events when using --from-db (default: 100)")

    p_status = sub.add_parser("status", help="Show current PMFI configuration and status")
    p_status.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_health = sub.add_parser("health", help="Validate operator readiness without writes or live venue calls")
    p_health.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_db_verify = sub.add_parser("db-verify", help="Verify Postgres schema integrity without writes")
    p_db_verify.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_monitor = sub.add_parser("monitor", help="Start live monitoring (requires live mode enabled)")
    p_monitor.add_argument("--fixture-replay", action="store_true", help="Stream fixture events as a live demo")
    p_monitor.add_argument("--fixture-dir", default=None, help="Path to fixture dir (default: tests/fixtures/raw)")
    p_monitor.add_argument("--delay", type=float, default=1.0, help="Seconds between fixture events (default: 1.0)")

    p_alerts = sub.add_parser("alerts", help="Alert commands: list, review, reviews, fp-rate, serve")
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
    p_alerts_review = alerts_sub.add_parser("review", help="Append an operator review for an alert")
    p_alerts_review.add_argument("alert_id", metavar="ALERT_ID")
    p_alerts_review.add_argument("--label", required=True)
    p_alerts_review.add_argument("--category", default=None)
    p_alerts_review.add_argument("--notes", default=None)
    p_alerts_review.add_argument("--reviewer", default=None)
    p_alerts_review.add_argument("--format", choices=["table", "json"], default="table")
    p_alerts_reviews = alerts_sub.add_parser("reviews", help="List recent alert review rows")
    p_alerts_reviews.add_argument("--format", choices=["table", "json"], default="table")
    p_alerts_reviews.add_argument("--limit", type=int, default=20)
    p_alerts_reviews.add_argument("--alert-id", default=None)
    p_alerts_reviews.add_argument("--label", default=None)
    p_alerts_fp_rate = alerts_sub.add_parser("fp-rate", help="Summarize false-positive rate from latest alert reviews")
    p_alerts_fp_rate.add_argument("--format", choices=["table", "json"], default="table")
    p_alerts_fp_rate.add_argument("--since", default="30d", help="ISO datetime or relative window (default: 30d)")
    p_alerts_fp_rate.add_argument("--bucket", choices=["all", "day", "hour"], default="day")
    p_alerts_fp_rate.add_argument("--rule", metavar="RULE_KEY", default=None)
    p_alerts_fp_rate.add_argument("--limit", type=int, default=20)

    p_ingest = sub.add_parser("ingest", help="Persistent live ingest daemon (requires live venue enabled in config)")
    p_ingest.add_argument("--venue", action="append", metavar="VENUE",
                          help="Venue to ingest from: polymarket or kalshi (can repeat). Default: all enabled in config.")
    p_ingest.add_argument("--dry-run", action="store_true", help="Connect and log events but do not persist to DB")
    p_ingest.add_argument("--check", action="store_true", help="Validate ingest readiness without live connections or writes")
    p_ingest.add_argument("--format", choices=["table", "json"], default="table", help="Output format for --check (default: table)")
    p_ingest.add_argument("--max-events", type=int, default=None, help="Stop after N raw events per adapter for a bounded proof run")
    p_ingest.add_argument("--max-seconds", type=float, default=None, help="Stop a bounded proof run after N seconds")
    p_ingest.add_argument("--fixture-source", default=None,
                          help="Local RawEvent fixture file or directory to ingest through the persisted DB pipeline")

    sub.add_parser("stats", help="Show aggregate DB statistics (row counts per table)")

    p_dl = sub.add_parser("dead-letters", help="Show recent normalization failures")
    p_dl.add_argument("--limit", type=int, default=20, help="Number of dead letters to show (default: 20)")
    p_dl.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")

    p_dqi = sub.add_parser("data-quality-incidents", help="Show open data-quality incidents")
    p_dqi.add_argument("--limit", type=int, default=20, help="Number of incidents to show (default: 20)")
    p_dqi.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")

    p_df = sub.add_parser("delivery-failures", help="Show pending or failed alert deliveries")
    p_df.add_argument("--limit", type=int, default=20, help="Number of delivery rows to show (default: 20)")
    p_df.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")

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
    p_markets_list.add_argument("--venue", choices=["polymarket", "kalshi"], default=None, help="Filter by venue code")
    p_markets_list.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
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
    p_report.add_argument("--source", choices=["db", "fixtures"], default="db", help="Report source (default: db)")
    p_report.add_argument("--fixture-dir", default=None, help="Fixture directory for --source fixtures")
    p_report.add_argument("--output-dir", default=None, help="Output directory for fixture table reports")

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
    p_bc = baseline_sub.add_parser("compute", help="Compute persisted market baselines from normalized trades")
    p_bc.add_argument("--lookback-days", type=int, default=7, help="Lookback window in days (default: 7)")
    p_bc.add_argument("--min-samples", type=int, default=2, dest="min_samples", help="Min trades required per market (default: 2)")
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
    p_live_smoke.add_argument("--tickers", type=str, default=None,
                               help="Comma-separated Kalshi market tickers to subscribe to")
    p_live_smoke.add_argument("--fixture-source", default=None,
                               help="Local RawEvent fixture file or directory to replay as a fake live source")
    p_live_smoke.add_argument("--save-fixtures", action="store_true",
                               help="Save captured raw events as JSON fixtures to tests/fixtures/live/")
    p_live_smoke.add_argument("--persist-raw", action="store_true",
                               help="Write events through full DB pipeline (raw + normalized + alerts)")
    p_live_smoke.add_argument("--force", action="store_true",
                               help="Skip PMFI_ENABLE_LIVE check (for testing)")
    p_review = sub.add_parser("review-pass", help="Validate fixture replay and alert explainability without DB writes")
    p_review.add_argument("--format", choices=["table", "json"], default="table", help="Output format (default: table)")
    p_review.add_argument("--fixture-dir", default=None, help="Fixture directory to review (default: tests/fixtures/raw)")


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)
    cmd = args.command

    if cmd in ("replay", "replay-fixtures"):
        return cmd_replay(args)
    elif cmd == "status":
        return cmd_status(args)
    elif cmd == "health":
        return cmd_health(args)
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
    elif cmd == "data-quality-incidents":
        return cmd_data_quality_incidents(args)
    elif cmd == "delivery-failures":
        return cmd_delivery_failures(args)
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
    elif cmd == "review-pass":
        return cmd_review_pass(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
