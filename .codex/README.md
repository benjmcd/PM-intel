# Codex setup

`AGENTS.md` is the canonical instruction file. `.codex/config.toml` provides conservative project-scoped defaults: workspace writes, user approval on boundary-crossing, no network by default, and read-only reviewer subagent configs.

Use `codex_prompts/00_initial_codex_prompt.md` for the first session and `codex_prompts/03_continuation_prompt.md` after handoffs. Do not rely on chat history; read the active plan in `plans/` and `WORKLOG.md`.
