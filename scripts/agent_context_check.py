from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_ALWAYS_LOADED_BYTES = 32 * 1024
MAX_AGENTS_LINES = 220

REQUIRED = [
    "AGENTS.md",
    "LOCAL_ONLY_SCOPE.md",
    "FAST_ADVANCE.md",
    "CLAUDE.md",
    "AGENT_START_HERE.md",
    "CODEX_START_HERE.md",
    ".agent/PLANS.md",
    "plans/2026-06-03-bottom-up-implementation-plan.md",
    "plans/2026-06-03-bottom-up-implementation-plan.md",
    "docs/ARCHITECTURE.md",
    "docs/TESTING.md",
    "docs/SECURITY.md",
    "scripts/verify.py",
    "scripts/task.py",
    "scripts/repo_status.py",
    "scripts/agent_context_check.py",
    ".codex/config.toml",
    ".codex/rules/default.rules",
    ".claude/settings.json",
]

AVOID_BY_DEFAULT = [
    "CONTEXT.md",
    "RULES.md",
    "DEVELOPMENT.md",
    "CODING_STANDARDS.md",
    "AI_NOTES.md",
    "PROMPTS.md",
    "SYSTEM_OVERVIEW.md",
    "PROJECT_STATE.md",
    "TASKS.md",
    "Make" + "file",
]


def fail(msg: str, errors: list[str]) -> None:
    errors.append(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    errors: list[str] = []
    warnings: list[str] = []

    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            fail(f"missing required file: {rel}", errors)

    agents = ROOT / "AGENTS.md"
    claude = ROOT / "CLAUDE.md"
    if agents.exists():
        text = agents.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > MAX_ALWAYS_LOADED_BYTES:
            fail("AGENTS.md exceeds 32 KiB; move detail into docs/plans/skills", errors)
        if len(text.splitlines()) > MAX_AGENTS_LINES:
            fail(f"AGENTS.md exceeds {MAX_AGENTS_LINES} lines; keep always-loaded guidance short", errors)
        for required_phrase in ["Postgres", "bottom-up", "FAST_ADVANCE.md", "python scripts\\verify.py", "raw external payloads", "Windows local directory", "local-only"]:
            if required_phrase not in text:
                fail(f"AGENTS.md missing required phrase: {required_phrase}", errors)

    if claude.exists():
        text = claude.read_text(encoding="utf-8")
        if "@AGENTS.md" not in text:
            fail("CLAUDE.md must import AGENTS.md", errors)
        if len(text.splitlines()) > 80:
            fail("CLAUDE.md should remain a thin adapter; it is too long", errors)

    for rel in AVOID_BY_DEFAULT:
        if (ROOT / rel).exists():
            msg = f"avoid broad or non-native context/control file unless justified: {rel}"
            if args.strict:
                fail(msg, errors)
            else:
                warnings.append(msg)

    for rel in [".codex/config.toml", "pyproject.toml"]:
        path = ROOT / rel
        if path.exists():
            try:
                tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                fail(f"cannot parse {rel}: {exc}", errors)

    path = ROOT / ".claude/settings.json"
    if path.exists():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            fail(f"cannot parse .claude/settings.json: {exc}", errors)

    if warnings and not args.quiet:
        for warning in warnings:
            print(f"warning: {warning}")

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("agent context check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
