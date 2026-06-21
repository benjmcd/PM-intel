from __future__ import annotations

import argparse
import copy
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from pmfi.commands._shared import ROOT

_VALID_SEVERITIES = frozenset({"low", "medium", "high"})
_DECORATIVE_FIELDS = frozenset({"type", "description"})


def _rules_yaml_path() -> Path:
    return ROOT / "config" / "alert_rules.yaml"


def _load_rules_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"rules file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"rules file is malformed: {path}")
    if not isinstance(data.get("rules"), dict):
        raise ValueError("rules file must contain a mapping at top-level key 'rules'")
    return data


def _atomic_write_rules(path: Path, data: dict[str, Any]) -> None:
    original_rules = copy.deepcopy(data.get("rules"))
    rendered = yaml.safe_dump(data, sort_keys=False, allow_unicode=False)
    parsed_back = yaml.safe_load(rendered) or {}
    if parsed_back.get("rules") != original_rules:
        raise ValueError("serialized rules YAML failed round-trip validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".rules-tmp-",
        suffix=".yaml",
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _known_fields(rule_cfg: dict[str, Any]) -> set[str]:
    return set(rule_cfg)


def _coerce_like(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError("expected boolean value")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if current is None:
        return raw
    return str(raw)


def _validate_value(field: str, value: Any) -> None:
    if field == "severity" and value not in _VALID_SEVERITIES:
        raise ValueError(f"severity must be one of: {', '.join(sorted(_VALID_SEVERITIES))}")
    if isinstance(value, (int, float)) and field.startswith(("min_", "history_", "window_")):
        if value <= 0:
            raise ValueError(f"{field} must be positive")
    if field == "min_spike_multiplier" and float(value) <= 1:
        raise ValueError("min_spike_multiplier must be greater than 1")
    if field == "acceptable_fp_rate_percent" and not (0 <= float(value) <= 100):
        raise ValueError("acceptable_fp_rate_percent must be between 0 and 100")


def _print_rules_table(data: dict[str, Any]) -> None:
    rule_map = data.get("rules") or {}
    rows = []
    for rule_id, cfg in sorted(rule_map.items()):
        thresholds = []
        for key, value in cfg.items():
            if key in {"enabled", "severity"} or key in _DECORATIVE_FIELDS:
                continue
            thresholds.append(f"{key}={value}")
        rows.append((rule_id, str(cfg.get("enabled", True)), str(cfg.get("severity", "")), ", ".join(thresholds)))

    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Alert rules")
        table.add_column("Rule", no_wrap=True)
        table.add_column("Enabled")
        table.add_column("Severity")
        table.add_column("Config")
        for row in rows:
            table.add_row(*row)
        Console(width=180).print(table)
    except Exception:
        for rule_id, enabled, severity, thresholds in rows:
            print(f"{rule_id}\tenabled={enabled}\tseverity={severity}\t{thresholds}")


def cmd_rules(args: argparse.Namespace) -> int:
    path = _rules_yaml_path()
    rules_cmd = getattr(args, "rules_cmd", None) or "list"
    try:
        data = _load_rules_yaml(path)
    except Exception as exc:
        print(f"[rules] {exc}")
        return 1

    rule_map = data.get("rules") or {}
    if rules_cmd == "list":
        _print_rules_table(data)
        return 0

    rule_id = getattr(args, "rule_id", None)
    if rule_id not in rule_map:
        print(f"[rules] unknown rule: {rule_id!r}")
        return 1

    if rules_cmd in {"enable", "disable"}:
        data["rules"][rule_id]["enabled"] = rules_cmd == "enable"
        try:
            _atomic_write_rules(path, data)
        except Exception as exc:
            print(f"[rules] failed to write rules: {exc}")
            return 1
        print(f"[rules] {rule_id}: {'enabled' if rules_cmd == 'enable' else 'disabled'}")
        return 0

    if rules_cmd == "set":
        field = getattr(args, "field", "")
        if field not in _known_fields(rule_map[rule_id]):
            print(f"[rules] unknown field {field!r} for rule {rule_id!r}")
            return 1
        try:
            value = _coerce_like(rule_map[rule_id].get(field), getattr(args, "value", ""))
            _validate_value(field, value)
        except Exception as exc:
            print(f"[rules] invalid value for {rule_id}.{field}: {exc}")
            return 1
        data["rules"][rule_id][field] = value
        try:
            _atomic_write_rules(path, data)
        except Exception as exc:
            print(f"[rules] failed to write rules: {exc}")
            return 1
        print(f"[rules] {rule_id}.{field} = {value!r}")
        return 0

    print(f"[rules] unknown subcommand: {rules_cmd!r}")
    return 1
