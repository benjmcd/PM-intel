from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

CORE_DOCS = [
    "AGENTS.md",
    "AGENT_START_HERE.md",
    "FAST_ADVANCE.md",
    "LOCAL_ONLY_SCOPE.md",
    "README.md",
    "WINDOWS_START_HERE.md",
    "docs/implementation/06_adaptive_milestone_map.md",
    "docs/governance/12_decision_methods.md",
    "plans/2026-06-03-bottom-up-implementation-plan.md",
]

CANONICAL_REQUIREMENTS = {
    "windows": ["AGENTS.md", "README.md", "WINDOWS_START_HERE.md", "docs/implementation/06_adaptive_milestone_map.md"],
    "local-only": ["AGENTS.md", "FAST_ADVANCE.md", "LOCAL_ONLY_SCOPE.md", "plans/2026-06-03-bottom-up-implementation-plan.md"],
    "Postgres": ["AGENTS.md", "FAST_ADVANCE.md", "docs/implementation/06_adaptive_milestone_map.md", "plans/2026-06-03-bottom-up-implementation-plan.md"],
    "bottom-up": ["AGENTS.md", "FAST_ADVANCE.md", "docs/implementation/06_adaptive_milestone_map.md"],
    "top-down spike": ["AGENTS.md", "FAST_ADVANCE.md", "docs/implementation/06_adaptive_milestone_map.md"],
    "raw": ["AGENTS.md", "FAST_ADVANCE.md", "plans/2026-06-03-bottom-up-implementation-plan.md"],
    "orthogonal": ["AGENTS.md", "FAST_ADVANCE.md", "docs/governance/12_decision_methods.md", "docs/implementation/06_adaptive_milestone_map.md"],
    "Talmudic": ["AGENTS.md", "FAST_ADVANCE.md", "docs/governance/12_decision_methods.md"],
    "material results": ["AGENTS.md", "FAST_ADVANCE.md", "docs/governance/12_decision_methods.md"],
}

FORBIDDEN_GLOBAL_PHRASES = [
    "runtime " + "call" + "back",
    "localhost_http_" + "call" + "back",
    "make " + "verify",
    "make " + "db-",
    "./" + "scripts/",
    "ubuntu-" + "latest",
    "strictly " + "sequential",
]

FORBIDDEN_PATH_PARTS = {".github", "billing", "payments", "rbac", "oidc", "oauth", "kubernetes", "k8s", "helm", "terraform"}
FORBIDDEN_FILE_SUFFIXES = {"." + "sh"}
FORBIDDEN_FILENAMES = {"Make" + "file"}

SCOPE_EXCLUSION_PHRASES = [
    "billing",
    "hosted deployment",
    "registry image attestation",
    "automatic key rotation",
    "external secret-manager",
    "RBAC",
    "OIDC",
]

IMPLEMENTATION_PATHS = ["src", "sql", "config", "docker-compose.local.yml", ".codex/config.toml", ".claude/settings.json", "pyproject.toml"]
IMPLEMENTATION_FORBIDDEN = [
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
    "aws secrets manager",
    "azure key vault",
    "gcp secret manager",
    "hashicorp vault",
    "automatic key rotation",
]


_SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".venv", "reports"}


def _skip(p: Path) -> bool:
    parts = p.parts
    return any(part in _SKIP_PARTS or part.endswith(".egg-info") for part in parts)


def text_files() -> list[Path]:
    exts = {".md", ".py", ".toml", ".yaml", ".yml", ".json", ".sql", ".cmd", ".ps1", ".rules", ".txt", ".example"}
    return [
        p
        for p in ROOT.rglob("*")
        if p.is_file() and not _skip(p)
        and (p.suffix in exts or p.name == ".env.example")
    ]


def implementation_files() -> list[Path]:
    out: list[Path] = []
    for rel in IMPLEMENTATION_PATHS:
        p = ROOT / rel
        if p.is_file():
            out.append(p)
        elif p.exists():
            out.extend(q for q in p.rglob("*") if q.is_file() and not _skip(q))
    return out


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    errors: list[str] = []

    for rel in CORE_DOCS:
        if not (ROOT / rel).exists():
            fail(errors, f"missing core doc: {rel}")

    for needle, docs in CANONICAL_REQUIREMENTS.items():
        for rel in docs:
            path = ROOT / rel
            if path.exists() and needle.lower() not in path.read_text(encoding="utf-8", errors="ignore").lower():
                fail(errors, f"{rel} missing canonical concept: {needle}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or _skip(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if path.name in FORBIDDEN_FILENAMES or path.suffix in FORBIDDEN_FILE_SUFFIXES:
            fail(errors, f"forbidden non-Windows file present: {rel}")
        if parts & FORBIDDEN_PATH_PARTS:
            fail(errors, f"forbidden platform/SaaS scaffold path present: {rel}")

    ambiguous_receiver_term = r"\b" + "call" + r"backs?\b"
    disallowed_trigger_term = r"\b" + "ho" + r"oks?\b"
    for path in text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        lower = text.lower()
        rel = path.relative_to(ROOT).as_posix()
        for phrase in FORBIDDEN_GLOBAL_PHRASES:
            if phrase in lower:
                fail(errors, f"stale/forbidden phrase in {rel}: {phrase}")
        if re.search(ambiguous_receiver_term, lower):
            fail(errors, f"ambiguous local receiver terminology in {rel}")
        if re.search(disallowed_trigger_term, lower):
            fail(errors, f"disallowed automatic trigger terminology in {rel}")

    for rel in ["LOCAL_ONLY_SCOPE.md", "docs/governance/08_local_only_exclusion_policy.md", "docs/adr/0007-local-only-scope-and-exclusions.md"]:
        text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        for phrase in SCOPE_EXCLUSION_PHRASES:
            if phrase not in text:
                fail(errors, f"{rel} missing local-only exclusion phrase: {phrase}")

    for path in implementation_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        rel = path.relative_to(ROOT).as_posix()
        for marker in IMPLEMENTATION_FORBIDDEN:
            if marker in text:
                fail(errors, f"forbidden SaaS/hosted implementation marker in {rel}: {marker}")

    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    if settings != {}:
        fail(errors, ".claude/settings.json must remain empty in the no-automatic-command-trigger setup")

    graph = yaml.safe_load((ROOT / "docs/implementation/02_task_graph.yaml").read_text(encoding="utf-8"))
    if graph.get("execution_model") != "adaptive_bottom_up":
        fail(errors, "task graph must use adaptive_bottom_up execution_model")
    policy = graph.get("advancement_policy", {})
    if policy.get("allowed_top_down_spikes") is not True:
        fail(errors, "task graph must allow bounded top-down spikes")
    if graph.get("primary_environment") != "windows_local_directory":
        fail(errors, "task graph primary_environment must be windows_local_directory")
    if graph.get("primary_storage") != "postgres":
        fail(errors, "task graph primary_storage must be postgres")

    if policy.get("orthogonal_reasoning_allowed") is not True:
        fail(errors, "task graph must allow orthogonal reasoning for unclear decisions")
    if policy.get("compact_decision_debate_required_for_unclear_architecture") is not True:
        fail(errors, "task graph must require compact decision debate for unclear architecture")
    if policy.get("material_progress_over_ceremony") is not True:
        fail(errors, "task graph must prefer material progress over ceremony")
    if "decision_loop" not in policy:
        fail(errors, "task graph missing decision_loop policy")
    policy = graph.get("advancement_policy", {})
    if policy.get("orthogonal_decisioning_for_unclear_work") is not True:
        fail(errors, "task graph must require orthogonal decisioning for unclear work")
    if policy.get("talmudic_style_debate_for_nontrivial_decisions") is not True:
        fail(errors, "task graph must support Talmudic-style debate for nontrivial decisions")
    if policy.get("anti_ceremony_material_results_over_process") is not True:
        fail(errors, "task graph must favor material results over low-impact ceremony")

    app_cfg = yaml.safe_load((ROOT / "config/app.example.yaml").read_text(encoding="utf-8"))
    modes = set(app_cfg.get("alerts", {}).get("allowed_delivery_modes", []))
    if not modes <= {"console", "file", "localhost_http_receiver"}:
        fail(errors, f"unexpected alert delivery modes: {sorted(modes)}")
    features = app_cfg.get("features", {})
    for forbidden_key in ["enable_user_accounts", "enable_hosted_runtime", "enable_external_notification_services"]:
        if forbidden_key in features:
            fail(errors, f"unnecessary excluded feature flag remains in config: {forbidden_key}")

    if errors:
        for error in errors:
            print(f"consistency error: {error}", file=sys.stderr)
        return 1
    print("consistency audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
