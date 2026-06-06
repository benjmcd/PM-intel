# Dual-agent operating model

## Purpose

This repository is prepared for both Codex and Claude Code without requiring either tool to remember prior chat context. The design uses a thin always-loaded instruction layer and moves detailed, volatile, or task-specific context into files that agents load only when needed.

## File roles

| File/path | Role | Loading expectation |
|---|---|---|
| `AGENTS.md` | Canonical cross-agent operating contract | Always loaded/read first |
| `CLAUDE.md` | Claude Code adapter importing `AGENTS.md` | Claude only |
| `AGENT_START_HERE.md` | Fresh-session entrypoint | Read at session start |
| `.agent/PLANS.md` | How to write executable plans | Read when planning |
| `plans/*.md` | Active task specs and resumable work state | Read for current work |
| `docs/` | Durable architecture/data/product/ops knowledge | Read selectively |
| `docs/adr/` | Decision history | Read when changing architecture |
| `.agents/skills/` | Portable repo-local agent skills | Loaded when relevant |
| `.claude/skills/` | Claude Code skill mirror | Loaded when relevant by Claude |
| `.claude/agents/` | Isolated review subagents | Used for review passes |
| `scripts/verify.py` | Executable truth source | Run repeatedly |

## Why not a giant context file

Large always-loaded instruction files become stale and consume context. This repo keeps the startup layer short and pushes detail into plans, ADRs, skills, and executable checks. If a rule becomes long, move it out of `AGENTS.md` and into a skill or doc linked from the active plan.

## Expected collaboration pattern

1. One agent explores and updates the active plan.
2. One agent implements a small bottom-up slice.
3. Verification runs locally.
4. The other model/tool family performs a review pass.
5. Findings and next steps go into `WORKLOG.md` and the active plan.

## Enforcement

Markdown guides behavior. Python checks, tests, enforce behavior. If prose and checks disagree, fix the checks or the code; do not handwave the conflict.
