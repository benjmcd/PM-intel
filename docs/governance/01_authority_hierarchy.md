# 01 — Authority Hierarchy

## Canonical sources

1. **Safety/honesty/local-only exclusions:** never bypass these to move faster.
2. **Executable truth:** tests, CLI behavior, database migrations that apply successfully, fixture replay outputs.
3. **Current repository files:** code, SQL, configs, fixtures.
4. **Fast-advance contract:** `FAST_ADVANCE.md` when the user asks for speed or reduced process.
5. **Root governance:** `AGENTS.md`, `AGENT_START_HERE.md`, `CLAUDE.md` for Claude Code, and `CODEX_START_HERE.md` for Codex.
6. **Active plans and implementation maps:** `plans/*`, `docs/implementation/*`.
7. **Architecture/data/product docs:** `docs/architecture/*`, `docs/data/*`, `docs/product/*`.
8. **External official docs:** venue/API docs, only after re-checking.
9. **Assumptions and chat context:** lowest authority.

## Conflict handling

When two repo files conflict:

1. Prefer executable truth and the most recent local-only/fast-advance contract.
2. Apply the smallest correction that unblocks implementation.
3. Add/update an ADR only if the decision is architectural or scope-changing.
4. Add a test if the conflict involved behavior.
5. Record the result in `WORKLOG.md` when the conflict affects future work.

## External-source freshness

Venue API docs and agent-runtime behavior can change. Before implementing live API behavior, re-check the relevant official docs listed in `docs/data/04_external_source_map.md`. If internet access is unavailable, implement behind an interface using fixtures and document the live-verification blocker.

## No invisible assumptions

If a persisted schema, normalized field, or alert rule depends on an assumption, write it down in one of:

- `docs/data/01_feed_assumptions_and_blockers.md`
- `docs/architecture/01_module_contracts.md`
- `docs/implementation/06_adaptive_milestone_map.md`
- `WORKLOG.md`
