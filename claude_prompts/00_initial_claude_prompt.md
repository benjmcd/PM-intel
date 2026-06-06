# Initial Claude Code prompt

Read `CLAUDE.md`, `AGENT_START_HERE.md`, `FAST_ADVANCE.md`, and the latest `WORKLOG.md` entry. Then run:

```powershell
python scripts\verify.py
python scripts\task.py status
```

Open the active plan, adaptive milestone map, architecture docs, testing docs, or reviewer subagents only as needed for the current slice. Use plan mode if the next step is ambiguous, cross-cutting, DB-related, or architecture-affecting. If the next step is obvious, implement a small verified slice and update durable state afterward.

Append a reconnaissance note to `WORKLOG.md`. Then advance the highest-leverage safe local slice. Use reviewer subagents before handoff if the change affects architecture, data/DB, testing, security, or live adapters.

Preserve local-only scope and no trading/order placement. Do not add SaaS, hosted runtime, billing, registry-publication, external secret-manager, RBAC/OIDC/user-account, or external notification service work.
