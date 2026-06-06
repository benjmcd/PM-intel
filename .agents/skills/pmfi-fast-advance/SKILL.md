---
name: pmfi-fast-advance
description: Use when the user asks to advance the repo quickly, reduce ceremony, maximize progress, or avoid rigid/constrictive governance while preserving safety, local-only scope, tests, modularity, non-fragility, and scalability.
---

# PMFI Fast Advance

Use this skill for speed-focused implementation sessions.

## Loop

1. Read `FAST_ADVANCE.md`, `AGENTS.md`, and `AGENT_START_HERE.md`.
2. Run `python scripts\verify.py` when the environment is ready.
3. Run `python scripts\task.py status`.
4. Pick the highest-leverage safe local slice.
5. Implement a small lower-layer proof or bounded top-down spike.
6. Convert learning into tests, schema, fixtures, interfaces, docs, or a precise blocker.
7. Update `WORKLOG.md`.

## Rules

- Bottom-up is the default, not a rigid lock.
- Do not stall on missing Docker/live API access; advance fixture-backed contracts and record blockers.
- Do not weaken checks, hide live calls in default tests, bypass raw evidence lineage, or cross local-only exclusions.
- Prefer working local utility over broad planning docs.
