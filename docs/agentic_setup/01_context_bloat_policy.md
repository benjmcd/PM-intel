# Context-bloat policy

## Rule

Keep always-loaded context minimal. Agents should discover local code and tests as needed instead of loading broad generated summaries.

## Always-loaded files

- `AGENTS.md`
- `CLAUDE.md` when using Claude Code

These must remain concise. `python scripts\agent_context_check.py` enforces rough size limits.

## Selectively loaded files

- `AGENT_START_HERE.md` for fresh sessions.
- `plans/*.md` for current work.
- Specific `docs/**` files only when the task touches that area.
- Skills only when the workflow matches.

## Avoid adding

Do not add broad, duplicative context files such as `CONTEXT.md`, `RULES.md`, `AI_NOTES.md`, `PROJECT_STATE.md`, or `SYSTEM_OVERVIEW.md` unless there is a specific, documented reason. They tend to drift and contradict the source files they summarize.

## Update rule

If an agent repeatedly makes the same mistake:

1. First add an executable test or script check if possible.
2. If enforcement is not possible, add a narrow rule to the relevant skill/doc.
3. Only add to `AGENTS.md` if the rule is short, stable, and cross-cutting.
