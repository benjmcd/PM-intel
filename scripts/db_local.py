r"""Local Postgres helper for Windows + Docker Desktop.

This script intentionally uses Python subprocess calls instead of Unix wrappers.
It can initialize the local Docker Postgres instance without requiring a native
`psql` installation by streaming SQL into `docker compose exec`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.local.yml"]
SQL_FILES = [
    "sql/001_init.sql",
    "sql/002_partitions_indexes.sql",
    "sql/003_views_and_queries.sql",
    "sql/004_seed_dev.sql",
    "sql/005_add_watched_flag.sql",
    "sql/006_metric_windows_unique_constraint.sql",
    "sql/007_venue_trade_id_index.sql",
    "sql/008_market_baselines_unique_constraint.sql",
]
POSTGRES_PORT = "5433"
WSL_STATUS_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class DockerDiagnostic:
    title: str
    guidance: tuple[str, ...]


DOCKER_DESKTOP_STARTUP_DIAGNOSTIC = DockerDiagnostic(
    title="Docker Desktop is not ready for local Postgres.",
    guidance=(
        "Start Docker Desktop and wait until it reports the engine is running.",
        "If startup fails, enable BIOS/UEFI virtualization, WSL2, and Windows Virtual Machine Platform.",
        "If Docker Desktop requires sign-in, sign in locally before retrying.",
        "Run `wsl -l -v` to confirm `docker-desktop` is running, then rerun `python scripts\\db_local.py up`.",
        "If virtualization is still blocked, compare this machine with Docker Desktop system requirements.",
    ),
)

DOCKER_MISSING_DIAGNOSTIC = DockerDiagnostic(
    title="docker.exe was not found.",
    guidance=(
        "Install Docker Desktop for Windows or add docker.exe to PATH.",
        "Start Docker Desktop, complete any required sign-in, then rerun `python scripts\\db_local.py up`.",
    ),
)


def postgres_user() -> str:
    return os.environ.get("POSTGRES_USER", "pmfi")


def postgres_db() -> str:
    return os.environ.get("POSTGRES_DB", "pmfi")


def require_docker() -> None:
    if not shutil.which("docker"):
        emit_docker_diagnostic(DOCKER_MISSING_DIAGNOSTIC)
        raise SystemExit(1)


def classify_docker_failure(text: str) -> DockerDiagnostic | None:
    normalized = text.lower()
    signatures = (
        "dockerdesktoplinuxengine",
        "docker desktop is unable to start",
        "virtualization support not detected",
        "request returned 500 internal server error",
    )
    if any(signature in normalized for signature in signatures):
        return DOCKER_DESKTOP_STARTUP_DIAGNOSTIC
    return None


def wsl_status_lines(stdout: str, stderr: str) -> tuple[str, ...]:
    output = "\n".join(part.replace("\x00", "") for part in (stdout, stderr) if part).strip()
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def wsl_status_context() -> tuple[str, ...]:
    lines = collect_wsl_status_lines()
    if not lines:
        return ()
    return ("WSL status context (`wsl.exe --status`):", *(f"- {line}" for line in lines))


def collect_wsl_status_lines() -> tuple[str, ...]:
    if not shutil.which("wsl.exe"):
        return ()
    try:
        completed = subprocess.run(
            ["wsl.exe", "--status"],
            cwd=ROOT,
            text=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=WSL_STATUS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return wsl_status_lines(completed.stdout, completed.stderr)


def emit_docker_diagnostic(diagnostic: DockerDiagnostic, *, context: tuple[str, ...] = ()) -> None:
    print(f"diagnostic: {diagnostic.title}", file=sys.stderr)
    for item in diagnostic.guidance:
        print(f"- {item}", file=sys.stderr)
    for item in context:
        print(item, file=sys.stderr)


def replay_completed_output(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)


def run(args: list[str], *, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    require_docker()
    print("==", " ".join(args), "==", flush=True)
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        input=input_text,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    replay_completed_output(completed)
    if completed.returncode != 0:
        diagnostic = classify_docker_failure(f"{completed.stdout or ''}\n{completed.stderr or ''}")
        if diagnostic:
            context = wsl_status_context() if diagnostic is DOCKER_DESKTOP_STARTUP_DIAGNOSTIC else ()
            emit_docker_diagnostic(diagnostic, context=context)
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed


def compose(*args: str, input_text: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run([*COMPOSE, *args], input_text=input_text, check=check)


def up() -> None:
    compose("up", "-d", "postgres")
    wait()


def down() -> None:
    compose("down")


def wait(timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        completed = compose(
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-p",
            POSTGRES_PORT,
            "-U",
            postgres_user(),
            "-d",
            postgres_db(),
            check=False,
        )
        if completed.returncode == 0:
            print("Postgres is ready")
            return
        time.sleep(2)
    print("Postgres did not become ready before timeout", file=sys.stderr)
    raise SystemExit(1)


def psql_stdin(sql: str) -> None:
    compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-p",
        POSTGRES_PORT,
        "-U",
        postgres_user(),
        "-d",
        postgres_db(),
        "-v",
        "ON_ERROR_STOP=1",
        input_text=sql,
    )


def psql_command(sql: str) -> None:
    compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-p",
        POSTGRES_PORT,
        "-U",
        postgres_user(),
        "-d",
        postgres_db(),
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    )


def init() -> None:
    wait()
    for rel in SQL_FILES:
        path = ROOT / rel
        print(f"applying {rel}")
        psql_stdin(path.read_text(encoding="utf-8"))


def verify() -> None:
    wait()
    psql_command("select venue_code from pmfi.venues order by venue_code;")


def status() -> None:
    compose("ps", check=False)


def diagnostic_json(diagnostic: DockerDiagnostic | None) -> dict[str, object] | None:
    if diagnostic is None:
        return None
    return {
        "title": diagnostic.title,
        "guidance": list(diagnostic.guidance),
    }


def status_payload() -> dict[str, object]:
    command = [*COMPOSE, "ps"]
    docker_available = shutil.which("docker") is not None
    wsl: dict[str, object] = {"checked": False, "lines": []}
    if not docker_available:
        return {
            "ok": False,
            "status": "unavailable",
            "command": command,
            "command_string": " ".join(command),
            "docker": {
                "available": False,
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "diagnostic": diagnostic_json(DOCKER_MISSING_DIAGNOSTIC),
            },
            "wsl": wsl,
            "next_actions": list(DOCKER_MISSING_DIAGNOSTIC.guidance),
        }

    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return {
            "ok": False,
            "status": "error",
            "command": command,
            "command_string": " ".join(command),
            "docker": {
                "available": True,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
                "diagnostic": None,
            },
            "wsl": wsl,
            "next_actions": [],
        }

    diagnostic = classify_docker_failure(f"{completed.stdout or ''}\n{completed.stderr or ''}")
    if diagnostic is DOCKER_DESKTOP_STARTUP_DIAGNOSTIC:
        wsl = {
            "checked": shutil.which("wsl.exe") is not None,
            "lines": list(collect_wsl_status_lines()),
        }
    status_name = "ready" if completed.returncode == 0 else "blocked" if diagnostic else "error"
    return {
        "ok": completed.returncode == 0,
        "status": status_name,
        "command": command,
        "command_string": " ".join(command),
        "docker": {
            "available": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
            "diagnostic": diagnostic_json(diagnostic),
        },
        "wsl": wsl,
        "next_actions": list(diagnostic.guidance) if diagnostic else [],
    }


def status_json() -> None:
    print(json.dumps(status_payload(), indent=2))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["up"]:
        up(); return 0
    if argv == ["down"]:
        down(); return 0
    if argv == ["init"]:
        init(); return 0
    if argv == ["verify"]:
        verify(); return 0
    if argv == ["status"]:
        status(); return 0
    if argv == ["status", "--format", "json"]:
        status_json(); return 0
    if argv == ["status", "--format", "text"]:
        status(); return 0
    print("usage: python scripts\\db_local.py {up|down|init|verify|status [--format json|text]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
