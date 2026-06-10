"""Offline tests asserting local-only network boundaries.

These tests require no network access, no live DB, and no Docker.
They verify that the compose file, HTTP delivery default, and .env.example
remain correctly scoped to loopback interfaces only.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

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
