"""Create a local handoff snapshot without publishing anything."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "handoff"
MAX_EXCERPT_CHARS = 1800
MAX_STATUS_CHARS = 5000

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|api[_-]?key|token|secret)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(postgres(?:ql)?://)([^:@/\s]+):([^@/\s]+)@"),
]


@dataclass
class CommandResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    skipped: bool = False
    reason: str | None = None


def _display_command(args: list[str]) -> str:
    return " ".join(args)


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if "postgres" in pattern.pattern:
            redacted = pattern.sub(r"\1\2:***@", redacted)
        else:
            redacted = pattern.sub(r"\1\2***", redacted)
    return redacted


def redact_db_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return "***"
    if not parts.scheme or not parts.netloc:
        return _redact_text(value)
    try:
        hostname = parts.hostname or ""
        port_value = parts.port
        username = parts.username
        password = parts.password
    except ValueError:
        return _redact_text(value)
    port = f":{port_value}" if port_value is not None else ""
    host = hostname
    if username:
        userinfo = username
        if password is not None:
            userinfo += ":***"
        host = f"{userinfo}@{host}"
    return urlunsplit((parts.scheme, f"{host}{port}", parts.path, parts.query, parts.fragment))


def run_command(args: list[str], *, timeout: int = 30, root: Path = ROOT) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return CommandResult(args, None, "", _redact_text(str(exc)), reason="command_not_found")
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            args,
            None,
            _redact_text(exc.stdout or ""),
            _redact_text(exc.stderr or ""),
            reason=f"timeout_after_{timeout}s",
        )
    return CommandResult(
        args,
        completed.returncode,
        _redact_text(completed.stdout or ""),
        _redact_text(completed.stderr or ""),
    )


def _git_stdout(*args: str, root: Path = ROOT) -> str | None:
    result = run_command(["git", *args], root=root, timeout=15)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def collect_git(root: Path = ROOT) -> dict[str, object]:
    branch = _git_stdout("rev-parse", "--abbrev-ref", "HEAD", root=root)
    head = _git_stdout("rev-parse", "HEAD", root=root)
    upstream = _git_stdout("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", root=root)
    ahead = behind = None
    if upstream:
        counts = _git_stdout("rev-list", "--left-right", "--count", f"{upstream}...HEAD", root=root)
        if counts:
            parts = counts.split()
            if len(parts) == 2 and all(part.isdigit() for part in parts):
                behind = int(parts[0])
                ahead = int(parts[1])
    porcelain = _git_stdout("status", "--porcelain=v1", root=root) or ""
    recent = _git_stdout("log", "--oneline", "--decorate=short", "-n", "10", root=root) or ""
    return {
        "branch": branch,
        "head": head,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(porcelain.strip()),
        "dirty_entries": porcelain.splitlines(),
        "recent_commits": recent.splitlines(),
    }


def latest_worklog_entry(root: Path = ROOT) -> dict[str, str | None]:
    path = root / "WORKLOG.md"
    if not path.exists():
        return {"heading": None, "excerpt": None}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    heading_index = None
    for index, line in enumerate(lines):
        if lines[index].startswith("## "):
            heading_index = index
            break
    if heading_index is None:
        text = "\n".join(lines).strip()
        return {"heading": None, "excerpt": text[:MAX_EXCERPT_CHARS]}
    next_heading_index = None
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            next_heading_index = index
            break
    heading = lines[heading_index].removeprefix("## ").strip()
    body_lines = lines[heading_index + 1 : next_heading_index]
    body = "\n".join(body_lines).strip()
    return {"heading": heading, "excerpt": body[:MAX_EXCERPT_CHARS]}


def collect_status(root: Path = ROOT) -> dict[str, object]:
    result = run_command([sys.executable, "scripts/repo_status.py"], root=root, timeout=30)
    output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
    return {
        "command": _display_command(result.command),
        "returncode": result.returncode,
        "excerpt": output[:MAX_STATUS_CHARS],
    }


def skipped_result(command: list[str], reason: str) -> CommandResult:
    return CommandResult(command, None, "", "", skipped=True, reason=reason)


def collect_verification(args: argparse.Namespace, root: Path = ROOT) -> dict[str, object]:
    commands = [
        "python scripts\\verify.py",
        "python scripts\\db_local.py verify",
        "python scripts\\task.py fixture-replay",
    ]
    db_command = [sys.executable, "scripts/db_local.py", "verify"]
    verify_command = [sys.executable, "scripts/verify.py"]
    db_result = (
        run_command(db_command, root=root, timeout=args.db_timeout)
        if args.db_verify
        else skipped_result(db_command, "use --db-verify to attempt local Postgres readiness")
    )
    verify_result = (
        run_command(verify_command, root=root, timeout=args.verify_timeout)
        if args.run_verify
        else skipped_result(verify_command, "use --run-verify to run the default gate")
    )
    return {
        "recommended_commands": commands,
        "db_verify": asdict(db_result),
        "default_verify": asdict(verify_result),
    }


def collect_snapshot(args: argparse.Namespace, root: Path = ROOT) -> dict[str, object]:
    now = datetime.now(UTC)
    db_url = redact_db_url(os.environ.get("PMFI_DB_URL"))
    return {
        "schema_version": 1,
        "created_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "local_only": True,
        "publication_performed": False,
        "git": collect_git(root),
        "worklog": latest_worklog_entry(root),
        "status": collect_status(root),
        "runtime": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "environment": {
            "pmfi_db_url": db_url if db_url else "not_set",
            "note": "No environment dump is included; secret-bearing values are intentionally excluded.",
        },
        "verification": collect_verification(args, root),
    }


def _md_block(text: str | None) -> str:
    if not text:
        return "_none_"
    return "```text\n" + text.rstrip() + "\n```"


def render_markdown(snapshot: dict[str, object]) -> str:
    git = snapshot["git"]
    worklog = snapshot["worklog"]
    status = snapshot["status"]
    verification = snapshot["verification"]
    assert isinstance(git, dict)
    assert isinstance(worklog, dict)
    assert isinstance(status, dict)
    assert isinstance(verification, dict)
    db_verify = verification["db_verify"]
    default_verify = verification["default_verify"]
    assert isinstance(db_verify, dict)
    assert isinstance(default_verify, dict)

    lines = [
        "# PMFI Local Handoff Snapshot",
        "",
        f"- Created: {snapshot['created_at']}",
        "- Local-only snapshot: yes",
        "- Publication performed: no",
        "",
        "## Git",
        f"- Branch: {git.get('branch')}",
        f"- HEAD: {git.get('head')}",
        f"- Upstream: {git.get('upstream') or 'none'}",
        f"- Ahead/behind upstream: ahead={git.get('ahead')} behind={git.get('behind')}",
        f"- Worktree dirty: {git.get('dirty')}",
        "",
        "### Dirty entries",
        _md_block("\n".join(git.get("dirty_entries") or [])),
        "",
        "### Recent commits",
        _md_block("\n".join(git.get("recent_commits") or [])),
        "",
        "## Latest WORKLOG Entry",
        f"- Heading: {worklog.get('heading') or 'none'}",
        _md_block(str(worklog.get("excerpt") or "")),
        "",
        "## Task Status",
        f"- Command: `{status.get('command')}`",
        f"- Return code: {status.get('returncode')}",
        _md_block(str(status.get("excerpt") or "")),
        "",
        "## Runtime",
        f"- Python: {snapshot['runtime']['python']}",
        f"- Executable: {snapshot['runtime']['executable']}",
        f"- Platform: {snapshot['runtime']['platform']}",
        "",
        "## Verification",
        "Recommended commands:",
    ]
    for command in verification["recommended_commands"]:
        lines.append(f"- `{command}`")
    lines.extend(
        [
            "",
            f"- DB verify: skipped={db_verify.get('skipped')} returncode={db_verify.get('returncode')} reason={db_verify.get('reason')}",
            _md_block((db_verify.get("stdout") or "") + (db_verify.get("stderr") or "")),
            f"- Default verify: skipped={default_verify.get('skipped')} returncode={default_verify.get('returncode')} reason={default_verify.get('reason')}",
            _md_block((default_verify.get("stdout") or "") + (default_verify.get("stderr") or "")),
            "",
            "## Secret Handling",
            "- Environment variables were not dumped.",
            "- PMFI_DB_URL is reported only as not_set or with credentials redacted.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_snapshot(snapshot: dict[str, object], output_dir: Path = DEFAULT_OUTPUT_DIR) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(snapshot["created_at"]).replace(":", "").replace("-", "")
    stem = f"handoff-{stamp}"
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(snapshot), encoding="utf-8")
    return json_path, md_path


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a local PMFI handoff snapshot.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    db_group = parser.add_mutually_exclusive_group()
    db_group.add_argument("--db-verify", action="store_true", help="Attempt scripts\\db_local.py verify.")
    db_group.add_argument("--no-db-verify", action="store_true", help="Skip DB verification explicitly.")
    parser.add_argument("--run-verify", action="store_true", help="Run scripts\\verify.py and record the result.")
    parser.add_argument("--db-timeout", type=int, default=90)
    parser.add_argument("--verify-timeout", type=int, default=600)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = collect_snapshot(args)
    json_path, md_path = write_snapshot(snapshot, args.output_dir)
    print("PMFI local handoff snapshot written:")
    print(f"- {display_path(json_path)}")
    print(f"- {display_path(md_path)}")
    if not args.db_verify:
        print("DB readiness: skipped (use --db-verify to attempt local Postgres verification)")
    if not args.run_verify:
        print("Default verification: skipped (use --run-verify to run scripts\\verify.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
