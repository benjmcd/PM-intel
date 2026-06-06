# 09 — Agent Runtime Compatibility

## Purpose

This repo must support both Codex and Claude Code without relying on chat history or duplicating large instruction files.

## Canonical instruction layer

- `AGENTS.md` is the canonical operating contract.
- `CLAUDE.md` imports `AGENTS.md` and adds only Claude-specific notes.
- `README.md` is human-facing, not an agent-control file.
- `docs/` holds durable project knowledge.
- `plans/` holds active executable plans.
- `.agent/PLANS.md` defines the plan format.
- `.agents/skills/` and `.claude/skills/` hold repeatable workflows.
- Explicit checks, tests, enforce behavior; prose only guides behavior.

## Context-bloat policy

Do not create broad root-level context files such as `CONTEXT.md`, `RULES.md`, `DEVELOPMENT.md`, `AI_NOTES.md`, `SYSTEM_OVERVIEW.md`, `PROJECT_STATE.md`, or `TASKS.md` unless an ADR approves the exception. Use scoped docs and active plans instead.

## Adaptive bottom-up default

Use this order as the default dependency map. It is not a hard lock; `FAST_ADVANCE.md` and `docs/implementation/06_adaptive_milestone_map.md` control when bounded top-down spikes or parallel fixture-backed work are appropriate:

```text
local verification -> Postgres -> raw events -> normalization -> simulated adapters -> optional live adapters -> metrics -> alerts -> delivery -> replay -> hardening
```

## Handoff requirements

Before switching between Codex and Claude Code, update:

1. `WORKLOG.md` with current state, checks run, failing checks, changed files, and next step.
2. The active plan in `plans/` with progress and residual risks.
3. Relevant docs/ADRs if behavior or architecture changed.

The receiving agent must read these files before relying on any previous conversation context. If stale wording conflicts with fast-advance guidance, prefer executable truth, `FAST_ADVANCE.md`, and the adaptive milestone map. If any older file implies rigid sequential execution, treat that wording as superseded by `FAST_ADVANCE.md` and the adaptive milestone map.
