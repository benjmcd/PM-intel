"""Validate local publication readiness without publishing or writing artifacts."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ATTRIBUTION_PATTERNS = [
    re.compile(r"(?i)\bco-authored-by\s*:"),
    re.compile(r"(?i)\bgenerated\s+(with|by)\b"),
    re.compile(r"(?i)\bcreated\s+by\s+(claude|codex|chatgpt|openai)\b"),
    re.compile(r"(?i)\bclaude\s+code\b"),
]


@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class AttributionHit:
    source: str
    pattern: str
    line: str


@dataclass
class PublishReadyReport:
    ok: bool = False
    branch: str | None = None
    head: str | None = None
    upstream: str | None = None
    main_ref: str | None = None
    ahead: int | None = None
    behind: int | None = None
    dirty_entries: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    attribution_hits: list[AttributionHit] = field(default_factory=list)
    remote_freshness: str = "not checked; validate-only default avoids network fetch"
    failures: list[str] = field(default_factory=list)
    evidence: list[GitResult] = field(default_factory=list)


def _run_git(root: Path, *args: str) -> GitResult:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return GitResult(tuple(args), None, "", str(exc))
    return GitResult(tuple(args), completed.returncode, completed.stdout.rstrip(), completed.stderr.rstrip())


def _append(result: GitResult, report: PublishReadyReport) -> GitResult:
    report.evidence.append(result)
    return result


def _git_stdout(report: PublishReadyReport, root: Path, *args: str) -> str | None:
    result = _append(_run_git(root, *args), report)
    if result.returncode != 0:
        return None
    return result.stdout


def _scan_attribution(source: str, text: str) -> list[AttributionHit]:
    hits: list[AttributionHit] = []
    for line in text.splitlines():
        for pattern in ATTRIBUTION_PATTERNS:
            if pattern.search(line):
                hits.append(AttributionHit(source=source, pattern=pattern.pattern, line=line.strip()))
    return hits


def _parse_counts(raw: str | None) -> tuple[int | None, int | None]:
    if raw is None:
        return None, None
    parts = raw.split()
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None, None
    return int(parts[0]), int(parts[1])


def _main_ref_from_upstream(upstream: str | None) -> str | None:
    if not upstream:
        return None
    if "/" not in upstream:
        return "main"
    remote, _branch = upstream.split("/", 1)
    return f"{remote}/main"


def _remote_from_upstream(upstream: str | None) -> str | None:
    if not upstream or "/" not in upstream:
        return None
    remote, _branch = upstream.split("/", 1)
    return remote or None


def collect_report(root: Path = ROOT, *, fetch: bool = False) -> PublishReadyReport:
    report = PublishReadyReport()

    if _git_stdout(report, root, "rev-parse", "--is-inside-work-tree") != "true":
        report.failures.append("Not inside a Git worktree, or Git is unavailable.")
        return report

    report.branch = _git_stdout(report, root, "rev-parse", "--abbrev-ref", "HEAD")
    report.head = _git_stdout(report, root, "rev-parse", "HEAD")
    report.upstream = _git_stdout(report, root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    if not report.branch or report.branch == "HEAD":
        report.failures.append("HEAD is detached or current branch could not be resolved.")
    if not report.head:
        report.failures.append("HEAD commit could not be resolved.")
    if not report.upstream:
        report.failures.append("No upstream branch is configured for the current branch.")

    if fetch:
        remote = _remote_from_upstream(report.upstream)
        if not remote:
            report.failures.append("Cannot fetch remote freshness because upstream remote could not be resolved.")
            report.remote_freshness = "fetch requested but upstream remote was unresolved"
        else:
            fetch_result = _append(_run_git(root, "fetch", "--prune", remote), report)
            report.remote_freshness = f"checked with git fetch --prune {remote}"
            if fetch_result.returncode != 0:
                report.failures.append(f"Remote freshness check failed for {remote}.")

    status = _git_stdout(report, root, "status", "--porcelain=v1") or ""
    report.dirty_entries = status.splitlines()
    if report.dirty_entries:
        report.failures.append("Worktree is not clean; commit, stash, or intentionally exclude local changes first.")

    if report.upstream:
        behind, ahead = _parse_counts(_git_stdout(report, root, "rev-list", "--left-right", "--count", f"{report.upstream}...HEAD"))
        report.behind = behind
        report.ahead = ahead
        if behind is None or ahead is None:
            report.failures.append(f"Could not compute ahead/behind counts against {report.upstream}.")
        elif behind > 0:
            report.failures.append(f"HEAD is behind upstream {report.upstream} by {behind} commit(s).")

        upstream_ancestor = _append(_run_git(root, "merge-base", "--is-ancestor", report.upstream, "HEAD"), report)
        if upstream_ancestor.returncode != 0:
            report.failures.append(f"Upstream {report.upstream} is not an ancestor of HEAD.")

    report.main_ref = _main_ref_from_upstream(report.upstream)
    main_exists = False
    if report.main_ref:
        main_ref_result = _append(_run_git(root, "rev-parse", "--verify", "--quiet", report.main_ref), report)
        main_exists = main_ref_result.returncode == 0
        if not main_exists:
            report.failures.append(f"Main reference {report.main_ref} could not be resolved.")
        else:
            main_ancestor = _append(_run_git(root, "merge-base", "--is-ancestor", report.main_ref, "HEAD"), report)
            if main_ancestor.returncode != 0:
                report.failures.append(f"Main reference {report.main_ref} is not an ancestor of HEAD.")

    diff_base = report.main_ref if main_exists else report.upstream
    if diff_base:
        changed = _git_stdout(report, root, "diff", "--name-status", f"{diff_base}...HEAD") or ""
        report.changed_files = changed.splitlines()
        commit_text = _git_stdout(report, root, "log", "--format=%B", f"{diff_base}..HEAD") or ""
        diff_text = _git_stdout(report, root, "diff", "--no-ext-diff", "--unified=0", f"{diff_base}...HEAD") or ""
        report.attribution_hits.extend(_scan_attribution("commit", commit_text))
        report.attribution_hits.extend(_scan_attribution("diff", diff_text))

    if report.attribution_hits:
        report.failures.append("Attribution or generated footer string found in commit messages or diff.")

    report.ok = not report.failures
    return report


def _format_list(values: list[str], empty: str) -> list[str]:
    if not values:
        return [f"  {empty}"]
    return [f"  {value}" for value in values]


def render_report(report: PublishReadyReport) -> str:
    lines = [
        "PMFI publish readiness check",
        f"Result: {'PASS' if report.ok else 'FAIL'}",
        "Publication performed: no",
        "Artifacts written: no",
        "",
        "Git truth:",
        f"  branch: {report.branch or 'unresolved'}",
        f"  HEAD: {report.head or 'unresolved'}",
        f"  upstream: {report.upstream or 'unresolved'}",
        f"  main ref: {report.main_ref or 'unresolved'}",
        f"  ahead/behind upstream: ahead={report.ahead} behind={report.behind}",
        f"  remote freshness: {report.remote_freshness}",
        "",
        "Changed-file scope:",
    ]
    lines.extend(_format_list(report.changed_files, "none"))
    lines.extend(["", "Dirty entries:"])
    lines.extend(_format_list(report.dirty_entries, "none"))
    lines.extend(["", "Attribution/generated footer hits:"])
    if report.attribution_hits:
        for hit in report.attribution_hits:
            lines.append(f"  {hit.source}: {hit.line}")
    else:
        lines.append("  none")
    lines.extend(["", "Failures:"])
    lines.extend(_format_list(report.failures, "none"))
    lines.extend(["", "Evidence commands:"])
    for result in report.evidence:
        status = result.returncode if result.returncode is not None else "missing"
        lines.append(f"  git {' '.join(result.args)} -> {status}")
        if result.stderr:
            lines.append(f"    stderr: {result.stderr}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate local publish readiness without pushing or writing artifacts.")
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Opt in to git fetch --prune for fresh remote-tracking evidence before ancestry checks.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect_report(ROOT, fetch=args.fetch)
    print(render_report(report), end="")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
