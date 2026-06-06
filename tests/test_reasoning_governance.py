from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_fast_advance_supports_orthogonal_reasoning_and_talmudic_debate():
    lower = read("FAST_ADVANCE.md").lower()
    assert "orthogonal" in lower
    assert "talmudic debate" in lower
    assert "material results" in lower
    assert "ceremony" in lower
    assert "docs/governance/12_decision_methods.md" in lower
    assert "proof" in lower or "payback" in lower


def test_core_agent_contract_references_reasoning_protocol_without_making_it_ceremony():
    lower = read("AGENTS.md").lower()
    assert "orthogonal" in lower
    assert "talmudic debate" in lower
    assert "material results" in lower
    assert "not a paperwork requirement" in lower or "not ceremony" in lower or "only when it improves" in lower
    assert "obvious" in lower


def test_reasoning_governance_is_compact_and_decision_oriented():
    text = read("docs/governance/12_decision_methods.md")
    lower = text.lower()
    for phrase in [
        "orthogonal approach",
        "talmudic debate method",
        "material-results rule",
        "question:",
        "option a / strongest case:",
        "objection / failure mode:",
        "consensus for this repo state:",
        "payback artifact:",
        "next command/check:",
    ]:
        assert phrase in lower
    assert len(text.splitlines()) < 100


def test_adaptive_milestone_map_contains_decision_acceleration_rule():
    text = read("docs/implementation/06_adaptive_milestone_map.md").lower()
    assert "decision acceleration rule" in text
    assert "orthogonal approach" in text
    assert "talmudic debate" in text
    assert "executable evidence" in text
    assert "docs/governance/12_decision_methods.md" in text
