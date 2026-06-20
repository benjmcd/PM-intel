"""Offline tests asserting local-only network boundaries.

These tests require no network access, no live DB, and no Docker.
They verify that the compose file, HTTP delivery default, and .env.example
remain correctly scoped to loopback interfaces only.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO / "docker-compose.local.yml"
ENV_EXAMPLE = REPO / ".env.example"


# ---------------------------------------------------------------------------
# 1. Every port mapping in docker-compose.local.yml must be loopback-bound.
# ---------------------------------------------------------------------------

def test_compose_all_ports_loopback_bound() -> None:
    """No service may publish a port on all interfaces (0.0.0.0)."""
    data = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    services = data.get("services", {})
    for svc_name, svc in services.items():
        for mapping in svc.get("ports", []):
            mapping_str = str(mapping)
            assert mapping_str.startswith("127.0.0.1:"), (
                f"Service '{svc_name}' has port mapping '{mapping_str}' that is not "
                f"loopback-bound. All mappings must start with '127.0.0.1:'."
            )


# ---------------------------------------------------------------------------
# 2. HttpDelivery default endpoint must resolve to localhost / 127.0.0.1.
# ---------------------------------------------------------------------------

def test_http_delivery_default_endpoint_is_loopback() -> None:
    """HttpDelivery's default endpoint host must be localhost or 127.0.0.1."""
    from pmfi.delivery.http import HttpDelivery

    delivery = HttpDelivery()
    # Access the private attribute set in __init__
    endpoint: str = delivery._endpoint  # noqa: SLF001
    parsed = urlparse(endpoint)
    loopback_hosts = {"localhost", "127.0.0.1"}
    assert parsed.hostname in loopback_hosts, (
        f"HttpDelivery default endpoint '{endpoint}' resolves to host "
        f"'{parsed.hostname}', expected one of {loopback_hosts}."
    )


def test_alert_receiver_rejects_non_loopback_host() -> None:
    """The local alert receiver must never bind to all interfaces."""
    from pmfi.delivery.server import run_alert_receiver

    try:
        asyncio.run(run_alert_receiver(host="0.0.0.0", port=8765))
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:  # pragma: no cover - the server would otherwise block
        raise AssertionError("run_alert_receiver accepted non-loopback host")


def test_cmd_alerts_serve_rejects_non_loopback_before_binding(capsys) -> None:
    """The CLI should reject public bind hosts before starting aiohttp."""
    from pmfi.commands.alerts import cmd_alerts_serve

    args = argparse.Namespace(host="0.0.0.0", port=8765)
    with patch("pmfi.delivery.server.run_alert_receiver", new=AsyncMock()) as run_receiver:
        rc = cmd_alerts_serve(args)

    assert rc == 1
    run_receiver.assert_not_called()
    assert "loopback" in capsys.readouterr().out


def test_cmd_dashboard_rejects_non_loopback_db_url_before_start(capsys) -> None:
    """Dashboard DB override must not point at a non-loopback Postgres host."""
    from pmfi.commands.dashboard import cmd_dashboard

    reserved_port = "54" + "32"
    args = argparse.Namespace(
        db_url=f"postgresql://pmfi:secret@192.0.2.10:{reserved_port}/pmfi",
        port=8766,
    )
    cfg = MagicMock()
    cfg.database.url = "postgresql://pmfi:secret@localhost:5433/pmfi"
    with patch("pmfi.config.load_config", return_value=cfg), \
            patch("pmfi.dashboard.server.run_dashboard", new=AsyncMock()) as run_dashboard:
        rc = cmd_dashboard(args)

    assert rc == 1
    run_dashboard.assert_not_called()
    assert "--db-url" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 3. .env.example must not contain the dead PMFI_ALERT_HTTP_RECEIVER_URL key.
# ---------------------------------------------------------------------------

def test_env_example_no_dead_http_receiver_url() -> None:
    """PMFI_ALERT_HTTP_RECEIVER_URL must not appear in .env.example."""
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "PMFI_ALERT_HTTP_RECEIVER_URL" not in content, (
        ".env.example still contains PMFI_ALERT_HTTP_RECEIVER_URL, which is "
        "never read by any code and should have been removed."
    )


# ---------------------------------------------------------------------------
# 4. load_config warns when the well-known default DB password is in use.
# ---------------------------------------------------------------------------

def test_load_config_warns_on_default_password(tmp_path, caplog, monkeypatch) -> None:
    import logging

    from pmfi.config import load_config

    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text("database: {}\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pmfi.config"):
        load_config(cfg_file)
    assert any("well-known default password" in r.getMessage() for r in caplog.records)


def test_load_config_no_warning_with_custom_password(tmp_path, caplog, monkeypatch) -> None:
    import logging

    from pmfi.config import load_config

    monkeypatch.setenv("DATABASE_URL", "postgresql://pmfi:s3cret@localhost:5433/pmfi")
    cfg_file = tmp_path / "app.yaml"
    cfg_file.write_text("database: {}\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="pmfi.config"):
        load_config(cfg_file)
    assert not any("well-known default password" in r.getMessage() for r in caplog.records)
