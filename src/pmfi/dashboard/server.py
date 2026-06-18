"""Localhost-only HTTP dashboard for live ingest rate/volume (Phase 1: JSON API).

Read-only: serves per-venue feed-health and volume time-series computed from the
existing Postgres tables. Binds 127.0.0.1 only (never public). A browser UI is
layered on in a later phase; for now the endpoints return JSON snapshots that a
poller (or `curl`) consumes.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STATIC_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger(__name__)


def _parse_alerts_query(query: Any) -> dict[str, Any]:
    """Parse dashboard /api/alerts query params and return typed kwargs for recent_alerts()."""
    from pmfi.dashboard.queries import ALLOWED_REVIEW_LABELS, ALLOWED_REVIEW_STATES, ALLOWED_TRIAGE_FLAGS

    def _int(name: str, default: int, lo: int, hi: int) -> int:
        raw = query.get(name, None)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer")
        if value < lo or value > hi:
            raise ValueError(f"{name} must be between {lo} and {hi}")
        return value

    review_state = query.get("review_state")
    review_state = review_state if review_state not in ("", None) else None
    review_label = query.get("review_label")
    review_label = review_label if review_label not in ("", None) else None

    triage_flag_inputs: list[str] = []
    if hasattr(query, "getall"):
        raw_flags = query.getall("triage_flag", [])
    else:
        value = query.get("triage_flag", None)
        raw_flags = value if isinstance(value, list) else ([value] if value else [])
    for raw_flag in raw_flags:
        if raw_flag is None:
            continue
        triage_flag_inputs.extend(
            [part.strip() for part in str(raw_flag).split(",") if part.strip()]
        )

    if review_state is not None and review_state not in ALLOWED_REVIEW_STATES:
        raise ValueError(f"invalid review_state {review_state!r}")
    if review_label is not None and review_label not in ALLOWED_REVIEW_LABELS:
        raise ValueError(f"invalid review_label {review_label!r}")
    if review_state == "unreviewed" and review_label is not None:
        raise ValueError("review_state=unreviewed cannot be combined with review_label")

    unknown_flags = [f for f in triage_flag_inputs if f not in ALLOWED_TRIAGE_FLAGS]
    if unknown_flags:
        raise ValueError(f"invalid triage_flag {', '.join(sorted(set(unknown_flags)))}")

    limit = _int("limit", 20, 1, 200)
    return {
        "limit": limit,
        "review_state": review_state,
        "review_label": review_label,
        "triage_flags_filter": triage_flag_inputs,
    }


async def run_dashboard(*, db_url: str, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Serve the dashboard JSON endpoints on localhost until interrupted.

    Endpoints (GET, JSON):
      /api/feedhealth          per-venue last-event age, events_60s/5m, unresolved dead-letters
      /api/volume[?minutes=N]  per-venue per-bucket trade_count + gross capital volume
      /healthz                 liveness + DB reachability
    """
    from aiohttp import web

    from pmfi.db import create_pool, close_pool
    from pmfi.dashboard.queries import feed_health, volume_timeseries, recent_alerts

    # host is forced to loopback below; never honor a public bind.
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("dashboard: ignoring non-loopback host %r; binding 127.0.0.1", host)
        host = "127.0.0.1"

    pool = await create_pool(db_url)

    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _feedhealth(request: web.Request) -> web.Response:
        try:
            lookback = max(1, min(int(request.query.get("lookback", "10")), 1440))
        except (TypeError, ValueError):
            lookback = 10
        async with pool.acquire() as conn:
            venues = await feed_health(conn, lookback_minutes=lookback)
        return web.json_response({"venues": venues, "lookback_minutes": lookback, "generated_at": _now_iso()})

    async def _volume(request: web.Request) -> web.Response:
        try:
            minutes = max(1, min(int(request.query.get("minutes", "60")), 1440))
        except (TypeError, ValueError):
            minutes = 60
        async with pool.acquire() as conn:
            buckets = await volume_timeseries(conn, lookback_minutes=minutes)
        return web.json_response({"buckets": buckets, "minutes": minutes, "generated_at": _now_iso()})

    async def _alerts(request: web.Request) -> web.Response:
        try:
            params = _parse_alerts_query(request.query)
        except (TypeError, ValueError) as exc:
            return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        async with pool.acquire() as conn:
            try:
                alerts = await recent_alerts(
                    conn,
                    limit=params["limit"],
                    review_state=params["review_state"],
                    review_label=params["review_label"],
                    triage_flags_filter=params["triage_flags_filter"],
                )
            except ValueError as exc:
                return web.json_response({"error": "invalid query", "detail": str(exc)}, status=400)
        return web.json_response({"alerts": alerts, "generated_at": _now_iso()})

    async def _healthz(request: web.Request) -> web.Response:
        ok = True
        try:
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
        except Exception as exc:  # pragma: no cover - defensive
            ok = False
            logger.warning("dashboard healthz DB check failed: %s", exc)
        return web.json_response({"ok": ok, "generated_at": _now_iso()})

    async def _index(request: web.Request) -> web.Response:
        return web.FileResponse(_STATIC_DIR / "index.html")

    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/api/feedhealth", _feedhealth)
    app.router.add_get("/api/volume", _volume)
    app.router.add_get("/api/alerts", _alerts)
    app.router.add_get("/healthz", _healthz)
    if _STATIC_DIR.is_dir():
        app.router.add_static("/static/", _STATIC_DIR)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(
        f"[dashboard] listening on http://{host}:{port}  "
        f"(/api/feedhealth  /api/volume  /api/alerts  /healthz) — Ctrl+C to stop"
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        await close_pool(pool)
