# 02 — Verification cadence

Run `python scripts\verify.py`:
- at fresh session start;
- before handoff;
- after changing contracts, normalization, scoring, or command setup;
- after each meaningful bottom-up milestone.

| Gate | Command | Scope | Required when |
|---|---|---|---|
| V0 | `python scripts\verify.py` | Context check, workspace check, compile, unit tests | Always |
| V1 | `python scripts\db_local.py verify` | Postgres schema/migration checks | SQL/storage changed or M1 work |
| V2 | `python scripts\task.py fixture-replay` | Rebuild normalized records from fixtures | Normalizers/scoring changed |
| V3 | `python -m pmfi.cli live-smoke` with `PMFI_ENABLE_LIVE=1` | Opt-in live feed checks | Adapter live mode changed |
| V4 | `python scripts\task.py review-pass` (`python -m pmfi.cli review-pass`) | Coherence check and docs drift scan | Milestone completion |

Default verification must not require credentials, network access, or a live database.
