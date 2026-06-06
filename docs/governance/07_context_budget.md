# Context budget policy

## Rule

Always-loaded instructions must stay thin. Durable detail belongs in docs, plans, skills, tests, SQL, and scripts.

## Do not add by default

Avoid broad root files such as:

- `CONTEXT.md`
- `RULES.md`
- `DEVELOPMENT.md`
- `CODING_STANDARDS.md`
- `AI_NOTES.md`
- `PROMPTS.md`
- `SYSTEM_OVERVIEW.md`
- `PROJECT_STATE.md`
- `TASKS.md`

Use `WORKLOG.md` and active `plans/*.md` for live state.

## Update rules

- Add to `AGENTS.md` only for recurring, always-relevant constraints.
- Add to `CLAUDE.md` only for Claude-specific behavior.
- Add to `.agent/PLANS.md` for planning process.
- Add to skills for repeatable procedures.
- Add to docs for durable design.
- Add to scripts for enforceable checks.
