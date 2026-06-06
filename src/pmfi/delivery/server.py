"""Minimal local HTTP receiver for testing alert delivery."""
from __future__ import annotations
import json
import logging

logger = logging.getLogger(__name__)


async def run_alert_receiver(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run a minimal aiohttp server that accepts POST /alerts and logs them."""
    from aiohttp import web

    async def handle_alert(request: web.Request) -> web.Response:
        try:
            body = await request.text()
            data = json.loads(body)
            rule = data.get("rule_id", "?")
            severity = data.get("severity", "?")
            venue = data.get("venue_code", "?")
            logger.info("received alert rule=%s severity=%s venue=%s", rule, severity, venue)
            print(f"[alert] rule={rule} severity={severity} venue={venue}")
        except Exception as exc:
            logger.warning("Alert parse error: %s", exc)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_post("/alerts", handle_alert)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    print(f"[alerts serve] listening on http://{host}:{port}/alerts — Ctrl+C to stop")
    try:
        import asyncio
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
