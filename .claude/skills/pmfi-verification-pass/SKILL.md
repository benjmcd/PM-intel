---
name: pmfi-verification-pass
description: Use for periodic validation, coherence checks, and milestone reviews in this repository. Runs tests, checks docs/code alignment, identifies blockers, and prevents unverified risky forward progress.
---

# PMFI Verification Pass

Use this skill after meaningful changes, before milestone completion, or whenever the user asks whether the implementation is coherent or ready.

## Steps

1. Re-read `AGENTS.md`, `FAST_ADVANCE.md`, and `docs/governance/02_verification_cadence.md`.
2. Run `python scripts\verify.py`.
3. Run milestone-specific gates if implemented and available.
4. Inspect current diffs and changed files.
5. Check architecture invariants:
   - raw before derived;
   - venue isolation;
   - Postgres-first;
   - replayability;
   - explainable alerts;
   - fixture-first tests.
6. Check whether any top-down spike was paid back with executable evidence.
7. Update `WORKLOG.md` with pass/fail and next action.

## Output standard

Facts, not optimism. Separate passed checks, failed checks, unverified claims, blockers, and next narrow fix.

## Windows-native constraint

Run verification through Python or the Windows wrappers; do not introduce Unix-only scripts.
