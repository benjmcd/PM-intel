# 04 — Agent Session Cadence

## Recommended session rhythm

Use a lightweight repeating pattern:

```text
Orient -> choose highest-leverage slice -> build -> verify -> record -> continue
```

Do not let the cadence become ceremony. If the next step is obvious and low-risk, implement it and document the result afterward.

## Periodic verification prompts

After substantial progress, use `agent_prompts/01_verification_pass.md` or the relevant Codex/Claude review prompt.

For speed-focused work, use `agent_prompts/03_fast_advance.md`.

## Expected handoff state

Every session should leave the repo in one of two states:

1. Green and ready for the next highest-leverage slice.
2. Red with a clear blocker recorded in `WORKLOG.md`, including the failing command and narrow next fix.

No ambiguous state is acceptable.
