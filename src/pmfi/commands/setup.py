from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pmfi.commands._shared import ROOT


def _check_result(name: str, status: str, detail: str, fix: str = "") -> dict[str, str]:
    if status not in {"ok", "warn", "fail"}:
        raise ValueError(f"unsupported check status: {status}")
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _classify_checks(checks: list[dict[str, str]]) -> tuple[str, int]:
    statuses = {c["status"] for c in checks}
    if "fail" in statuses:
        return "FAIL", 1
    if "warn" in statuses:
        return "WARN", 0
    return "OK", 0


def _copy_config_if_missing(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False
    import shutil

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _run_db_local_init() -> subprocess.CompletedProcess[str]:
    script = ROOT / "scripts" / "db_local.py"
    return subprocess.run(
        [sys.executable, str(script), "init"],
        cwd=ROOT,
        text=True,
        check=False,
    )


async def _query_scalar(db_url: str, query: str, *args: Any) -> Any:
    import asyncpg

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=1,
        server_settings={"search_path": "pmfi,public"},
        timeout=5,
    )
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    finally:
        await pool.close()


async def _query_rows(db_url: str, query: str, *args: Any) -> list[Any]:
    import asyncpg

    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=1,
        server_settings={"search_path": "pmfi,public"},
        timeout=5,
    )
    try:
        async with pool.acquire() as conn:
            return list(await conn.fetch(query, *args))
    finally:
        await pool.close()


async def _check_db_reachable(db_url: str) -> dict[str, str]:
    try:
        await _query_scalar(db_url, "SELECT 1")
        return _check_result("db_reachable", "ok", "Connected to local Postgres")
    except Exception as exc:
        return _check_result(
            "db_reachable",
            "fail",
            f"Cannot connect: {exc}",
            "Run: python scripts\\db_local.py up",
        )


async def _check_schema_present(db_url: str) -> dict[str, str]:
    required = ["venues", "markets", "raw_events", "normalized_trades", "alerts"]
    try:
        rows = await _query_rows(
            db_url,
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = current_schema()
              AND tablename = ANY($1::text[])
            """,
            required,
        )
        found = {r["tablename"] for r in rows}
        missing = [name for name in required if name not in found]
        if missing:
            return _check_result(
                "schema_present",
                "fail",
                f"Missing tables: {', '.join(missing)}",
                "Run: python scripts\\db_local.py init",
            )
        return _check_result("schema_present", "ok", f"Core tables present: {len(required)}")
    except Exception as exc:
        return _check_result(
            "schema_present",
            "fail",
            f"Schema check failed: {exc}",
            "Run: python scripts\\db_local.py init",
        )


async def _check_venues_configured(db_url: str) -> dict[str, str]:
    try:
        rows = await _query_rows(db_url, "SELECT venue_code FROM venues ORDER BY venue_code")
        if not rows:
            return _check_result(
                "venues_configured",
                "warn",
                "No venues are seeded",
                "Run: python scripts\\db_local.py init",
            )
        return _check_result(
            "venues_configured",
            "ok",
            "Venues: " + ", ".join(str(r["venue_code"]) for r in rows),
        )
    except Exception as exc:
        return _check_result(
            "venues_configured",
            "warn",
            f"Venue check failed: {exc}",
            "Run: python scripts\\db_local.py init",
        )


async def _check_watched_markets(db_url: str) -> dict[str, str]:
    try:
        count = await _query_scalar(db_url, "SELECT COUNT(*) FROM markets WHERE watched = true")
        if not count:
            return _check_result(
                "watched_markets",
                "warn",
                "No markets are watched",
                "Run: pmfi markets discover then pmfi markets watch <market_id>",
            )
        return _check_result("watched_markets", "ok", f"{count} watched market(s)")
    except Exception as exc:
        return _check_result(
            "watched_markets",
            "warn",
            f"Watched-market check failed: {exc}",
            "Run: pmfi markets discover then pmfi markets watch <market_id>",
        )


async def _check_baselines(db_url: str) -> dict[str, str]:
    try:
        count = await _query_scalar(db_url, "SELECT COUNT(*) FROM market_baselines")
        if not count:
            return _check_result(
                "baselines",
                "warn",
                "No market baselines are stored",
                "Run: pmfi baselines compute",
            )
        latest = await _query_scalar(db_url, "SELECT MAX(computed_at) FROM market_baselines")
        return _check_result("baselines", "ok", f"{count} baseline(s), latest={latest}")
    except Exception as exc:
        return _check_result(
            "baselines",
            "warn",
            f"Baseline check failed: {exc}",
            "Run: pmfi baselines compute",
        )


async def _check_recent_ingest(db_url: str) -> dict[str, str]:
    try:
        count = await _query_scalar(
            db_url,
            "SELECT COUNT(*) FROM raw_events WHERE received_at >= now() - interval '24 hours'",
        )
        last = await _query_scalar(db_url, "SELECT MAX(received_at) FROM raw_events")
        if not count:
            return _check_result(
                "recent_ingest",
                "warn",
                f"No raw events in the last 24h; last={last or 'never'}",
                "Run: pmfi ingest after selecting watched markets",
            )
        return _check_result("recent_ingest", "ok", f"{count} raw event(s) in the last 24h")
    except Exception as exc:
        return _check_result(
            "recent_ingest",
            "warn",
            f"Recent-ingest check failed: {exc}",
            "Run: pmfi ingest after selecting watched markets",
        )


async def _run_all_doctor_checks(db_url: str) -> list[dict[str, str]]:
    db_check = await _check_db_reachable(db_url)
    checks = [db_check]
    if db_check["status"] == "fail":
        for name in [
            "schema_present",
            "venues_configured",
            "watched_markets",
            "baselines",
            "recent_ingest",
        ]:
            checks.append(_check_result(name, "fail", "Skipped because DB is unreachable", "Fix db_reachable first"))
        return checks

    checks.append(await _check_schema_present(db_url))
    checks.append(await _check_venues_configured(db_url))
    checks.append(await _check_watched_markets(db_url))
    checks.append(await _check_baselines(db_url))
    checks.append(await _check_recent_ingest(db_url))
    return checks


def _render_doctor_table(checks: list[dict[str, str]], label: str) -> None:
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table

        console = Console(width=120)
        table = Table(title="pmfi doctor", show_lines=False)
        table.add_column("Check", style="cyan", min_width=20)
        table.add_column("Status", min_width=6)
        table.add_column("Detail", min_width=35)
        table.add_column("Fix", style="dim", min_width=20)
        for check in checks:
            table.add_row(check["name"], check["status"].upper(), check["detail"], check["fix"])
        console.print(table)
        console.print(Panel(f"Overall: {label}", expand=False))
        return
    except Exception:
        pass

    print("pmfi doctor")
    for check in checks:
        print(f"{check['status'].upper():<5} {check['name']}: {check['detail']}")
        if check["fix"]:
            print(f"      FIX: {check['fix']}")
    print(f"Overall: {label}")


def cmd_doctor(args: argparse.Namespace) -> int:
    from pmfi.config import load_config

    cfg = load_config()
    checks = asyncio.run(_run_all_doctor_checks(cfg.database.url))
    label, exit_code = _classify_checks(checks)
    if getattr(args, "json_output", False):
        print(json.dumps({"overall": label, "checks": checks}, indent=2))
    else:
        _render_doctor_table(checks, label)
    return exit_code


def cmd_init(args: argparse.Namespace) -> int:
    config_dir = ROOT / "config"
    src = config_dir / "app.example.yaml"
    dst = config_dir / "app.yaml"

    if src.exists():
        copied = _copy_config_if_missing(src, dst)
        if copied:
            print("[init] Created config/app.yaml from config/app.example.yaml")
        else:
            print("[init] config/app.yaml already exists; not overwritten")
    else:
        print("[init] WARNING: config/app.example.yaml missing; config scaffold skipped")

    print("[init] Applying local DB schema and seeds with python scripts\\db_local.py init")
    completed = _run_db_local_init()
    if completed.returncode != 0:
        print("[init] DB initialization failed.")
        print("       Run: python scripts\\db_local.py up")
        print("       Then: pmfi init")
        return completed.returncode or 1

    discover = bool(getattr(args, "discover", False) or getattr(args, "watch_top", None))
    if discover:
        top_n = getattr(args, "watch_top", None) or 10
        print(f"[init] --discover requested; run: pmfi markets discover --venue polymarket --limit {top_n}")
        print("[init] Discovery is left as an explicit operator step to avoid implicit live API calls.")
    else:
        print("[init] Optional next step: pmfi markets discover --venue polymarket")

    print("[init] Next steps:")
    print("  1. pmfi doctor")
    print("  2. pmfi markets watch <market_id>")
    print("  3. pmfi ingest")
    print("  4. pmfi dashboard")
    return 0
