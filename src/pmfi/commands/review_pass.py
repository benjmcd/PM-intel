from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FILES = [
    "AGENTS.md",
    "AGENT_START_HERE.md",
    "FAST_ADVANCE.md",
    "LOCAL_ONLY_SCOPE.md",
    "WORKLOG.md",
    "docs/governance/02_verification_cadence.md",
    "docs/governance/03_review_and_coherence_pass.md",
    "docs/governance/08_local_only_exclusion_policy.md",
    "docs/implementation/02_task_graph.yaml",
    "docs/implementation/06_adaptive_milestone_map.md",
    "docs/data/03_postgres_requirements.md",
    "docs/adr/0001-postgres-first.md",
    "docs/adr/0002-raw-before-derived.md",
    "docs/adr/0007-local-only-scope-and-exclusions.md",
    "scripts/verify.py",
    "scripts/db_local.py",
    "scripts/task.py",
    "scripts/publish_ready.py",
]

REQUIRED_CONSTRAINTS = {
    "local_only_scope": "local-only scope",
    "postgres_first_storage": "Postgres-first storage",
    "raw_evidence_lineage_before_derived_records": "raw lineage before derived records",
    "no_trading_or_order_placement": "no trading/order placement",
    "default_tests_make_no_live_api_calls": "no default live API calls",
}

REQUIRED_COMMANDS = {
    "python scripts\\verify.py": "verify",
    "python scripts\\db_local.py verify": "db verify",
    "python scripts\\task.py publish-ready --fetch": "publish-ready",
    "python scripts\\task.py health": "health",
    "python scripts\\task.py report --since 7d": "report",
    "python scripts\\task.py review-packet --since 24h": "review-packet",
}

FORBIDDEN_DEFAULT_VERIFY_MARKERS = {
    "PMFI_ENABLE_LIVE": "live-enable environment flag",
    "live-smoke": "live smoke command",
    'load_script("scripts/db_local.py")': "DB verification script load",
    "scripts/db_local.py\", \"verify": "DB verification subprocess",
    "scripts\\db_local.py\", \"verify": "DB verification subprocess",
    '"db-verify"': "DB verification CLI command",
    "'db-verify'": "DB verification CLI command",
}

REQUIRED_MILESTONES = {f"M{i}" for i in range(11)}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ReviewReport:
    ok: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [check.detail for check in self.checks if not check.ok]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "title": "PMFI review pass",
            "ok": self.ok,
            "result": "PASS" if self.ok else "FAIL",
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
        }


def _read_text(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8", errors="replace")


def _ok(name: str, detail: str) -> Check:
    return Check(name=name, ok=True, detail=detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name=name, ok=False, detail=detail)


def _load_task_graph(root: Path) -> tuple[dict[str, Any] | None, Check]:
    path = root / "docs/implementation/02_task_graph.yaml"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, _fail("task graph parses", "Missing docs/implementation/02_task_graph.yaml")
    except yaml.YAMLError as exc:
        return None, _fail("task graph parses", f"Task graph YAML is malformed: {exc.__class__.__name__}")

    if not isinstance(loaded, dict):
        return None, _fail("task graph parses", "Task graph YAML must parse to a mapping")
    return loaded, _ok("task graph parses", "Task graph YAML parsed")


def _check_required_files(root: Path) -> Check:
    missing = [rel for rel in REQUIRED_FILES if not (root / rel).is_file()]
    if missing:
        return _fail("required files exist", "Missing required files: " + ", ".join(missing))
    return _ok("required files exist", f"{len(REQUIRED_FILES)} required files present")


def _check_task_graph_posture(graph: dict[str, Any] | None) -> Check:
    if graph is None:
        return _fail("task graph posture", "Skipped because task graph did not parse")

    posture = graph.get("current_posture")
    if not isinstance(posture, dict):
        return _fail("task graph posture", "Task graph missing current_posture mapping")

    missing: list[str] = []
    for key in ("summary", "constraints_intact", "verified_proof", "next_recommended_focus", "residual_proof_gaps"):
        value = posture.get(key)
        if value in (None, "", []):
            missing.append(f"current_posture.{key}")

    if missing:
        return _fail("task graph posture", "Task graph missing required posture fields: " + ", ".join(missing))
    return _ok("task graph posture", "Current posture, next focus, residual gaps, and verified proof present")


def _check_required_constraints(graph: dict[str, Any] | None) -> Check:
    if graph is None:
        return _fail("required constraints", "Skipped because task graph did not parse")

    posture = graph.get("current_posture")
    constraints = posture.get("constraints_intact") if isinstance(posture, dict) else None
    if not isinstance(constraints, list):
        return _fail("required constraints", "current_posture.constraints_intact must be a list")

    present = {str(item) for item in constraints}
    missing = [label for key, label in REQUIRED_CONSTRAINTS.items() if key not in present]
    if missing:
        return _fail("required constraints", "Missing required constraints: " + ", ".join(missing))
    return _ok("required constraints", "Local-only, Postgres-first, raw-lineage, no-trading, and offline-default constraints present")


def _check_high_priority_commands(graph: dict[str, Any] | None) -> Check:
    if graph is None:
        return _fail("high-priority commands", "Skipped because task graph did not parse")

    rows = graph.get("high_priority_commands")
    if not isinstance(rows, list):
        return _fail("high-priority commands", "high_priority_commands must be a list")

    commands = {
        str(row.get("command"))
        for row in rows
        if isinstance(row, dict) and row.get("command")
    }
    missing = [label for command, label in REQUIRED_COMMANDS.items() if command not in commands]
    if missing:
        return _fail("high-priority commands", "Missing high-priority commands: " + ", ".join(missing))
    return _ok(
        "high-priority commands",
        "Required high-priority commands present: " + "; ".join(REQUIRED_COMMANDS),
    )


def _check_milestones(graph: dict[str, Any] | None) -> Check:
    if graph is None:
        return _fail("milestones M0-M10", "Skipped because task graph did not parse")

    rows = graph.get("milestones")
    if not isinstance(rows, list):
        return _fail("milestones M0-M10", "milestones must be a list")

    present = {
        str(row.get("id"))
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
    missing = sorted(REQUIRED_MILESTONES - present)
    if missing:
        return _fail("milestones M0-M10", "Missing milestones: " + ", ".join(missing))
    return _ok("milestones M0-M10", "Milestones M0-M10 present")


def _check_constraint_docs(root: Path) -> Check:
    docs = {
        "local-only": (
            [
                "AGENTS.md",
                "LOCAL_ONLY_SCOPE.md",
                "docs/governance/08_local_only_exclusion_policy.md",
            ],
            [["local-only"]],
        ),
        "Postgres-first": (
            [
                "AGENTS.md",
                "docs/data/03_postgres_requirements.md",
                "docs/adr/0001-postgres-first.md",
            ],
            [["postgres-first"], ["postgres"]],
        ),
        "raw lineage before derived records": (
            [
                "AGENTS.md",
                "docs/adr/0002-raw-before-derived.md",
                "docs/governance/00_operating_model.md",
            ],
            [["raw"], ["before"], ["derived", "normalized", "lineage"]],
        ),
        "no trading/order placement": (
            [
                "AGENTS.md",
                "docs/governance/05_stop_gates.md",
                "LOCAL_ONLY_SCOPE.md",
            ],
            [["trading"], ["order"], ["placement"]],
        ),
        "no default live API calls": (
            [
                "AGENTS.md",
                "docs/governance/02_verification_cadence.md",
                "docs/TESTING.md",
            ],
            [["default"], ["live"], ["api"], ["calls", "credentials", "network"]],
        ),
    }
    missing: list[str] = []
    for label, (rels, required_groups) in docs.items():
        combined = "\n".join(_read_text(root, rel).lower() for rel in rels if (root / rel).is_file())
        if not all(any(term in combined for term in group) for group in required_groups):
            missing.append(label)
    if missing:
        return _fail("constraint docs", "Constraint docs do not mention: " + ", ".join(missing))
    return _ok("constraint docs", "Durable docs mention required operating constraints")


def _check_v4_doc(root: Path) -> Check:
    text = _read_text(root, "docs/governance/02_verification_cadence.md")
    missing = [
        command
        for command in (
            "python scripts\\task.py review-pass",
            "python -m pmfi.cli review-pass",
        )
        if command not in text
    ]
    if missing:
        return _fail("V4 review-pass docs", "V4 docs missing: " + ", ".join(missing))
    return _ok("V4 review-pass docs", "V4 docs mention task-wrapper and module review-pass commands")


def _check_task_wrapper(root: Path) -> Check:
    text = _read_text(root, "scripts/task.py")
    required = {
        "review-pass": ['"review-pass"', 'module("pmfi.cli", "review-pass")'],
        "health": ['"health"', 'module("pmfi.cli", "health", *health_args)'],
        "report": ['"report"', 'module("pmfi.cli", "report", *report_args)'],
        "review-packet": ['"review-packet"', 'module("pmfi.cli", "alerts", "review-packet", *review_packet_args)'],
    }
    missing = [
        route
        for route, markers in required.items()
        if not all(marker in text for marker in markers)
    ]
    if missing:
        return _fail("task wrapper route", "scripts/task.py missing route(s): " + ", ".join(missing))
    return _ok("task wrapper route", "scripts/task.py routes review-pass and M10 operator commands to pmfi.cli")


def _check_default_verify_offline(root: Path) -> Check:
    text = _read_text(root, "scripts/verify.py")
    hits = [
        label
        for marker, label in FORBIDDEN_DEFAULT_VERIFY_MARKERS.items()
        if marker in text
    ]
    if hits:
        return _fail(
            "default verification stays offline",
            "scripts/verify.py contains default live/DB marker(s): " + ", ".join(hits),
        )
    return _ok(
        "default verification stays offline",
        "scripts/verify.py has no live-smoke, live-enable, or DB-verify execution markers",
    )


def _latest_worklog_slice(text: str) -> str:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("## ") and not line.startswith("## Format"):
            start = idx
            break
    if start is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## ") and not lines[idx].startswith("## Format"):
            end = idx
            break
    return "\n".join(lines[start:end])


def _check_latest_worklog(root: Path) -> Check:
    slice_text = _latest_worklog_slice(_read_text(root, "WORKLOG.md"))
    if not slice_text:
        return _fail("latest WORKLOG slice", "WORKLOG.md has no dated slice")

    lower = slice_text.lower()
    missing: list[str] = []
    if "### verification" not in lower:
        missing.append("Verification")
    if "### residual risk" not in lower or "next" not in lower:
        missing.append("Residual risk/next steps")

    if missing:
        heading = slice_text.splitlines()[0] if slice_text.splitlines() else "latest slice"
        return _fail("latest WORKLOG slice", f"{heading} missing sections: " + ", ".join(missing))
    return _ok("latest WORKLOG slice", "Latest WORKLOG slice has Verification and Residual risk/next steps")


def collect_report(root: Path | str) -> ReviewReport:
    root = Path(root)
    checks: list[Check] = [_check_required_files(root)]
    graph, parse_check = _load_task_graph(root)
    checks.append(parse_check)
    checks.extend(
        [
            _check_task_graph_posture(graph),
            _check_required_constraints(graph),
            _check_high_priority_commands(graph),
            _check_milestones(graph),
        ]
    )

    if checks[0].ok:
        checks.extend(
            [
                _check_constraint_docs(root),
                _check_v4_doc(root),
                _check_task_wrapper(root),
                _check_default_verify_offline(root),
                _check_latest_worklog(root),
            ]
        )

    return ReviewReport(ok=all(check.ok for check in checks), checks=checks)


def _sanitize_line(text: str) -> str:
    return "".join(ch if ch == "\n" or ord(ch) >= 32 else "?" for ch in text)


def format_text(report: ReviewReport) -> str:
    lines = [
        "PMFI review pass",
        f"Result: {'PASS' if report.ok else 'FAIL'}",
    ]
    for check in report.checks:
        status = "PASS" if check.ok else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.detail}")
    return _sanitize_line("\n".join(lines))


def cmd_review_pass(args: Any, *, root: Path | str | None = None) -> int:
    repo_root = Path(root) if root is not None else Path(__file__).resolve().parents[3]
    report = collect_report(repo_root)
    output_format = getattr(args, "format", "text")
    if output_format == "json":
        print(json.dumps(report.to_jsonable(), indent=2))
    else:
        print(format_text(report))
    return 0 if report.ok else 1
