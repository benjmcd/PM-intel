"""Dashboard command handler."""
from __future__ import annotations

import argparse
import asyncio


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Run the localhost ingest-rate dashboard and local alert-review endpoint."""
    from pmfi.config import load_config
    from pmfi.commands._shared import is_loopback_db_url
    from pmfi.dashboard.server import run_dashboard

    cfg = load_config()
    db_url = getattr(args, "db_url", None) or cfg.database.url
    if not is_loopback_db_url(db_url):
        print("[dashboard] --db-url must point to localhost or another loopback address.")
        return 1
    port = getattr(args, "port", 8766)
    try:
        asyncio.run(run_dashboard(db_url=db_url, host="127.0.0.1", port=port))
    except KeyboardInterrupt:
        print("\n[dashboard] stopped.")
    return 0
