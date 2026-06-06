from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fast_advance_contract_exists_and_is_permissive():
    text = (ROOT / "FAST_ADVANCE.md").read_text(encoding="utf-8").lower()
    assert "bottom-up" in text
    assert "not a rigid lock" in text or "not a rigid" in text
    assert "top-down spike" in text
    assert "local-only" in text
    assert "weaken" in text


def test_adaptive_milestone_map_supersedes_rigid_order():
    text = (ROOT / "docs" / "implementation" / "06_adaptive_milestone_map.md").read_text(encoding="utf-8").lower()
    assert "supersedes" in text
    assert "rigid sequential" in text
    assert "milestone order is a guide" in text
    assert "parallel-safe" in text


def test_task_graph_is_adaptive():
    text = (ROOT / "docs" / "implementation" / "02_task_graph.yaml").read_text(encoding="utf-8")
    assert "execution_model: adaptive_bottom_up" in text
    assert "allowed_top_down_spikes: true" in text
    assert "spike_payback_required" in text


def test_fast_advance_prompts_exist():
    for rel in [
        "agent_prompts/03_fast_advance.md",
        "codex_prompts/06_fast_advance_prompt.md",
        "claude_prompts/01_fast_advance_prompt.md",
    ]:
        assert (ROOT / rel).exists()



def test_fast_advance_supports_material_results_priority():
    lower = (ROOT / "FAST_ADVANCE.md").read_text(encoding="utf-8").lower()
    assert "material results" in lower
    assert "low-velocity" in lower
    assert "ceremon" in lower
    assert "docs/governance/12_decision_methods.md" in lower
