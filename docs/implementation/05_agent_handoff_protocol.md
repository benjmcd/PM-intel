# Agent Handoff Protocol

## Required handoff note

When a session stops, append this to `WORKLOG.md`:

```markdown
## <date/time> handoff

### Goal
<current objective>

### Current milestone
<M0-M10 and status>

### Changed files
- <file>: <change>

### Checks run
- `<command>`: pass/fail

### Failing or skipped checks
- <check>: <reason>

### Residual risks
- <risk>

### Next smallest step
<one concrete next step>
```

## Receiving-agent startup

1. Read `AGENTS.md` and `AGENT_START_HERE.md`; read `CLAUDE.md` if in Claude Code or `CODEX_START_HERE.md` if in Codex.
2. Read the latest `WORKLOG.md` entry and the active plan only as needed for the current slice.
3. Run `python scripts\verify.py` before editing when the environment is ready.
4. If verification is red because of code/tests, fix the narrow failure before adding features. If it is red because of a local environment dependency, record the blocker and continue only with fixture-backed or documentation-safe work.

## Review before handoff

Run at least:

```powershell
python scripts\agent_context_check.py
python scripts\verify.py
```

For DB work, also run:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```
