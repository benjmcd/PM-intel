# Testing and verification

## Canonical command

```powershell
python scripts\verify.py
```

This is the default gate for every handoff. It must remain cheap enough to run frequently.

## Test layers

| Layer | Purpose | Default network? |
|---|---|---|
| Compile/import | Catch syntax/import breakage. | No |
| Unit tests | Domain, normalization, scoring. | No |
| Fixture replay | Exercise raw-to-alert path with saved examples. | No |
| DB verification | Apply SQL and query Postgres. | Local Docker only |
| Live smoke | Verify current venue connectivity. | Opt-in only |

## Default rules

- Normal tests must not require network, API keys, or live accounts.
- Live tests must require an explicit env flag such as `PMFI_ENABLE_LIVE=1`.
- New behavior requires tests or a documented reason tests are deferred.
- DB changes require at least one clean-DB migration check once Postgres is available.
- Failed tests are evidence; do not delete or weaken them to get green checks.

## Periodic verification loop

During multi-hour agent work, run a verification pass after each coherent slice:

1. focused test for touched code;
2. `python scripts\agent_context_check.py` for governance drift;
3. `python scripts\verify.py` before handoff;
4. update `WORKLOG.md` and the active plan.

## Review passes

Use a separate review pass for:

- architecture boundary changes;
- database schema changes;
- live adapter changes;
- alert semantics changes;
- security or secret-handling changes.

Prefer cross-family review where possible: if Codex implemented, use Claude reviewer agents; if Claude implemented, use Codex review.
