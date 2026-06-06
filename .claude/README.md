# Claude Code integration

`CLAUDE.md` imports `AGENTS.md` and adds only Claude-specific routing notes. Keep `CLAUDE.md` small.
Repeatable procedures belong in `.claude/skills/`; broad review work belongs in `.claude/agents/`.
The canonical verification entrypoint is `python scripts\verify.py`.

No automatic Claude command-trigger configuration is included. Verification is explicit through Python commands.
