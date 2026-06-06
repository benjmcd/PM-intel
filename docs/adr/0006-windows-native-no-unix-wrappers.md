# ADR 0006: Windows-native local workflow

## Status
Accepted

## Context
The project will be worked and advanced in a Windows local directory by Codex and Claude Code sessions. The command surface must not depend on Unix-only scripts, non-Windows build-runner availability, remote workflow automation, or automatic agent-side command-trigger automation.

## Decision
Use Python as the executable source of truth, with optional Windows wrappers:

- `python scripts\verify.py`
- `python scripts\db_local.py ...`
- `pmfi.cmd ...`
- `pmfi.ps1 ...`

Local Postgres is run through Docker Desktop. SQL is streamed into the Postgres container by Python, so a native `psql.exe` installation is not required.

## Consequences
- Agents must preserve Windows command examples in new docs and plans.
- Local verification is the required gate; no remote workflow automation workflow is required for this package.
- Any future cross-platform support must not weaken the Windows baseline.
- Unix-specific local wrappers remain out of scope unless explicitly requested later.
