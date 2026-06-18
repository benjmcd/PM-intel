from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_baseline_smoke():
    path = ROOT / "scripts" / "baseline_smoke.py"
    spec = importlib.util.spec_from_file_location("baseline_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(**overrides):
    baseline_smoke = _load_baseline_smoke()
    payload = {
        "ok": True,
        "status": "pass",
        "source": "db_free_baseline_contracts",
        "checks": [
            {"name": name, "status": "pass", "details": {"proof": name}}
            for name in baseline_smoke.EXPECTED_CHECKS
        ],
    }
    payload.update(overrides)
    return payload


def test_baseline_smoke_success_payload_proves_expected_contracts():
    baseline_smoke = _load_baseline_smoke()

    payload = baseline_smoke.run_baseline_smoke()

    assert payload["ok"] is True
    assert payload["status"] == "pass"
    assert payload["source"] == "db_free_baseline_contracts"
    assert [check["name"] for check in payload["checks"]] == list(baseline_smoke.EXPECTED_CHECKS)

    by_name = {check["name"]: check["details"] for check in payload["checks"]}
    assert by_name["compute_path_uses_normalized_trades"]["source_table"] == "normalized_trades"
    assert by_name["compute_path_uses_normalized_trades"]["excluded_table"] == "metric_windows"
    assert by_name["baseline_upsert_conflict_constraint"]["constraint"] == (
        "market_baselines_market_scope_unique"
    )
    assert by_name["baseline_available_alert"]["data_quality"] == "baseline_available"
    assert by_name["baseline_available_alert"]["baseline_state"] == "baseline_sufficient"
    assert by_name["baseline_stale_alert"]["data_quality"] == "baseline_stale"
    assert by_name["baseline_stale_alert"]["baseline_state"] == "baseline_stale"
    assert by_name["baseline_stale_alert"]["reason_codes"] == ["capital_above_minimum_threshold"]
    assert by_name["baseline_missing_alert"]["data_quality"] == "baseline_pending"
    assert by_name["volume_spike_uses_prior_history"]["baseline_median_usd"] == 100.0
    assert by_name["volume_spike_uses_prior_history"]["spike_multiplier"] == 60.0


def test_baseline_smoke_json_main_emits_valid_payload(capsys):
    baseline_smoke = _load_baseline_smoke()

    assert baseline_smoke.main(["--format", "json"]) == 0

    emitted = json.loads(capsys.readouterr().out)
    assert [check["name"] for check in emitted["checks"]] == list(baseline_smoke.EXPECTED_CHECKS)


def test_baseline_smoke_fails_closed_on_empty_payload():
    baseline_smoke = _load_baseline_smoke()

    with pytest.raises(RuntimeError, match="checks must be a non-empty list"):
        baseline_smoke.validate_baseline_payload(_payload(checks=[]))


def test_baseline_smoke_fails_closed_on_malformed_check_details():
    baseline_smoke = _load_baseline_smoke()
    checks = _payload()["checks"]
    checks[0] = {"name": checks[0]["name"], "status": "pass"}

    with pytest.raises(RuntimeError, match="details"):
        baseline_smoke.validate_baseline_payload(_payload(checks=checks))


def test_baseline_smoke_fails_closed_on_unexpected_check_set():
    baseline_smoke = _load_baseline_smoke()
    checks = [
        check
        for check in _payload()["checks"]
        if check["name"] != "baseline_stale_alert"
    ]

    with pytest.raises(RuntimeError, match="baseline_stale_alert"):
        baseline_smoke.validate_baseline_payload(_payload(checks=checks))
