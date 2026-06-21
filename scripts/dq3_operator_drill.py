from __future__ import annotations

import argparse
import json


def build_drill(run_key: str) -> dict[str, list[str]]:
    return {
        "incident": [
            "pmfi dead-letters --limit 20",
            "pmfi data-coverage --venue polymarket",
        ],
        "backlog": [
            "pmfi data-coverage --venue polymarket",
            "pmfi replay --from-db --venue polymarket --limit 0",
        ],
        "repair": [
            "pmfi replay --from-db --venue polymarket --limit 0 --persist",
            "pmfi dead-letters resolve <dead_letter_id_or_prefix> --dry-run",
        ],
        "final_status": [
            "pmfi db-verify",
            "pmfi health",
            f"pmfi data-coverage --venue polymarket  # confirm {run_key} has no unaccounted trade raw_events",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the local-only DQ-3 operator recovery drill commands."
    )
    parser.add_argument("--run-key", default="DQ3-RECOVERY-V1")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    drill = build_drill(args.run_key)
    if args.json_output:
        print(json.dumps(drill, indent=2, sort_keys=True))
        return 0

    for category, commands in drill.items():
        print(f"[{category}]")
        for command in commands:
            print(f"  {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
