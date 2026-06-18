"""Validate local setup diagnostics without requiring Postgres to be ready."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], CommandResult]


def fail(message: str) -> NoReturn:
    raise RuntimeError(f"setup-smoke failed: {message}")


def run_command(args: list[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _non_empty_string_list(value: object) -> bool:
    return _string_list(value) and any(item.strip() for item in value)


def _combined_text(lines: list[str]) -> str:
    return "\n".join(lines).lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _has_nul(lines: list[str]) -> bool:
    return any("\x00" in line for line in lines)


def _require_blocked_actionable(
    diagnostic: dict[str, object],
    next_actions: list[str],
    wsl_lines: list[str],
) -> None:
    guidance = diagnostic.get("guidance")
    if not isinstance(guidance, list):
        fail("blocked docker diagnostic guidance is not a list")
    lines = [line for line in guidance if isinstance(line, str)]
    lines.extend(next_actions)
    lines.extend(wsl_lines)
    if _has_nul(lines):
        fail("blocked guidance contains NUL bytes")

    text = _combined_text(lines)
    has_docker_desktop_hint = "docker desktop" in text and _contains_any(text, ("start", "engine", "running"))
    has_postgres_retry_hint = (
        ("db_local.py" in text and _contains_any(text, ("rerun", "retry", "run", " up", " status")))
        or ("local postgres" in text and _contains_any(text, ("rerun", "retry", "start", "run")))
    )
    has_windows_virtualization_hint = _contains_any(
        text,
        ("virtualization", "wsl", "wsl2", "virtual machine platform", "bios/uefi"),
    )
    if not (has_docker_desktop_hint and has_postgres_retry_hint and has_windows_virtualization_hint):
        fail("blocked docker diagnostic lacks actionable Docker Desktop/Postgres/Windows virtualization guidance")


def _require_unavailable_actionable(docker: dict[str, object], next_actions: list[str]) -> None:
    diagnostic = docker.get("diagnostic")
    lines = list(next_actions)
    if isinstance(diagnostic, dict):
        guidance = diagnostic.get("guidance")
        if isinstance(guidance, list):
            lines.extend(line for line in guidance if isinstance(line, str))
    if _has_nul(lines):
        fail("unavailable guidance contains NUL bytes")

    text = _combined_text(lines)
    has_install_or_path_hint = (
        ("install" in text and "docker desktop" in text)
        or ("docker.exe" in text and "path" in text)
    )
    has_retry_or_start_hint = (
        ("start" in text and _contains_any(text, ("docker desktop", "docker")))
        or _contains_any(text, ("rerun", "retry"))
    )
    if not (has_install_or_path_hint and has_retry_or_start_hint):
        fail("unavailable docker diagnostic lacks Docker Desktop install/PATH and retry/start guidance")


def validate_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        fail("db_local status payload is not a JSON object")

    ok = payload.get("ok")
    status = payload.get("status")
    if not isinstance(ok, bool):
        fail("db_local status payload has non-boolean ok")
    if not _non_empty_string(status):
        fail("db_local status payload has missing status")

    command = payload.get("command")
    if not _non_empty_string_list(command):
        fail("db_local status payload has missing command list")
    if not _non_empty_string(payload.get("command_string")):
        fail("db_local status payload has missing command_string")

    docker = payload.get("docker")
    if not isinstance(docker, dict):
        fail("db_local status payload has missing docker object")

    next_actions = payload.get("next_actions")
    if not _string_list(next_actions):
        fail("db_local status payload has missing next_actions list")

    if ok:
        if status != "ready":
            fail(f"ok=true requires status=ready, got {status!r}")
        if docker.get("available") is not True:
            fail("ok=true requires docker available")
        if docker.get("returncode") != 0:
            fail("ok=true requires docker returncode 0")
    else:
        if status not in {"blocked", "unavailable", "error"}:
            fail(f"ok=false has unsupported status {status!r}")

    if status == "blocked" and docker.get("available") is not True:
        fail("status=blocked requires docker available")
    if status == "unavailable" and docker.get("available") is not False:
        fail("status=unavailable requires docker unavailable")

    if status in {"blocked", "unavailable"} and not _non_empty_string_list(next_actions):
        fail(f"status={status} requires next_actions guidance")
    if status == "error":
        fail("status=error is not an actionable setup diagnostic")

    if status == "blocked":
        diagnostic = docker.get("diagnostic")
        if not isinstance(diagnostic, dict):
            fail("blocked docker status has missing diagnostic")
        if not _non_empty_string(diagnostic.get("title")):
            fail("blocked docker diagnostic has missing title")
        if not _non_empty_string_list(diagnostic.get("guidance")):
            fail("blocked docker diagnostic has missing guidance")

    wsl = payload.get("wsl")
    wsl_lines: list[str] = []
    if wsl is not None:
        if not isinstance(wsl, dict):
            fail("wsl payload is not an object")
        lines = wsl.get("lines", [])
        if not _string_list(lines):
            fail("wsl lines are not a string list")
        if any("\x00" in line for line in lines):
            fail("wsl lines contain NUL bytes")
        wsl_lines = list(lines)

    if status == "blocked":
        diagnostic = docker.get("diagnostic")
        assert isinstance(diagnostic, dict)
        assert isinstance(next_actions, list)
        _require_blocked_actionable(diagnostic, next_actions, wsl_lines)
    if status == "unavailable":
        assert isinstance(next_actions, list)
        _require_unavailable_actionable(docker, next_actions)

    return payload


def run_setup_smoke(runner: Runner = run_command) -> dict[str, object]:
    args = [sys.executable, "scripts/db_local.py", "status", "--format", "json"]
    result = runner(args)
    if result.returncode != 0:
        fail(f"db_local status exited {result.returncode}")

    stdout = result.stdout.strip()
    if not stdout:
        fail("db_local status produced empty stdout")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        fail(f"db_local status produced unparsable JSON: {exc}")

    return validate_payload(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local setup diagnostics.")
    parser.add_argument("--format", choices=("json", "text"), default="text")
    args = parser.parse_args(argv)

    try:
        payload = run_setup_smoke()
    except RuntimeError as exc:
        if args.format == "json":
            print(json.dumps({"ok": False, "status": "error", "error": str(exc)}, sort_keys=True))
            return 1
        print(exc, file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(payload, sort_keys=True))
        return 0

    print(f"setup-smoke passed: status={payload['status']} ok={str(payload['ok']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
