# Codex Start Here

Read `AGENTS.md`, then `AGENT_START_HERE.md`. This repo is Windows-native, local-only, Postgres-first, and adaptive bottom-up.

Do not rely on chat history. Use `FAST_ADVANCE.md`, the active plan in `plans/2026-06-03-bottom-up-implementation-plan.md`, and the local-only boundary in `docs/governance/08_local_only_exclusion_policy.md`.

Canonical verification:

```powershell
python scripts\verify.py
```

Local Postgres verification when Docker Desktop is available:

```powershell
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
```

Fast orientation:

```powershell
python scripts\task.py status
```
