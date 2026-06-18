from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_setup_smoke():
    path = ROOT / "scripts" / "setup_smoke.py"
    spec = importlib.util.spec_from_file_location("setup_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload(**overrides):
    payload = {
        "ok": True,
        "status": "ready",
        "command": ["docker", "compose", "-f", "docker-compose.local.yml", "ps"],
        "command_string": "docker compose -f docker-compose.local.yml ps",
        "docker": {
            "available": True,
            "returncode": 0,
            "stdout": "postgres running\n",
            "stderr": "",
            "diagnostic": None,
        },
        "wsl": {"checked": False, "lines": []},
        "next_actions": [],
    }
    payload.update(overrides)
    return payload


def _blocked_payload(**overrides):
    payload = _payload(
        ok=False,
        status="blocked",
        docker={
            "available": True,
            "returncode": 1,
            "stdout": "",
            "stderr": "Docker Desktop is unable to start.",
            "diagnostic": {
                "title": "Docker Desktop is not ready for local Postgres.",
                "guidance": [
                    "Start Docker Desktop and wait until it reports the engine is running.",
                    "If startup fails, enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform.",
                    "If Docker Desktop requires sign-in, sign in locally before retrying.",
                    "Run `wsl -l -v` to confirm `docker-desktop` is running, then rerun `python scripts\\db_local.py up`.",
                    "If virtualization is still blocked, compare this machine with Docker Desktop system requirements.",
                ],
            },
        },
        wsl={
            "checked": True,
            "lines": ["WSL2 cannot start because virtualization is not enabled."],
        },
        next_actions=[
            "Start Docker Desktop and wait until it reports the engine is running.",
            "If startup fails, enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform.",
            "Run `wsl -l -v` to confirm `docker-desktop` is running, then rerun `python scripts\\db_local.py up`.",
        ],
    )
    payload.update(overrides)
    return payload


def _unavailable_payload(**overrides):
    payload = _payload(
        ok=False,
        status="unavailable",
        docker={
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "diagnostic": {
                "title": "docker.exe was not found.",
                "guidance": ["Install Docker Desktop for Windows."],
            },
        },
        next_actions=[
            "Install Docker Desktop for Windows or add docker.exe to PATH.",
            "Start Docker Desktop, complete any required sign-in, then rerun `python scripts\\db_local.py up`.",
        ],
    )
    payload.update(overrides)
    return payload


def _error_payload(**overrides):
    payload = _payload(
        ok=False,
        status="error",
        docker={
            "available": True,
            "returncode": 17,
            "stdout": "",
            "stderr": "compose file is invalid",
            "diagnostic": None,
        },
        next_actions=[],
    )
    payload.update(overrides)
    return payload


def _runner(setup_smoke, *, payload=None, stdout=None, returncode=0):
    calls = []

    def runner(args):
        calls.append(args)
        text = json.dumps(payload) if payload is not None else stdout
        return setup_smoke.CommandResult(args=args, returncode=returncode, stdout=text or "", stderr="")

    return calls, runner


def test_setup_smoke_accepts_ready_status():
    setup_smoke = _load_setup_smoke()
    calls, runner = _runner(setup_smoke, payload=_payload())

    payload = setup_smoke.run_setup_smoke(runner=runner)

    assert payload["status"] == "ready"
    assert calls == [[sys.executable, "scripts/db_local.py", "status", "--format", "json"]]


def test_setup_smoke_accepts_db_local_style_docker_desktop_blocked_status():
    setup_smoke = _load_setup_smoke()
    calls, runner = _runner(setup_smoke, payload=_blocked_payload())

    payload = setup_smoke.run_setup_smoke(runner=runner)

    assert payload["status"] == "blocked"
    assert calls == [[sys.executable, "scripts/db_local.py", "status", "--format", "json"]]


def test_setup_smoke_accepts_db_local_style_missing_docker_unavailable_status():
    setup_smoke = _load_setup_smoke()
    calls, runner = _runner(setup_smoke, payload=_unavailable_payload())

    payload = setup_smoke.run_setup_smoke(runner=runner)

    assert payload["status"] == "unavailable"
    assert calls == [[sys.executable, "scripts/db_local.py", "status", "--format", "json"]]


def test_setup_smoke_fails_closed_on_generic_error_status():
    setup_smoke = _load_setup_smoke()
    calls, runner = _runner(setup_smoke, payload=_error_payload())

    with pytest.raises(RuntimeError, match="status=error"):
        setup_smoke.run_setup_smoke(runner=runner)
    assert calls == [[sys.executable, "scripts/db_local.py", "status", "--format", "json"]]


def test_setup_smoke_main_defaults_to_text_output(monkeypatch, capsys):
    setup_smoke = _load_setup_smoke()

    monkeypatch.setattr(setup_smoke, "run_setup_smoke", lambda: _payload())

    assert setup_smoke.main([]) == 0

    captured = capsys.readouterr()
    assert captured.out == "setup-smoke passed: status=ready ok=true\n"
    assert captured.err == ""


def test_setup_smoke_main_emits_validated_json_payload(monkeypatch, capsys):
    setup_smoke = _load_setup_smoke()
    payload = _blocked_payload()

    monkeypatch.setattr(setup_smoke, "run_setup_smoke", lambda: payload)

    assert setup_smoke.main(["--format", "json"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert captured.err == ""


def test_setup_smoke_fails_closed_on_non_actionable_blocked_guidance():
    setup_smoke = _load_setup_smoke()
    payload = _blocked_payload(
        docker={
            "available": True,
            "returncode": 1,
            "stdout": "",
            "stderr": "Docker Desktop is unable to start.",
            "diagnostic": {
                "title": "Docker Desktop is not ready.",
                "guidance": ["Review the local setup logs."],
            },
        },
        wsl={"checked": True, "lines": ["Subsystem startup failed."]},
        next_actions=["Review diagnostics."],
    )
    _, runner = _runner(setup_smoke, payload=payload)

    with pytest.raises(RuntimeError, match="actionable Docker Desktop/Postgres/Windows virtualization guidance"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_non_actionable_unavailable_guidance():
    setup_smoke = _load_setup_smoke()
    payload = _unavailable_payload(
        docker={
            "available": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "diagnostic": {
                "title": "docker.exe was not found.",
                "guidance": ["Docker is unavailable."],
            },
        },
        next_actions=["Review local setup."],
    )
    _, runner = _runner(setup_smoke, payload=payload)

    with pytest.raises(RuntimeError, match="Docker Desktop install/PATH and retry/start guidance"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_nonzero_status_command():
    setup_smoke = _load_setup_smoke()
    _, runner = _runner(setup_smoke, payload=_payload(), returncode=2)

    with pytest.raises(RuntimeError, match="exited 2"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_empty_stdout():
    setup_smoke = _load_setup_smoke()
    _, runner = _runner(setup_smoke, stdout="")

    with pytest.raises(RuntimeError, match="empty stdout"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_unparsable_json():
    setup_smoke = _load_setup_smoke()
    _, runner = _runner(setup_smoke, stdout="{not-json")

    with pytest.raises(RuntimeError, match="unparsable JSON"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_malformed_blocked_diagnostic():
    setup_smoke = _load_setup_smoke()
    payload = _blocked_payload(docker={"available": True, "returncode": 1, "diagnostic": {"guidance": []}})
    _, runner = _runner(setup_smoke, payload=payload)

    with pytest.raises(RuntimeError, match="diagnostic.*title|diagnostic.*guidance"):
        setup_smoke.run_setup_smoke(runner=runner)


def test_setup_smoke_fails_closed_on_wsl_nul_bytes():
    setup_smoke = _load_setup_smoke()
    payload = _blocked_payload(wsl={"checked": True, "lines": ["Virtualization\x00disabled"]})
    _, runner = _runner(setup_smoke, payload=payload)

    with pytest.raises(RuntimeError, match="NUL bytes"):
        setup_smoke.run_setup_smoke(runner=runner)
