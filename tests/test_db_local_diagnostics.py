from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_db_local():
    path = ROOT / "scripts" / "db_local.py"
    spec = importlib.util.spec_from_file_location("db_local_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _docker_available(monkeypatch, db_local):
    monkeypatch.setattr(db_local.shutil, "which", lambda name: "C:/Program Files/Docker/Docker/resources/bin/docker.exe")


def _fake_docker_run(monkeypatch, db_local, *, returncode: int, stdout: str = "", stderr: str = ""):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return db_local.subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(db_local.subprocess, "run", fake_run)
    return calls


def _fake_process_run(monkeypatch, db_local, responses):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        key = tuple(args)
        returncode, stdout, stderr = responses[key]
        return db_local.subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(db_local.subprocess, "run", fake_run)
    return calls


def _nul_separated(text: str) -> str:
    return "\x00".join(text) + "\x00"


def test_missing_docker_exe_prints_windows_local_guidance(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(db_local.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit) as exc:
        db_local.run(["docker", "compose", "ps"])

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert "docker.exe was not found" in captured.err
    assert "Install Docker Desktop for Windows" in captured.err
    assert "python scripts\\db_local.py up" in captured.err


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.",
            "wsl -l -v",
        ),
        (
            "Docker Desktop is unable to start. Docker Desktop failed to initialize.",
            "Start Docker Desktop",
        ),
        (
            "Virtualization support not detected on this machine.",
            "enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform",
        ),
        (
            "request returned 500 Internal Server Error for Docker Desktop API route",
            "Docker Desktop system requirements",
        ),
    ],
)
def test_docker_desktop_startup_failures_print_actionable_guidance(monkeypatch, capsys, stderr, expected):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(monkeypatch, db_local, returncode=1, stderr=stderr)

    with pytest.raises(SystemExit) as exc:
        db_local.run(["docker", "compose", "up", "-d", "postgres"])

    captured = capsys.readouterr()
    assert exc.value.code == 1
    assert stderr in captured.err
    assert "diagnostic: Docker Desktop is not ready for local Postgres." in captured.err
    assert expected in captured.err
    assert "sign in" in captured.err
    assert "python scripts\\db_local.py up" in captured.err


def test_status_reports_known_docker_desktop_failure_even_without_check(monkeypatch, capsys):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(
        monkeypatch,
        db_local,
        returncode=1,
        stderr="Docker Desktop is unable to start.",
    )

    db_local.status()

    captured = capsys.readouterr()
    assert "docker compose -f docker-compose.local.yml ps" in captured.out
    assert "diagnostic: Docker Desktop is not ready for local Postgres." in captured.err
    assert "wsl -l -v" in captured.err


def test_docker_desktop_startup_diagnostic_includes_wsl_status_when_useful(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    calls = _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
            ("wsl.exe", "--status"): (
                1,
                "",
                "WSL2 cannot start because virtualization is not enabled.\n",
            ),
        },
    )

    db_local.status()

    captured = capsys.readouterr()
    assert ("wsl.exe", "--status") in [tuple(call[0]) for call in calls]
    assert "WSL status context (`wsl.exe --status`):" in captured.err
    assert "WSL2 cannot start because virtualization is not enabled." in captured.err


def test_docker_desktop_startup_diagnostic_reports_virtual_machine_platform_suggestion(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
            ("wsl.exe", "--status"): (
                1,
                "",
                "Virtual Machine Platform optional component should be enabled.\n"
                "Run: wsl.exe --install --no-distribution\n",
            ),
        },
    )

    db_local.status()

    captured = capsys.readouterr()
    assert "Virtual Machine Platform optional component should be enabled." in captured.err
    assert "wsl.exe --install --no-distribution" in captured.err


def test_docker_desktop_startup_diagnostic_sanitizes_nul_separated_wsl_status(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
            ("wsl.exe", "--status"): (
                1,
                _nul_separated("Default Distribution: Ubuntu\n"),
                _nul_separated("\n  \nKernel version: 5.15.90\n"),
            ),
        },
    )

    db_local.status()

    captured = capsys.readouterr()
    assert "- Default Distribution: Ubuntu" in captured.err
    assert "- Kernel version: 5.15.90" in captured.err
    assert "\x00" not in captured.err
    assert "- " not in {line for line in captured.err.splitlines()}


def test_docker_desktop_startup_diagnostic_omits_empty_wsl_status(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
            ("wsl.exe", "--status"): (1, "", " \n\t"),
        },
    )

    db_local.status()

    captured = capsys.readouterr()
    assert "diagnostic: Docker Desktop is not ready for local Postgres." in captured.err
    assert "WSL status context" not in captured.err


def test_docker_desktop_startup_diagnostic_skips_wsl_status_when_wsl_missing(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: None if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    calls = _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
        },
    )

    db_local.status()

    captured = capsys.readouterr()
    assert ("wsl.exe", "--status") not in [tuple(call[0]) for call in calls]
    assert "diagnostic: Docker Desktop is not ready for local Postgres." in captured.err
    assert "WSL status context" not in captured.err


def test_docker_desktop_startup_diagnostic_skips_wsl_status_on_timeout(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )

    def fake_run(args, **kwargs):
        if args == ["wsl.exe", "--status"]:
            raise db_local.subprocess.TimeoutExpired(args, timeout=5)
        return db_local.subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="Docker Desktop is unable to start.",
        )

    monkeypatch.setattr(db_local.subprocess, "run", fake_run)

    db_local.status()

    captured = capsys.readouterr()
    assert "diagnostic: Docker Desktop is not ready for local Postgres." in captured.err
    assert "WSL status context" not in captured.err


def test_generic_compose_failure_replays_raw_error_without_startup_diagnostic(monkeypatch, capsys):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(monkeypatch, db_local, returncode=17, stderr="compose file is invalid")

    with pytest.raises(SystemExit) as exc:
        db_local.run(["docker", "compose", "up", "-d", "postgres"])

    captured = capsys.readouterr()
    assert exc.value.code == 17
    assert "compose file is invalid" in captured.err
    assert "diagnostic:" not in captured.err


def test_successful_docker_command_replays_output_without_diagnostic(monkeypatch, capsys):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(monkeypatch, db_local, returncode=0, stdout="postgres running\n", stderr="ok\n")

    completed = db_local.run(["docker", "compose", "ps"])

    captured = capsys.readouterr()
    assert completed.returncode == 0
    assert "postgres running" in captured.out
    assert "ok" in captured.err
    assert "diagnostic:" not in captured.err


def test_status_json_reports_known_docker_desktop_failure_with_wsl_context(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(
        db_local.shutil,
        "which",
        lambda name: "C:/Windows/System32/wsl.exe" if name == "wsl.exe" else "C:/Docker/docker.exe",
    )
    _fake_process_run(
        monkeypatch,
        db_local,
        {
            ("docker", "compose", "-f", "docker-compose.local.yml", "ps"): (
                1,
                "",
                "Docker Desktop is unable to start.",
            ),
            ("wsl.exe", "--status"): (
                1,
                _nul_separated("Default Distribution: Ubuntu\n"),
                _nul_separated("WSL2 cannot start because virtualization is not enabled.\n"),
            ),
        },
    )

    assert db_local.main(["status", "--format", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["command"] == ["docker", "compose", "-f", "docker-compose.local.yml", "ps"]
    assert payload["docker"]["available"] is True
    assert payload["docker"]["returncode"] == 1
    assert payload["docker"]["stderr"] == "Docker Desktop is unable to start."
    assert payload["docker"]["diagnostic"]["title"] == "Docker Desktop is not ready for local Postgres."
    assert "Start Docker Desktop" in payload["next_actions"][0]
    assert payload["wsl"]["checked"] is True
    assert payload["wsl"]["lines"] == [
        "Default Distribution: Ubuntu",
        "WSL2 cannot start because virtualization is not enabled.",
    ]
    assert "\x00" not in captured.out


def test_status_json_reports_missing_docker_without_traceback(monkeypatch, capsys):
    db_local = _load_db_local()
    monkeypatch.setattr(db_local.shutil, "which", lambda name: None)

    assert db_local.main(["status", "--format", "json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["status"] == "unavailable"
    assert payload["docker"]["available"] is False
    assert payload["docker"]["returncode"] is None
    assert payload["docker"]["diagnostic"]["title"] == "docker.exe was not found."
    assert "Install Docker Desktop for Windows" in payload["next_actions"][0]
    assert payload["wsl"] == {"checked": False, "lines": []}


def test_status_json_reports_successful_docker_ps(monkeypatch, capsys):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(monkeypatch, db_local, returncode=0, stdout="postgres running\n", stderr="")

    db_local.status_json()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert payload["docker"]["available"] is True
    assert payload["docker"]["returncode"] == 0
    assert payload["docker"]["stdout"] == "postgres running\n"
    assert payload["docker"]["stderr"] == ""
    assert payload["docker"]["diagnostic"] is None
    assert payload["next_actions"] == []
    assert payload["wsl"] == {"checked": False, "lines": []}


def test_status_json_reports_generic_compose_failure_without_startup_diagnostic(monkeypatch, capsys):
    db_local = _load_db_local()
    _docker_available(monkeypatch, db_local)
    _fake_docker_run(monkeypatch, db_local, returncode=17, stdout="", stderr="compose file is invalid")

    db_local.status_json()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["status"] == "error"
    assert payload["docker"]["available"] is True
    assert payload["docker"]["returncode"] == 17
    assert payload["docker"]["stderr"] == "compose file is invalid"
    assert payload["docker"]["diagnostic"] is None
    assert payload["next_actions"] == []
    assert payload["wsl"] == {"checked": False, "lines": []}
