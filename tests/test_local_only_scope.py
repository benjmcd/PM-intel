from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_only_policy_is_canonicalized():
    policy = (ROOT / "docs/governance/08_local_only_exclusion_policy.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    adr = (ROOT / "docs/adr/0007-local-only-scope-and-exclusions.md").read_text(encoding="utf-8")

    required = [
        "SaaS",
        "billing",
        "hosted deployment",
        "registry",
        "image signing",
        "automatic key rotation",
        "external secret-manager",
        "RBAC",
        "OIDC",
        "Cloud databases",
        "external-control-plane",
    ]
    lower_policy = policy.lower()
    for term in required:
        assert term.lower() in lower_policy
    assert "docs/governance/08_local_only_exclusion_policy.md" in agents
    assert "local-only" in adr.lower()


def test_no_external_platform_scaffold_paths():
    forbidden_parts = {
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
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if parts & forbidden_parts:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
