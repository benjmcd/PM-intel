from __future__ import annotations

import argparse
import copy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
REAL_RULES_PATH = ROOT / "config" / "alert_rules.yaml"


def _copy_rules(tmp_path: Path) -> Path:
    dst = tmp_path / "alert_rules.yaml"
    dst.write_text(REAL_RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def test_rules_list_prints_current_rules(capsys):
    from pmfi.cli import main

    rc = main(["rules", "list"])

    assert rc == 0
    out = capsys.readouterr().out
    for rule_id in _load(REAL_RULES_PATH)["rules"]:
        assert rule_id in out


def test_rules_enable_disable_and_set_mutate_existing_fields(monkeypatch, tmp_path):
    import pmfi.commands.rules as rules_cmd

    rules_path = _copy_rules(tmp_path)
    monkeypatch.setattr(rules_cmd, "_rules_yaml_path", lambda: rules_path)

    assert rules_cmd.cmd_rules(argparse.Namespace(rules_cmd="disable", rule_id="volume_spike_v1")) == 0
    assert _load(rules_path)["rules"]["volume_spike_v1"]["enabled"] is False

    assert rules_cmd.cmd_rules(argparse.Namespace(rules_cmd="enable", rule_id="volume_spike_v1")) == 0
    assert _load(rules_path)["rules"]["volume_spike_v1"]["enabled"] is True

    assert rules_cmd.cmd_rules(
        argparse.Namespace(
            rules_cmd="set",
            rule_id="volume_spike_v1",
            field="min_spike_multiplier",
            value="8.0",
        )
    ) == 0
    assert _load(rules_path)["rules"]["volume_spike_v1"]["min_spike_multiplier"] == pytest.approx(8.0)


@pytest.mark.parametrize(
    ("namespace"),
    [
        argparse.Namespace(rules_cmd="enable", rule_id="missing_rule"),
        argparse.Namespace(
            rules_cmd="set",
            rule_id="momentum_v1",
            field="missing_field",
            value="1",
        ),
        argparse.Namespace(
            rules_cmd="set",
            rule_id="momentum_v1",
            field="min_trades",
            value="not-a-number",
        ),
        argparse.Namespace(
            rules_cmd="set",
            rule_id="volume_spike_v1",
            field="min_spike_multiplier",
            value="0.5",
        ),
        argparse.Namespace(
            rules_cmd="set",
            rule_id="momentum_v1",
            field="severity",
            value="critical",
        ),
    ],
)
def test_rules_invalid_input_leaves_file_unchanged(monkeypatch, tmp_path, namespace):
    import pmfi.commands.rules as rules_cmd

    rules_path = _copy_rules(tmp_path)
    before = rules_path.read_bytes()
    monkeypatch.setattr(rules_cmd, "_rules_yaml_path", lambda: rules_path)

    assert rules_cmd.cmd_rules(namespace) != 0
    assert rules_path.read_bytes() == before


def test_atomic_write_round_trips_yaml(tmp_path):
    from pmfi.commands.rules import _atomic_write_rules

    path = tmp_path / "rules.yaml"
    data = {
        "version": "alert_rules.v1",
        "rules": {
            "volume_spike_v1": {
                "enabled": True,
                "min_spike_multiplier": 6.0,
                "severity": "low",
            }
        },
    }

    _atomic_write_rules(path, data)

    assert _load(path) == data


def test_reload_rules_rebuilds_thresholds_and_preserves_same_window_state():
    from pmfi.pipeline.engine import AlertEngine

    engine = AlertEngine()
    original_acc = engine._momentum_acc
    rules = copy.deepcopy(engine._load_rules())
    rules["rules"]["momentum_v1"]["min_net_capital_usd"] = 99999
    rules["rules"]["momentum_v1"]["min_trades"] = 9
    rules["rules"]["momentum_v1"]["severity"] = "low"
    rules["rules"]["volume_spike_v1"]["min_spike_multiplier"] = 12.0
    rules["rules"]["volume_spike_v1"]["min_baseline_trades"] = 30
    rules["rules"]["volume_spike_v1"]["enabled"] = False

    assert engine.reload_rules(rules) is True

    assert engine._momentum_acc is original_acc
    assert engine._momentum_min_capital == pytest.approx(99999)
    assert engine._momentum_min_trades == 9
    assert engine._momentum_severity == "low"
    assert engine._vs_multiplier == Decimal("12.0")
    assert engine._vs_min_trades == 30
    assert engine._vs_enabled is False


def test_reload_rules_window_change_rebuilds_only_momentum_accumulator():
    from pmfi.pipeline.engine import AlertEngine

    engine = AlertEngine()
    original_momentum = engine._momentum_acc
    original_directional = engine._accumulator
    rules = copy.deepcopy(engine._load_rules())
    rules["rules"]["momentum_v1"]["window_seconds"] = engine._momentum_window + 60

    assert engine.reload_rules(rules) is True

    assert engine._momentum_acc is not original_momentum
    assert engine._accumulator is original_directional


@pytest.mark.parametrize(
    "bad_rules",
    [
        {},
        {"version": "alert_rules.v1"},
        {"version": "alert_rules.v1", "rules": {}},
        {"version": "alert_rules.v1", "rules": None},
        {"rules": {"momentum_v1": {"severity": "critical"}}},
    ],
)
def test_reload_rules_rejects_invalid_config_without_state_change(bad_rules):
    from pmfi.pipeline.engine import AlertEngine

    engine = AlertEngine()
    old_rules = copy.deepcopy(engine._rules)
    old_capital = engine._momentum_min_capital

    assert engine.reload_rules(bad_rules) is False
    assert engine._rules == old_rules
    assert engine._momentum_min_capital == old_capital
