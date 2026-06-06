# Codex / Claude handoff protocol

## Handoff fields

Every handoff must be recoverable from files alone. Update `WORKLOG.md` and the active plan with:

- Goal.
- Current milestone.
- Files changed.
- Checks run.
- Passing/failing status.
- Blockers or ambiguities.
- Next recommended slice.
- Residual risk.

## Recommended routing

- Use Codex for tight edit-test loops and implementation slices.
- Use Claude Code plan mode for ambiguous architecture/spec work.
- Use Claude subagents for security, test, and architecture reviews.
- Use the other model/tool family for adversarial review when possible.

Routing is advisory, not dogma. The invariant is cross-review and executable verification.

## Review loop

```text
plan -> implementation slice -> narrow tests -> python scripts\verify.py -> other-model review -> update plan/worklog -> next slice
```

Do not start a broad refactor while the repo is red unless the active plan says the refactor is the recovery path.
