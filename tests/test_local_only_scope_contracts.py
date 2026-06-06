from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SCOPE_FILES = [
    "LOCAL_ONLY_SCOPE.md",
    "AGENTS.md",
    "docs/governance/08_local_only_exclusion_policy.md",
    "docs/adr/0007-local-only-scope-and-exclusions.md",
    "docs/product/00_product_scope.md",
    "docs/SECURITY.md",
    "plans/2026-06-03-bottom-up-implementation-plan.md",
]

FORBIDDEN_PATH_PARTS = {
    ".github",
    "billing",
    "payments",
    "rbac",
    "oidc",
    "oauth",
    "kubernetes",
    "k8s",
    "helm",
    "terraform",
}

IMPLEMENTATION_SCAN_TARGETS = [
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


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="ignore")


_SKIP = {".git", ".pytest_cache", "__pycache__", ".venv"}


def _skip_path(p: Path) -> bool:
    return any(part in _SKIP or part.endswith(".egg-info") for part in p.parts)


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    if not path.exists():
        return
    for file in path.rglob("*"):
        if file.is_file() and not _skip_path(file):
            yield file


def test_local_only_scope_is_encoded_in_authoritative_files():
    for rel in REQUIRED_SCOPE_FILES:
        text = read(rel).lower()
        assert "local-only" in text or "local only" in text
        assert "billing" in text
        assert "hosted" in text
        assert "rbac" in text
        assert "oidc" in text


def test_no_external_platform_scaffold_paths():
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or _skip_path(path):
            continue
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if parts & FORBIDDEN_PATH_PARTS:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_saas_hosted_markers_do_not_appear_in_runtime_targets():
    violations: list[str] = []
    for rel in IMPLEMENTATION_SCAN_TARGETS:
        for file in iter_files(ROOT / rel):
            text = file.read_text(encoding="utf-8", errors="ignore").lower()
            for marker in FORBIDDEN_IMPLEMENTATION_MARKERS:
                if marker in text:
                    violations.append(f"{file.relative_to(ROOT).as_posix()}: {marker}")
    assert not violations, "Forbidden SaaS/hosted implementation markers found:\n" + "\n".join(violations)


def test_local_secret_handling_is_the_only_default_secret_pattern():
    security = read("docs/SECURITY.md").lower()
    assert "local untracked config" in security
    assert "external secret" in security
    agents = read("AGENTS.md").lower()
    assert "local secret handling" in agents


def test_no_hosted_ci_workflow_required_for_local_only_scope():
    assert not (ROOT / ".github" / "workflows" / "ci.yml").exists()


def test_runtime_config_does_not_scaffold_excluded_saas_features():
    text = (ROOT / "config" / "app.example.yaml").read_text(encoding="utf-8")
    forbidden_keys = [
        "enable_hosted_runtime",
        "enable_user_accounts",
        "enable_external_notification_services",
    ]
    assert not [key for key in forbidden_keys if key in text]

