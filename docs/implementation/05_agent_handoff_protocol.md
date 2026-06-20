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

## Executable local snapshot

For a reproducible repo-local evidence bundle, run:

```powershell
python scripts\task.py handoff
```

The command writes compact JSON and Markdown under `reports\handoff\`. It records
current Git branch/HEAD/upstream counts, dirty-state evidence, recent commits,
latest `WORKLOG.md` excerpt plus bounded excerpts for its `###` sections, task
status output, runtime details, and the verification commands to run next. It
does not push, publish, dump environment variables, or imply remote readiness.
Use `--db-verify` to attempt local Postgres readiness and `--run-verify` to run
the default gate inside the snapshot. Use `--publish-ready` to record
network-free publish-readiness evidence, or `--publish-ready-fetch` to record the
same validate-only check with fresh remote tracking evidence. All outcomes are
recorded as evidence; none of these flags push, merge, or write source files.

## Validate-only publication readiness

Before claiming that a local branch is ready to push or open as a PR, run:

```powershell
python scripts\task.py publish-ready --fetch
```

The command performs no push, publish, or artifact write. With `--fetch`, it
refreshes the configured remote-tracking branch, then checks worktree
cleanliness, current branch/HEAD/upstream, ahead/behind counts, upstream/main
ancestry, changed-file scope, and attribution/generated footer strings in the
commit range and diff. Without `--fetch`, it stays network-free and reports that
remote freshness was not checked.

## Clean-checkout smoke

Before claiming release-profile readiness from a local branch, run:

```powershell
python scripts\task.py clean-checkout-smoke --run-verify --db-verify
```

The command creates a detached clean worktree under `worktrees\`, runs the
workspace, review-pass, optional default verification, and optional DB gates
there, writes an ignored JSON report under `reports\clean-checkout\`, and then
removes the temporary worktree unless `--keep-worktree` is supplied. It performs
no live API calls, source writes, push, or merge.

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
