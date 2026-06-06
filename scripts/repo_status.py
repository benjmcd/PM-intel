from __future__ import annotations

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    graph_path = ROOT / "docs" / "implementation" / "02_task_graph.yaml"
    graph = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    print("PMFI local repo status")
    print("environment: Windows local directory")
    print(f"execution model: {graph.get('execution_model')}")
    print(f"default gate: {graph.get('required_default_gate')}")
    print()
    print("High-priority commands:")
    print(r"- python scripts\verify.py")
    print(r"- python scripts\db_local.py up")
    print(r"- python scripts\db_local.py init")
    print(r"- python scripts\db_local.py verify")
    print(r"- python scripts\task.py fixture-replay")
    print()
    print("Milestones:")
    for milestone in graph.get("milestones", []):
        mid = milestone.get("id")
        name = milestone.get("name")
        status = milestone.get("status")
        gate = milestone.get("gate")
        print(f"- {mid}: {name} [{status}] gate={gate}")
    print()
    print("Fast-advance rule: choose the highest-leverage safe local slice; bottom-up is the default, not a rigid lock.")
    print("If a lower layer is blocked, advance fixture-backed contracts and record the blocker in WORKLOG.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
