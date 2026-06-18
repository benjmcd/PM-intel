from __future__ import annotations

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _print_wrapped_items(items: list[object]) -> None:
    for item in items:
        if isinstance(item, dict):
            command = item.get("command")
            why = item.get("why")
            if command and why:
                print(f"- {command} -- {why}")
            elif command:
                print(f"- {command}")
            else:
                print(f"- {item}")
        else:
            print(f"- {item}")


def main() -> int:
    graph_path = ROOT / "docs" / "implementation" / "02_task_graph.yaml"
    graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    posture = graph.get("current_posture", {})
    print("PMFI local repo status")
    print(f"environment: {graph.get('primary_environment', 'windows_local_directory')}")
    print(f"execution model: {graph.get('execution_model')}")
    print(f"default gate: {graph.get('required_default_gate')}")
    print()
    print("Current posture:")
    print(posture.get("summary", "No current posture recorded."))
    if posture.get("source_of_truth"):
        print(f"source of truth: {posture['source_of_truth']}")
    constraints = posture.get("constraints_intact", [])
    if constraints:
        print("constraints intact: " + ", ".join(constraints))
    print()
    print("Next recommended focus:")
    focus = posture.get("next_recommended_focus", {})
    if focus:
        print(f"{focus.get('id')}: {focus.get('summary')}")
    else:
        print("No next focus recorded.")
    print()
    print("Residual proof gaps:")
    gaps = posture.get("residual_proof_gaps", [])
    if gaps:
        _print_wrapped_items(gaps)
    else:
        print("- No residual proof gaps recorded.")
    print()
    print("High-priority commands:")
    _print_wrapped_items(graph.get("high_priority_commands", []))
    print()
    print("Milestones:")
    for milestone in graph.get("milestones", []):
        mid = milestone.get("id")
        name = milestone.get("name")
        status = milestone.get("status")
        gate = milestone.get("gate")
        print(f"- {mid}: {name} [{status}] gate={gate}")
        if milestone.get("proof"):
            print(f"  proof={milestone['proof']}")
    print()
    print("Fast-advance rule: choose the highest-leverage safe local slice; bottom-up is the default, not a rigid lock.")
    print("If a lower layer is blocked, advance fixture-backed contracts and record the blocker in WORKLOG.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
