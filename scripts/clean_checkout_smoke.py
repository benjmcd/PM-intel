"""Run a local clean-checkout smoke in a repo-owned git worktree."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKTREES_DIR = ROOT / "worktrees"
DEFAULT_WORKTREE = WORKTREES_DIR / "clean-smoke"
DEFAULT_REPORT_DIR = ROOT / "reports" / "clean-checkout"


@dataclass
class CommandResult:
    command: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str


def _display_command(command: list[str]) -> str:
    return " ".join(command)


def _resolve_inside_worktrees(path: Path, *, root: Path = ROOT) -> Path:
    worktrees_dir = (root / "worktrees").resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve(strict=False)
    if resolved == worktrees_dir or not resolved.is_relative_to(worktrees_dir):
        raise ValueError(f"worktree path must be inside {worktrees_dir}")
    return resolved


def _run(command: list[str], *, cwd: Path, timeout: int) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    return CommandResult(
        command=command,
        cwd=str(cwd),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def smoke_commands(*, run_verify: bool, db_verify: bool) -> list[list[str]]:
    commands = [
        ["git", "status", "--short", "--branch"],
        [sys.executable, "scripts/agent_context_check.py", "--quiet"],
        [sys.executable, "scripts/verify_workspace.py"],
        [sys.executable, "scripts/task.py", "review-pass"],
    ]
    if run_verify:
        commands.append([sys.executable, "scripts/verify.py"])
    if db_verify:
        commands.append([sys.executable, "scripts/db_local.py", "verify"])
    return commands


def write_report(payload: dict[str, object], report_dir: Path = DEFAULT_REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(payload["created_at"]).replace(":", "").replace("-", "")
    path = report_dir / f"clean-checkout-smoke-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_smoke(args: argparse.Namespace, *, root: Path = ROOT) -> tuple[dict[str, object], Path]:
    target = _resolve_inside_worktrees(Path(args.worktree_dir), root=root)
    if target.exists():
        raise FileExistsError(f"clean-checkout worktree already exists: {target}")

    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    results: list[CommandResult] = []
    cleanup_result: CommandResult | None = None
    created = False

    add_result = _run(["git", "worktree", "add", "--detach", str(target), args.ref], cwd=root, timeout=args.timeout)
    results.append(add_result)
    if add_result.returncode != 0:
        payload = _payload(args, created_at, target, results, cleanup_result, success=False)
        return payload, write_report(payload, Path(args.report_dir))
    created = True

    success = True
    for command in smoke_commands(run_verify=args.run_verify, db_verify=args.db_verify):
        result = _run(command, cwd=target, timeout=args.timeout)
        results.append(result)
        if result.returncode != 0:
            success = False
            break

    if created and not args.keep_worktree:
        cleanup_result = _run(["git", "worktree", "remove", str(target)], cwd=root, timeout=args.timeout)
        if cleanup_result.returncode != 0:
            success = False

    payload = _payload(args, created_at, target, results, cleanup_result, success=success)
    return payload, write_report(payload, Path(args.report_dir))


def _payload(
    args: argparse.Namespace,
    created_at: str,
    target: Path,
    results: list[CommandResult],
    cleanup_result: CommandResult | None,
    *,
    success: bool,
) -> dict[str, object]:
    return {
        "schema_version": "clean_checkout_smoke.v1",
        "created_at": created_at,
        "local_only": True,
        "validate_only": True,
        "ref": args.ref,
        "worktree_dir": str(target),
        "kept_worktree": bool(args.keep_worktree),
        "run_verify": bool(args.run_verify),
        "db_verify": bool(args.db_verify),
        "success": success,
        "commands": [asdict(result) for result in results],
        "cleanup": asdict(cleanup_result) if cleanup_result else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local clean-checkout smoke in a repo-owned worktree.")
    parser.add_argument("--ref", default="HEAD", help="Git ref to check out into the clean worktree.")
    parser.add_argument("--worktree-dir", type=Path, default=DEFAULT_WORKTREE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--run-verify", action="store_true", help="Run python scripts\\verify.py in the clean worktree.")
    parser.add_argument("--db-verify", action="store_true", help="Run python scripts\\db_local.py verify in the clean worktree.")
    parser.add_argument("--keep-worktree", action="store_true", help="Leave the clean worktree on disk after the smoke.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload, report_path = run_smoke(args)
    except (FileExistsError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"clean-checkout smoke failed before report: {exc}", file=sys.stderr)
        return 1

    print("PMFI clean-checkout smoke")
    print(f"Result: {'PASS' if payload['success'] else 'FAIL'}")
    print(f"Report: {report_path.relative_to(ROOT) if report_path.is_relative_to(ROOT) else report_path}")
    for result in payload["commands"]:
        assert isinstance(result, dict)
        print(f"- {_display_command(result['command'])}: {result['returncode']}")
    cleanup = payload.get("cleanup")
    if isinstance(cleanup, dict):
        print(f"- {_display_command(cleanup['command'])}: {cleanup['returncode']}")
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
