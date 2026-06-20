# 00 — Windows local setup

The primary development environment is a Windows local directory.

## Requirements
- Python 3.11+.
- Docker Desktop for local Postgres.
- Git for Windows if using Git.
- Codex and/or Claude Code pointed at the repository root.

## Bootstrap
PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

Command Prompt:

```cmd
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python scripts\verify.py
```

## Local Postgres
Start Docker Desktop first.
The host port is `5433`.

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

The DB helper streams SQL into the container. A native Windows Postgres client is optional, not required.
`verify` is read-only: it checks Postgres readiness, required PMFI schema objects, and seeded venues without applying migrations or writing rows.
The helper uses the stable Docker Compose project name `pm-intel` so root and repo-local worktrees share the same local PMFI Postgres service. Set `PMFI_COMPOSE_PROJECT` only if you intentionally need an isolated local DB project.

## Fixture workflow target
A complete local fixture workflow should eventually be:

```powershell
python scripts\task.py fixture-replay
```

or:

```powershell
.\pmfi.ps1 fixture-replay
```

After editable install, the equivalent console script is:

```powershell
pmfi replay-fixtures
```

## Troubleshooting
- If `python` points to the wrong interpreter, use `.venv\Scripts\python.exe` explicitly.
- If Docker commands fail, open Docker Desktop and confirm the engine is running.
- If PowerShell blocks script execution, use `pmfi.cmd` or direct `python` commands.
