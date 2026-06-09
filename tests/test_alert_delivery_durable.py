"""US-10: Durable alert delivery tests (offline, no DB, no network)."""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from pmfi.domain import AlertDecision


# ---------------------------------------------------------------------------
# Shared fixture helper
# ---------------------------------------------------------------------------

def _make_decision() -> AlertDecision:
    return AlertDecision(
        emit_alert=True,
        rule_id="large_trade_absolute_v1",
        rule_version="alert_rules.v1",
        severity="high",
        confidence="medium",
        score=Decimal("1.0"),
        reason_codes=("capital_at_risk_threshold",),
        evidence={"venue_code": "polymarket", "capital_at_risk_usd": "50000"},
        data_quality="unverified",
    )


# ---------------------------------------------------------------------------
# 1. FileDelivery round-trip
# ---------------------------------------------------------------------------

def test_file_delivery_writes_durable_jsonl(tmp_path: Path) -> None:
    """FileDelivery appends a JSONL record to a dated file under the given dir."""
    from pmfi.delivery.file import FileDelivery

    fd = FileDelivery(tmp_path)
    decision = _make_decision()
    asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="mkt-99"))

    files = list(tmp_path.glob("alerts_*.jsonl"))
    assert len(files) == 1, f"expected exactly one alerts_*.jsonl, got {files}"

    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    rec = json.loads(lines[0])
    assert rec["rule_id"] == "large_trade_absolute_v1"
    assert rec["severity"] == "high"
    assert rec["venue_code"] == "polymarket"
    assert rec["market_id"] == "mkt-99"
    assert "ts" in rec


def test_file_delivery_appends_multiple(tmp_path: Path) -> None:
    """Multiple deliver() calls produce multiple lines in the same file."""
    from pmfi.delivery.file import FileDelivery

    fd = FileDelivery(tmp_path)
    decision = _make_decision()
    asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="mkt-1"))
    asyncio.run(fd.deliver(decision, venue_code="kalshi", market_id="mkt-2"))

    files = list(tmp_path.glob("alerts_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["venue_code"] == "polymarket"
    assert json.loads(lines[1])["venue_code"] == "kalshi"


def test_file_delivery_creates_missing_dirs(tmp_path: Path) -> None:
    """FileDelivery creates nested output dirs if they do not exist."""
    from pmfi.delivery.file import FileDelivery

    nested = tmp_path / "reports" / "alerts"
    fd = FileDelivery(nested)
    assert nested.exists(), "dirs should be created by FileDelivery.__init__"
    decision = _make_decision()
    asyncio.run(fd.deliver(decision, venue_code="kalshi", market_id=None))
    assert any(nested.glob("alerts_*.jsonl"))


# ---------------------------------------------------------------------------
# 2. _delivery_banner pure helper
# ---------------------------------------------------------------------------

from pmfi.cli import _delivery_banner


def test_banner_file_mode_mentions_path() -> None:
    banner = _delivery_banner("file", "/some/reports/alerts/alerts_YYYY-MM-DD.jsonl")
    assert "/some/reports/alerts/alerts_YYYY-MM-DD.jsonl" in banner
    assert "durable" in banner.lower() or "Alerts are written durably" in banner


def test_banner_console_mode_warns_ephemeral() -> None:
    banner = _delivery_banner("console", "stdout (ephemeral)")
    assert "ephemeral" in banner.lower()
    # Should recommend file mode
    assert "file" in banner.lower()


def test_banner_http_mode_mentions_endpoint() -> None:
    banner = _delivery_banner("localhost_http_receiver", "http://localhost:8765/alerts")
    assert "http://localhost:8765/alerts" in banner


def test_banner_all_modes_mention_alerts_list() -> None:
    """Every delivery mode must surface the DB-backed query commands."""
    for mode, dest in [
        ("file", "/path/to/alerts"),
        ("console", "stdout (ephemeral)"),
        ("localhost_http_receiver", "http://localhost:8765/alerts"),
    ]:
        banner = _delivery_banner(mode, dest)
        assert "pmfi alerts list" in banner, f"mode={mode}: missing 'pmfi alerts list'"
        # DB-backed retrieval is mentioned (dashboard or watch)
        assert "pmfi watch" in banner or "dashboard" in banner.lower(), (
            f"mode={mode}: missing DB-backed retrieval hint"
        )


def test_banner_all_modes_mention_db() -> None:
    """Every banner must confirm alerts are stored in DB regardless of mode."""
    for mode, dest in [
        ("file", "/path/to/alerts"),
        ("console", "stdout (ephemeral)"),
        ("localhost_http_receiver", "http://localhost:8765/alerts"),
    ]:
        banner = _delivery_banner(mode, dest)
        assert "DB" in banner or "db" in banner.lower() or "insert_alert" in banner, (
            f"mode={mode}: no mention of DB persistence"
        )


# ---------------------------------------------------------------------------
# 3. app.example.yaml defaults to file delivery
# ---------------------------------------------------------------------------

def test_example_config_default_delivery_is_file() -> None:
    """A fresh operator copying app.example.yaml gets durable file delivery."""
    from pathlib import Path as _Path
    from pmfi.config import load_config

    example = _Path(__file__).resolve().parents[1] / "config" / "app.example.yaml"
    assert example.exists(), f"example config not found at {example}"
    cfg = load_config(example)
    assert cfg.alerts.default_delivery == "file", (
        f"expected 'file', got {cfg.alerts.default_delivery!r}. "
        "app.example.yaml must default to durable file delivery."
    )
