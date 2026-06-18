"""Localhost-only HTTP dashboard for live ingest rate/volume and alert review.

The dashboard serves read endpoints for feed-health, volume, and alerts, plus a
single local append-only alert review POST. It binds 127.0.0.1 only (never
public) and does not add auth, SaaS, external services, or trading surfaces.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _parse_alert_review_body(body: Any) -> dict[str, str | None]:
    """Validate dashboard POST /api/alerts/{id}/review JSON."""
    from pmfi.dashboard.queries import ALLOWED_REVIEW_LABELS

    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    allowed = {"label", "category", "notes", "reviewed_by"}
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")

    label = body.get("label")
    if not isinstance(label, str) or label not in ALLOWED_REVIEW_LABELS:
        raise ValueError("label must be one of: tp, fp, noise")

    parsed: dict[str, str | None] = {"label": label}
    for name in ("category", "notes", "reviewed_by"):
        value = body.get(name)
        if value is None:
            if name in body:
                raise ValueError(f"{name} must be a string")
            parsed[name] = None
            continue
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        parsed[name] = value
    return parsed


def _request_origin(request: Any) -> str:
    return f"{request.scheme}://{request.host}"


def _url_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _review_write_origin_error(request: Any) -> str | None:
    """Return an error detail when a dashboard write has a foreign origin."""
    expected = _request_origin(request)
    origin = request.headers.get("Origin")
    if origin and _url_origin(origin) != expected:
        return "review writes require same-origin Origin"
    referer = request.headers.get("Referer")
    if referer and _url_origin(referer) != expected:
        return "review writes require same-origin Referer"
    return None


def _create_dashboard_app(pool: Any):
    from aiohttp import web

    from pmfi.dashboard.queries import feed_health, volume_timeseries, recent_alerts
    from pmfi.db.repos.alerts import insert_alert_review

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

    async def _review_alert(request: web.Request) -> web.Response:
        origin_error = _review_write_origin_error(request)
        if origin_error:
            return web.json_response({"error": "forbidden", "detail": origin_error}, status=403)
        if request.content_type != "application/json":
            return web.json_response(
                {"error": "invalid body", "detail": "Content-Type must be application/json"},
                status=400,
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid body", "detail": "body must be JSON"}, status=400)
        try:
            parsed = _parse_alert_review_body(body)
        except ValueError as exc:
            return web.json_response({"error": "invalid body", "detail": str(exc)}, status=400)
        async with pool.acquire() as conn:
            review = await insert_alert_review(
                conn,
                request.match_info["alert_id"],
                label=parsed["label"] or "",
                category=parsed["category"],
                notes=parsed["notes"],
                reviewed_by=parsed["reviewed_by"],
            )
        if review is None:
            return web.json_response({"error": "not found", "alert_id": request.match_info["alert_id"]}, status=404)
        return web.json_response({
            "ok": True,
            "alert_id": review["alert_id"],
            "review": review,
            "generated_at": _now_iso(),
        })

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
    app.router.add_post("/api/alerts/{alert_id}/review", _review_alert)
    app.router.add_get("/healthz", _healthz)
    if _STATIC_DIR.is_dir():
        app.router.add_static("/static/", _STATIC_DIR)
    return app


async def run_dashboard(*, db_url: str, host: str = "127.0.0.1", port: int = 8766) -> None:
    """Serve the dashboard JSON endpoints on localhost until interrupted.

    Endpoints (GET, JSON):
      /api/feedhealth          per-venue last-event age, events_60s/5m, unresolved dead-letters
      /api/volume[?minutes=N]  per-venue per-bucket trade_count + gross capital volume
      /api/alerts              recent alerts with review filters
      /api/alerts/{id}/review  append one local alert review row (POST)
      /healthz                 liveness + DB reachability
    """
    from aiohttp import web

    from pmfi.db import create_pool, close_pool

    # host is forced to loopback below; never honor a public bind.
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning("dashboard: ignoring non-loopback host %r; binding 127.0.0.1", host)
        host = "127.0.0.1"

    pool = await create_pool(db_url)
    app = _create_dashboard_app(pool)

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
