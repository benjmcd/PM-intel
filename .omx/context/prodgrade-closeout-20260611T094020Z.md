# Prodgrade Closeout Context

## Task statement
Re-review the interrupted Claude production-grade tranche, identify remaining work, set the
overarching Codex goal, and continue until remaining work is completed or explicitly blocked.

## Desired outcome
- PR #4 (`prodgrade-advance` -> `main`) is merge-ready under local-only PMFI constraints.
- All actionable PR review comments are fixed or explicitly rejected with evidence.
- Verification is fresh, targeted, and proportional to this device.
- Durable ledger/worklog/roadmap distinguish immediate release blockers from optional future work.

## Known facts and evidence
- Worktree: `C:\Users\benny\OneDrive\Desktop\PM-intel-prodgrade`.
- Branch: `prodgrade-advance`, clean before this pass, pushed at `d3ca4de`.
- PR #4 is open, non-draft, merge state clean, no CI checks reported.
- Claude session ended after `gh pr edit 4` result, before final human-facing handoff.
- PR review comments are present on PR #4:
  - P1: `scripts/db_local.py init` records migrations before `schema_migrations` exists.
  - P2: price-impact rule is not seeded for `replay --from-db` windows.
  - P2: data-quality feed-silence checks every enabled venue, not active ingest venues.
  - P2: Kalshi 429 retry path can loop forever inside helper.
  - P2: data-quality feed_silent/dead_letter_spike alerts dedupe each other.
  - P2: FileDelivery rotation path stat can raise outside non-fatal guard.
  - P2: Polymarket 429 path sleeps then raises same response instead of retrying.
- True blocked/future items remain:
  - Wallet/holder accumulation blocked by public feed lacking wallet/maker/taker identity.
  - Kalshi WS auth path unsupported; REST polling is current supported path.
  - Optional liquidity extensions: periodic orderbook polling, Kalshi orderbook capture, deeper book coverage.
  - Optional dashboard Phase 3 chart polish.
  - Optional config gating for composite/cross-venue behavior.

## Constraints
- Local-only, no trading/order placement, no SaaS/hosted/account/billing/external secret scope.
- No live API calls in default tests.
- Windows-native commands and path-length discipline.
- No deletes; archive only if removal ever becomes necessary.
- Use small targeted verification, not broad heavy full-suite runs, unless explicitly justified.
- Raw external payloads remain source of truth before derived records.

## Unknowns/open questions
- Whether local Postgres is currently running and has a suitable DB for DB-gated tests.
- Whether PR comments cover all merge blockers; after fixing, re-check PR review comments.
- Whether a final merge should be performed by agent or left for human; default is make PR merge-ready and report.

## Likely touchpoints
- `scripts/db_local.py`
- `src/pmfi/markets.py`
- `src/pmfi/delivery/file.py`
- `src/pmfi/monitoring/base.py`
- `src/pmfi/monitoring/data_quality.py`
- `src/pmfi/commands/daemon.py`
- `src/pmfi/replay.py`
- `src/pmfi/pipeline/engine.py`
- Relevant tests under `tests/`
