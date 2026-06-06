# Model and tool routing

This file is advisory. The repo must remain tool-agnostic and recoverable through plans, tests, and worklogs.

## Default routing

| Work type | Preferred route | Reason |
|---|---|---|
| Ambiguous product/spec/architecture | Claude Code plan mode or high-reasoning Codex | Better to clarify design before edits |
| Multi-file implementation | Codex or Claude Code with active plan | Requires tight edit-test loop |
| Tactical single-file fix | Fast model/profile | Keep cost/latency low |
| Security/test/architecture review | Other model/tool family | Avoid same-model self-review |
| Large refactor/migration | Plan first, execute in small verified slices | Reduces drift and recovery cost |

## Constraints

- Do not encode model-specific assumptions into product code.
- Keep tool-specific notes in `.codex/` and `.claude/`.
- Do not require network access or broad filesystem access for normal verification.
