# Agentic workflow

## Intent

This repo is prepared for Codex and Claude Code sessions that may not share chat history. Durable state lives in files, plans, tests, SQL, fixtures, and `WORKLOG.md`.

## Thin instruction layer

- `AGENTS.md`: canonical shared operating contract.
- `CLAUDE.md`: imports `AGENTS.md` and adds only Claude-specific notes.
- `.agent/PLANS.md`: execution-plan template.
- `plans/*.md`: active implementation specs.
- `.agents/skills/`: Codex repo skills.
- `.claude/skills/`: Claude Code project skills.
- `.claude/agents/`: Claude Code reviewer subagents.
- `.codex/`: Codex project config and command rules.

## Loop

```text
explore -> plan -> implement smallest slice -> focused tests -> verify -> review -> document -> next slice
```

## Context-bloat control

- Do not add broad generated context files.
- Do not duplicate stable docs into `AGENTS.md` or `CLAUDE.md`.
- Do not make agents read every planning file at session start.
- Summarize current state in `WORKLOG.md` and active `plans/*.md` instead of relying on chat history.
- Move repeatable procedures into skills.

## Periodic coherence pass

After every 2–3 meaningful slices or before session handoff:

1. compare code against `docs/ARCHITECTURE.md`;
2. compare active work against the active plan;
3. run `python scripts\verify.py`;
4. scan for stale assumptions in docs touched by the change;
5. update `WORKLOG.md` with residual risk and next action.


## Orthogonal decision loop

Use this loop only for non-trivial unclear work: examine the obvious path, challenge it, test an orthogonal framing, then select the consensus that yields the fastest verified local product progress. The loop should end in code, tests, schema, fixtures, or a precise blocker.
