from pathlib import Path


def test_governance_files_exist():
    root = Path(__file__).resolve().parents[1]
    required = [
        "AGENTS.md",
        "CLAUDE.md",
        "AGENT_START_HERE.md",
        "CODEX_START_HERE.md",
        "LOCAL_ONLY_SCOPE.md",
        "FAST_ADVANCE.md",
        ".agent/PLANS.md",
        ".codex/config.toml",
        ".claude/settings.json",
        "plans/2026-06-03-bottom-up-implementation-plan.md",
        "docs/governance/00_operating_model.md",
        "docs/governance/08_local_only_exclusion_policy.md",
        "docs/implementation/01_bottom_up_work_orders.md",
        "docs/implementation/06_adaptive_milestone_map.md",
        "docs/architecture/00_architecture_invariants.md",
        "docs/data/00_data_contracts.md",
        "docs/product/00_product_scope.md",
        "docs/adr/0007-local-only-scope-and-exclusions.md",
        "sql/001_init.sql",
    ]
    missing = [path for path in required if not (root / path).exists()]
    assert missing == []


def test_thin_agent_instruction_layer():
    root = Path(__file__).resolve().parents[1]
    assert (root / "CLAUDE.md").read_text(encoding="utf-8").splitlines()[0] == "@AGENTS.md"
    assert len((root / "AGENTS.md").read_text(encoding="utf-8").splitlines()) <= 220
    assert len((root / "CLAUDE.md").read_text(encoding="utf-8").splitlines()) <= 80


def test_skill_mirrors_match():
    root = Path(__file__).resolve().parents[1]
    canonical = sorted(p.parent.name for p in (root / ".agents/skills").glob("*/SKILL.md"))
    claude = sorted(p.parent.name for p in (root / ".claude/skills").glob("*/SKILL.md"))
    assert canonical == claude



def test_sql_partition_targets_are_coherent():
    import re

    root = Path(__file__).resolve().parents[1]
    sql_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / "sql").glob("*.sql")))
    created_tables = set(re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-zA-Z_][\w.]*)", sql_text, flags=re.I))
    partition_targets = re.findall(r"PARTITION\s+OF\s+([a-zA-Z_][\w.]*)", sql_text, flags=re.I)
    missing = [target for target in partition_targets if target not in created_tables]
    assert missing == []
    assert "rolling_market_metrics" not in sql_text
