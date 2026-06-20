# Agent Start Here

This is the fresh-session entrypoint for Codex or Claude Code. Do not rely on chat history.

## First actions

1. Read `AGENTS.md`.
2. Read `FAST_ADVANCE.md`.
3. Read `LOCAL_ONLY_SCOPE.md`.
4. Run `python scripts\verify.py` when the environment is ready.
5. Run `python scripts\task.py status`.
6. Inspect `WORKLOG.md` for the latest durable state.
7. Use the active plan and adaptive milestone map to choose the highest-leverage safe next action.
8. If the next action is structurally unclear, use `docs/governance/12_decision_methods.md`.

## Current product boundary

The product is local-only for now. Do not add SaaS, hosted runtime, billing, registry-publication, external secret-manager, RBAC/OIDC/user-account, or external notification service work. If a task appears to require one of those areas, record a blocker and propose a local substitute.

## Command surface

```powershell
python scripts\verify.py
python scripts\task.py status
python scripts\db_local.py up
python scripts\db_local.py init
python scripts\db_local.py verify
python scripts\task.py fixture-replay
python scripts\task.py handoff --db-verify
python scripts\task.py publish-ready --fetch
python scripts\task.py clean-checkout-smoke --install-dev --run-verify --db-verify
python scripts\task.py soak --window 2h
```

Optional wrappers:

```powershell
.\pmfi.ps1 verify
```

```cmd
pmfi.cmd verify
```

## Working rule

Prefer narrow implementation slices that make one layer more proven or one local workflow more useful. Bottom-up is the default. Bounded top-down spikes and orthogonal approaches are allowed when they remove uncertainty or accelerate a usable local product; convert the learning into tests/contracts before relying on it. Do not spend time on low-impact ceremony when executable progress is available.
