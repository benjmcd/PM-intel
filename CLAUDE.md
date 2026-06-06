@AGENTS.md

## Claude Code specific
- Use plan mode for ambiguous, cross-cutting, or multi-file changes.
- Use `FAST_ADVANCE.md` when the user asks to move quickly or reduce process.
- Use `.claude/skills/*` when the task matches a reusable workflow; do not paste long skill contents into chat unless needed.
- Use `.claude/agents/*` for isolated review passes.
- No automatic agent-side command-trigger automation is configured. Verification is explicit through Python commands.
