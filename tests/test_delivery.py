from __future__ import annotations
import asyncio
import json
import sys
from types import SimpleNamespace
from pathlib import Path
from pmfi.domain import AlertDecision
from pmfi.delivery.stdout import deliver_stdout
from pmfi.delivery.file import FileDelivery
from pmfi.delivery.http import HttpDelivery, validate_loopback_http_endpoint
from pmfi.delivery.server import validate_local_bind_host
from decimal import Decimal
import pytest

def _make_alert() -> AlertDecision:
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

def test_deliver_stdout_runs(capsys):
    decision = _make_alert()
    asyncio.run(deliver_stdout(decision, venue_code="polymarket", market_id="mkt-1"))
    out = capsys.readouterr().out
    data = json.loads(out.strip())
    assert data["alert"] is True
    assert data["rule_id"] == "large_trade_absolute_v1"
    assert data["rule_version"] == "alert_rules.v1"
    assert data["severity"] == "high"
    assert data["data_quality"] == "unverified"

def test_file_delivery_writes(tmp_path):
    fd = FileDelivery(tmp_path)
    decision = _make_alert()
    asyncio.run(fd.deliver(decision, venue_code="polymarket", market_id="mkt-1"))
    files = list(tmp_path.glob("alerts_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["rule_id"] == "large_trade_absolute_v1"
    assert rec["rule_version"] == "alert_rules.v1"
    assert rec["severity"] == "high"
    assert rec["data_quality"] == "unverified"

def test_http_delivery_posts_data_quality(monkeypatch):
    posted = {}

    class FakeTimeout:
        def __init__(self, *, total):
            self.total = total

    class FakeResponse:
        status = 202

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, endpoint, *, data, headers, timeout):
            posted["endpoint"] = endpoint
            posted["payload"] = json.loads(data)
            posted["headers"] = headers
            posted["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(ClientSession=FakeSession, ClientTimeout=FakeTimeout),
    )

    decision = _make_alert()
    asyncio.run(
        HttpDelivery("http://localhost:9999/alerts", timeout=1.25).deliver(
            decision, venue_code="polymarket", market_id="mkt-1"
        )
    )

    assert posted["endpoint"] == "http://localhost:9999/alerts"
    assert posted["headers"] == {"Content-Type": "application/json"}
    assert posted["timeout"].total == 1.25
    assert posted["payload"]["rule_version"] == "alert_rules.v1"
    assert posted["payload"]["data_quality"] == "unverified"

@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:9999/alerts",
        "https://localhost/alerts",
        "http://127.0.0.1:9999/alerts",
        "http://[::1]:9999/alerts",
    ],
)
def test_validate_loopback_http_endpoint_allows_loopback_http_endpoints(endpoint):
    assert validate_loopback_http_endpoint(endpoint) == endpoint

@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/alerts",
        "http://192.168.1.50/alerts",
        "http://0.0.0.0/alerts",
        "http:///alerts",
        "ftp://localhost/alerts",
    ],
)
def test_http_delivery_rejects_non_loopback_endpoint_before_network_call(endpoint):
    with pytest.raises(ValueError, match="loopback|local endpoint"):
        HttpDelivery(endpoint)

def test_validate_local_bind_host_allows_loopback_hosts():
    assert validate_local_bind_host("127.0.0.1") == "127.0.0.1"
    assert validate_local_bind_host("localhost") == "localhost"
    assert validate_local_bind_host("::1") == "::1"

def test_validate_local_bind_host_rejects_non_loopback_host():
    with pytest.raises(ValueError, match="loopback"):
        validate_local_bind_host("0.0.0.0")
