from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_autostart_smoke():
    path = ROOT / "scripts" / "autostart_smoke.py"
    spec = importlib.util.spec_from_file_location("autostart_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_payload():
    return {
        "ok": True,
        "status": "pass",
        "source": "db_free_autostart_contracts",
        "checks": [
            {"name": "dry_run_plan_shape", "status": "pass", "details": {}},
            {"name": "absolute_path_safety", "status": "pass", "details": {}},
            {"name": "repo_local_log_path", "status": "pass", "details": {}},
            {"name": "db_recovery_instructions", "status": "pass", "details": {}},
            {"name": "docker_postgres_dependency_warning", "status": "pass", "details": {}},
            {"name": "missing_status_idempotent", "status": "pass", "details": {}},
            {"name": "missing_uninstall_idempotent", "status": "pass", "details": {}},
        ],
    }


def test_autostart_smoke_payload_contract():
    autostart_smoke = _load_autostart_smoke()

    autostart_smoke.assert_autostart_smoke_payload(_valid_payload())


def test_autostart_smoke_fails_closed_on_missing_check():
    autostart_smoke = _load_autostart_smoke()
    payload = _valid_payload()
    payload["checks"] = [
        check for check in payload["checks"] if check["name"] != "absolute_path_safety"
    ]

    with pytest.raises(RuntimeError, match="absolute_path_safety"):
        autostart_smoke.assert_autostart_smoke_payload(payload)


def test_autostart_smoke_main_prints_json(capsys):
    autostart_smoke = _load_autostart_smoke()

    rc = autostart_smoke.main(["--format", "json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["ok"] is True
    assert payload["source"] == "db_free_autostart_contracts"


def test_task_wrapper_routes_autostart_smoke(monkeypatch):
    import scripts.task as task

    calls = []

    def python_script(script, *args):
        calls.append((script, args))

    monkeypatch.setattr(task, "python_script", python_script)

    assert task.main(["autostart-smoke"]) == 0
    assert calls == [("scripts/autostart_smoke.py", ())]
