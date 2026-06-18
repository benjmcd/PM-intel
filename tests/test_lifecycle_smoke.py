from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_lifecycle_smoke():
    path = ROOT / "scripts" / "lifecycle_smoke.py"
    spec = importlib.util.spec_from_file_location("lifecycle_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_lifecycle_smoke_produces_db_free_runner_contract_proof():
    lifecycle_smoke = _load_lifecycle_smoke()

    payload = lifecycle_smoke.run_lifecycle_smoke()

    assert payload["ok"] is True
    assert payload["status"] == "pass"
    assert payload["source"] == "db_free_runner_contracts"
    checks = {check["name"]: check for check in payload["checks"]}
    assert set(checks) == {
        "raw_event_replay_dedupe",
        "duplicate_trade_skip",
        "suppression_cache_seed",
        "suppression_window_expiry",
        "non_trade_raw_persistence",
        "kalshi_rest_poll_overlap_dedupe",
    }
    assert checks["raw_event_replay_dedupe"]["details"]["normalized_source_ids"] == [
        "restart-1",
        "restart-2",
    ]
    assert checks["duplicate_trade_skip"]["details"]["alerts_delivered"] == 0
    assert checks["suppression_cache_seed"]["details"]["seeded_entries"] == 1
    assert checks["suppression_window_expiry"]["details"] == {
        "raw_events_seen": 3,
        "alerts_inserted": 2,
        "alerts_delivered": 2,
        "alerts_suppressed": 1,
        "suppression_window_seconds": 300,
    }
    assert checks["non_trade_raw_persistence"]["details"]["non_trade_skips"] == 1
    assert checks["non_trade_raw_persistence"]["details"]["alerts_delivered"] == 0
    assert checks["kalshi_rest_poll_overlap_dedupe"]["details"] == {
        "ticker": "KXOVERLAP-26JUN",
        "poll_windows": [["t1", "t2"], ["t2", "t3"]],
        "raw_events_seen": 4,
        "raw_events_inserted": 3,
        "raw_event_duplicates": 1,
        "normalized_trades_inserted": 3,
        "duplicate_trades": 0,
        "metrics_upserted": 3,
        "alerts_inserted": 3,
        "alerts_delivered": 3,
        "source_event_ids": ["t1", "t2", "t3"],
        "venue_trade_ids": ["t1", "t2", "t3"],
        "source_channels": ["rest_trades"],
    }


def test_lifecycle_smoke_fails_closed_on_malformed_proof():
    lifecycle_smoke = _load_lifecycle_smoke()

    payload = lifecycle_smoke.run_lifecycle_smoke()
    payload["checks"][0]["details"]["normalized_source_ids"] = []

    with pytest.raises(RuntimeError, match="normalized_source_ids"):
        lifecycle_smoke.validate_lifecycle_payload(payload)


def test_lifecycle_smoke_fails_closed_on_wrong_suppression_expiry_counts():
    lifecycle_smoke = _load_lifecycle_smoke()

    payload = lifecycle_smoke.run_lifecycle_smoke()
    checks = {check["name"]: check for check in payload["checks"]}
    checks["suppression_window_expiry"]["details"]["alerts_inserted"] = 1

    with pytest.raises(RuntimeError, match="suppression_window_expiry alerts_inserted"):
        lifecycle_smoke.validate_lifecycle_payload(payload)


def test_lifecycle_smoke_fails_closed_on_wrong_kalshi_overlap_counts():
    lifecycle_smoke = _load_lifecycle_smoke()

    payload = lifecycle_smoke.run_lifecycle_smoke()
    checks = {check["name"]: check for check in payload["checks"]}
    checks["kalshi_rest_poll_overlap_dedupe"]["details"]["normalized_trades_inserted"] = 4

    with pytest.raises(RuntimeError, match="kalshi_rest_poll_overlap_dedupe normalized_trades_inserted"):
        lifecycle_smoke.validate_lifecycle_payload(payload)
