"""Workspace self-check for agent-ready Windows local development.

This validates that the governance scaffold, config files, command wrappers,
and fixtures are parseable. It does not replace pytest or DB verification.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "AGENT_START_HERE.md",
    "CODEX_START_HERE.md",
    "LOCAL_ONLY_SCOPE.md",
    "FAST_ADVANCE.md",
    "WORKLOG.md",
    "README.md",
    "MANIFEST.md",
    "WINDOWS_START_HERE.md",
    "pmfi.cmd",
    "pmfi.ps1",
    "pyproject.toml",
    ".agent/PLANS.md",
    ".codex/config.toml",
    ".codex/rules/default.rules",
    ".claude/settings.json",
    ".claude/agents/security-reviewer.md",
    ".claude/agents/test-reviewer.md",
    ".claude/agents/architecture-reviewer.md",
    "docs/agentic_setup/00_dual_agent_operating_model.md",
    "docs/agentic_setup/01_context_bloat_policy.md",
    "docs/agentic_setup/02_codex_claude_handoff.md",
    "docs/agentic_setup/03_bottom_up_governance.md",
    "docs/governance/00_operating_model.md",
    "docs/governance/01_authority_hierarchy.md",
    "docs/governance/02_verification_cadence.md",
    "docs/governance/08_local_only_exclusion_policy.md",
    "docs/governance/11_current_alignment_review.md",
    "docs/governance/12_decision_methods.md",
    "docs/governance/09_agent_runtime_compatibility.md",
    "docs/implementation/01_bottom_up_work_orders.md",
    "docs/implementation/02_task_graph.yaml",
    "docs/implementation/06_adaptive_milestone_map.md",
    "docs/adr/0007-local-only-scope-and-exclusions.md",
    "docs/architecture/00_architecture_invariants.md",
    "docs/data/00_data_contracts.md",
    "docs/product/00_product_scope.md",
    "plans/2026-06-03-bottom-up-implementation-plan.md",
    "scripts/agent_context_check.py",
    "scripts/db_local.py",
    "scripts/task.py",
    "scripts/repo_status.py",
    "scripts/verify.py",
    "sql/001_init.sql",
    "config/app.example.yaml",
    "config/alert_rules.yaml",
    ".agents/skills/pmfi-implementation-governance/SKILL.md",
    ".agents/skills/pmfi-verification-pass/SKILL.md",
    ".agents/skills/pmfi-feature-plan/SKILL.md",
    ".agents/skills/pmfi-fast-advance/SKILL.md",
    ".agents/skills/who-built-this-before-me/SKILL.md",
    ".claude/skills/pmfi-implementation-governance/SKILL.md",
    ".claude/skills/pmfi-verification-pass/SKILL.md",
    ".claude/skills/pmfi-feature-plan/SKILL.md",
    ".claude/skills/pmfi-fast-advance/SKILL.md",
    ".claude/skills/who-built-this-before-me/SKILL.md",
]

MAX_ALWAYS_LOADED_LINES = {
    "AGENTS.md": 220,
    "CLAUDE.md": 80,
}

FORBIDDEN_FILE_SUFFIXES = ["." + "sh"]
FORBIDDEN_FILENAMES = ["Make" + "file"]
FORBIDDEN_TEXT = [
    "make " + "verify",
    "make " + "db-",
    "./" + "scripts/",
    "source " + ".venv",
    "ubuntu-" + "latest",
    "Make" + " target",
    "tools: Read, Grep, Glob, " + "Ba" + "sh",
]

FORBIDDEN_PLATFORM_PATH_PARTS = {
    ".github",
    "billing",
    "payments",
    "auth",
    "rbac",
    "oidc",
    "oauth",
    "kubernetes",
    "k8s",
    "helm",
    "terraform",
}

IMPLEMENTATION_TEXT_SCAN_TARGETS = [
    "src",
    "sql",
    "config",
    "docker-compose.local.yml",
    ".codex/config.toml",
    ".claude/settings.json",
    "pyproject.toml",
]

FORBIDDEN_IMPLEMENTATION_MARKERS = [
    "stripe",
    "hosted billing",
    "billing reconciliation",
    "tenant management",
    "deployment attestation",
    "registry image attestation",
    "registry push",
    "image signing",
    "cosign",
    "ghcr.io",
    "docker hub",
    "external secret manager",
    "aws secrets manager",
    "azure key vault",
    "gcp secret manager",
    "hashicorp vault",
    "automatic key rotation",
]


def parse_json(path: Path) -> None:
    json.loads(path.read_text(encoding="utf-8"))


def parse_yaml(path: Path) -> None:
    yaml.safe_load(path.read_text(encoding="utf-8"))


_SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".venv"}


def _skip(p: Path) -> bool:
    return any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in p.parts)


def text_files() -> list[Path]:
    exts = {".md", ".py", ".toml", ".yaml", ".yml", ".json", ".sql", ".cmd", ".ps1", ".example", ".rules", ".txt"}
    return [p for p in ROOT.rglob("*") if p.is_file() and not _skip(p) and (p.suffix in exts or p.name == ".env.example")]


def iter_scan_target_files() -> list[Path]:
    files: list[Path] = []
    for rel in IMPLEMENTATION_TEXT_SCAN_TARGETS:
        path = ROOT / rel
        if path.is_file():
            files.append(path)
        elif path.exists():
            files.extend(
                p for p in path.rglob("*")
                if p.is_file() and not any(part.endswith(".egg-info") for part in p.parts)
            )
    return files



def check_sql_consistency() -> list[str]:
    import re

    sql_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in sorted((ROOT / "sql").glob("*.sql")))
    created_tables = set(re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][\w.]*)", sql_text, flags=re.I))
    partition_targets = re.findall(r"PARTITION\s+OF\s+([a-zA-Z_][\w.]*)", sql_text, flags=re.I)
    index_targets = re.findall(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+([a-zA-Z_][\w.]*)", sql_text, flags=re.I)
    errors: list[str] = []
    for target in partition_targets:
        if target not in created_tables:
            errors.append(f"SQL partition target has no parent table: {target}")
    for target in index_targets:
        if target not in created_tables:
            errors.append(f"SQL index target has no table: {target}")
    docs = (ROOT / "docs" / "data" / "03_postgres_requirements.md").read_text(encoding="utf-8", errors="ignore")
    if "rolling_market_metrics" in docs or "rolling_market_metrics" in sql_text:
        errors.append("stale rolling_market_metrics table name remains; use metric_windows")
    if "metric_windows" not in docs:
        errors.append("Postgres requirements doc must list metric_windows")
    return errors

def main() -> int:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    if missing:
        print("missing required files:")
        for path in missing:
            print(f"- {path}")
        return 1

    forbidden_files: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or _skip(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if path.name in FORBIDDEN_FILENAMES or any(path.name.endswith(suffix) for suffix in FORBIDDEN_FILE_SUFFIXES):
            forbidden_files.append(rel)
        if parts & FORBIDDEN_PLATFORM_PATH_PARTS:
            forbidden_files.append(rel)
    if forbidden_files:
        print("forbidden files or platform-scaffold paths present:")
        for rel in sorted(set(forbidden_files)):
            print(f"- {rel}")
        return 1

    for path in text_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in FORBIDDEN_TEXT:
            if needle in text:
                print(f"forbidden legacy command reference in {rel}: {needle}")
                return 1

    implementation_violations: list[str] = []
    for path in iter_scan_target_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in FORBIDDEN_IMPLEMENTATION_MARKERS:
            if marker in text:
                implementation_violations.append(f"{path.relative_to(ROOT).as_posix()}: {marker}")
    if implementation_violations:
        print("forbidden hosted/SaaS implementation markers present:")
        for violation in implementation_violations:
            print(f"- {violation}")
        return 1

    required_scope_phrases = [
        "local-only",
        "billing",
        "hosted deployment",
        "registry image attestation",
        "automatic key rotation",
        "external secret-manager",
        "RBAC",
        "OIDC",
    ]
    for rel in ["LOCAL_ONLY_SCOPE.md", "docs/governance/08_local_only_exclusion_policy.md", "docs/adr/0007-local-only-scope-and-exclusions.md"]:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        for phrase in required_scope_phrases:
            if phrase not in text:
                print(f"{rel} missing required local-only phrase: {phrase}")
                return 1

    if (ROOT / ".github").exists():
        print(".github workflow directory is not part of the local-only current phase")
        return 1

    for rel, max_lines in MAX_ALWAYS_LOADED_LINES.items():
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            print(f"{rel} is too long: {len(lines)} lines > {max_lines}")
            return 1

    claude_first_line = (ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()[0].strip()
    if claude_first_line != "@AGENTS.md":
        print("CLAUDE.md must start with @AGENTS.md")
        return 1

    for path in (ROOT / "config").glob("*.yaml"):
        parse_yaml(path)
    parse_yaml(ROOT / "docker-compose.local.yml")
    parse_yaml(ROOT / "docs/implementation/02_task_graph.yaml")
    parse_json(ROOT / ".claude/settings.json")
    tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    for path in (ROOT / "tests/fixtures/raw").glob("*.json"):
        parse_json(path)

    governance_numbers: dict[str, list[str]] = {}
    for gov in (ROOT / "docs" / "governance").glob("[0-9][0-9]_*.md"):
        prefix = gov.name[:2]
        governance_numbers.setdefault(prefix, []).append(gov.name)
    duplicate_governance_numbers = {k: v for k, v in governance_numbers.items() if len(v) > 1}
    if duplicate_governance_numbers:
        print("duplicate governance document numbers:")
        for prefix, names in sorted(duplicate_governance_numbers.items()):
            print(f"- {prefix}: {', '.join(sorted(names))}")
        return 1

    canonical = sorted(p.parent.name for p in (ROOT / ".agents/skills").glob("*/SKILL.md"))
    claude = sorted(p.parent.name for p in (ROOT / ".claude/skills").glob("*/SKILL.md"))
    if canonical != claude:
        print("Claude skill mirror mismatch")
        print("canonical:", canonical)
        print("claude:", claude)
        return 1

    sql_errors = check_sql_consistency()
    if sql_errors:
        print("SQL consistency errors:")
        for error in sql_errors:
            print(f"- {error}")
        return 1

    print("workspace self-check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
