from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_fast_advance_supports_orthogonal_talmudic_anti_ceremony_decisions():
    text = read("FAST_ADVANCE.md").lower()
    assert "orthogonal" in text
    assert "talmudic" in text
    assert "ceremony" in text
    assert "material local product progress" in text or "material local results" in text
    assert "implementation" in text


def test_core_authority_docs_align_on_decision_style():
    for rel in [
        "AGENTS.md",
        "docs/governance/00_operating_model.md",
        "docs/governance/03_review_and_coherence_pass.md",
        "docs/implementation/06_adaptive_milestone_map.md",
        "plans/2026-06-03-bottom-up-implementation-plan.md",
    ]:
        text = read(rel).lower()
        assert "orthogonal" in text, rel
        assert "talmudic" in text, rel


def test_decisioning_policy_is_lightweight_not_a_gate():
    text = read("docs/governance/12_decision_methods.md").lower()
    assert "not a ceremony requirement" in text
    assert "do not use it for obvious" in text or "obvious one-file fixes" in text
    assert "consensus" in text
    assert "executable progress" in text


def test_task_graph_records_decisioning_policy():
    graph = yaml.safe_load(read("docs/implementation/02_task_graph.yaml"))
    policy = graph["advancement_policy"]
    assert policy["orthogonal_decisioning_for_unclear_work"] is True
    assert policy["talmudic_style_debate_for_nontrivial_decisions"] is True
    assert policy["anti_ceremony_material_results_over_process"] is True
