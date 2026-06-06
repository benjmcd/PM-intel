# Bottom-up governance

## Principle

Build on proven local behavior by default. A higher layer may be explored through a bounded spike, but it cannot be considered complete or relied on downstream until the relevant lower-layer dependencies have fixtures, tests, local verification, or a precise blocker.

## Default layer order

1. Repo harness and verification.
2. Postgres schema and migration safety.
3. Raw event storage.
4. Normalization with lineage.
5. Fixture/simulated collectors.
6. Opt-in live adapters.
7. Rolling metrics.
8. Alert scoring and suppression.
9. Local delivery adapters.
10. Replay/backtesting.
11. Ops hardening and local dashboard/CLI.

## Gate rule

Milestone order is a dependency map, not a cage. `FAST_ADVANCE.md` and `docs/implementation/06_adaptive_milestone_map.md` govern speed-oriented sessions.


Each milestone must specify:

- exact acceptance condition;
- exact verification command;
- data-quality assumptions;
- rollback/recovery path if persisted state changes;
- confirmation that the work does not cross the local-only boundary.

## Forbidden shortcuts

- Treating alert UI/dashboard work as complete before replayable alert generation exists. A bounded local sketch is allowed only to reveal missing contracts.
- Treating live API behavior as product-ready before fixture/simulated adapters pass. Opt-in probes are allowed only when isolated from default verification.
- Adding a new database or message broker before Postgres bottlenecks are measured.
- Inferring feed semantics from one example payload without documenting uncertainty.
- Adding SaaS/hosted/billing/user-account/external-secret-manager work without a human-approved exception ADR.


## Decision method for unclear work

Bottom-up governance does not mean single-track thinking. When a lower layer, milestone order, or module boundary is unclear, use the orthogonal lenses and compact Talmudic debate method in `FAST_ADVANCE.md`. The debate is successful only if it selects a next executable slice or a precise blocker. Avoid low-velocity governance work when a test, fixture, interface, or local command would settle the question faster.
