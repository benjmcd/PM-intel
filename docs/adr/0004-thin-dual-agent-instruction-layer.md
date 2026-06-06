# ADR 0004: Thin dual-agent instruction layer

## Status

Accepted

## Context

The repository should work with both Codex and Claude Code across multiple sessions without relying on chat history. Large always-loaded context files increase inconsistency risk and make agents less likely to follow critical constraints.

## Decision

Use:

- `AGENTS.md` as the canonical shared operating contract;
- `CLAUDE.md` as a thin importer of `AGENTS.md` plus Claude-specific notes;
- `.agent/PLANS.md` and `plans/*.md` for long executable work plans;
- `.agents/skills/` and `.claude/skills/` for repeatable workflows;
- Python verification scripts for enforcement.

## Consequences

- Always-loaded instructions stay short.
- Agents must update active plans and `WORKLOG.md` rather than relying on chat history.
- Long governance/procedural material belongs in plans, skills, docs, or scripts.
