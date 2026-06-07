from __future__ import annotations
import asyncio
import json
from pathlib import Path
from pmfi.domain import AlertDecision
from pmfi.delivery.stdout import deliver_stdout
from pmfi.delivery.file import FileDelivery
from decimal import Decimal

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
    assert data["severity"] == "high"


def test_deliver_stdout_includes_rule_version(capsys):
    """stdout payload must include rule_version for operator traceability."""
    decision = _make_alert()
    asyncio.run(deliver_stdout(decision, venue_code="polymarket", market_id="mkt-1"))
    out = capsys.readouterr().out
    data = json.loads(out.strip())
    assert "rule_version" in data, f"rule_version missing from stdout payload; keys={list(data)}"
    assert data["rule_version"] == "alert_rules.v1"

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
    assert rec["severity"] == "high"
