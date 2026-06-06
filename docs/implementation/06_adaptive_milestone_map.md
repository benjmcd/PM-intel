# 06 — Adaptive milestone map

This file supersedes rigid sequential interpretations of the milestone list. The implementation remains bottom-up by default, but milestone order is a guide, not a cage.

## Primary objective

Advance toward a production-grade local tool that can be used end-to-end on a Windows machine with local Postgres, fixture replay, optional opt-in live reads, transparent alert scoring, local delivery, and replay/backtest support. In this phase, production-grade means reliable, replayable, testable, operator-usable local software; it does not mean hosted deployment, SaaS readiness, registry publication, billing, or user-account infrastructure.

## Critical path

```text
M0 repo baseline
M1 local Postgres proof
M2 raw event persistence
M3 normalized trade contracts
M4 fixture pipeline
M5 opt-in live read adapters
M6 rolling metrics
M7 explainable alerts
M8 local delivery
M9 replay/backtest
M10 hardening and local operator UX
```

## Adaptive rule

Work bottom-up unless one of these is true:

- a lower milestone is blocked by the local environment;
- a narrow top-down spike will reveal missing lower-layer requirements faster;
- the user explicitly asks to maximize forward progress;
- a product-utility gap is preventing meaningful validation of lower-layer choices.

In those cases, make the smallest useful top-down or parallel move, then convert the learning into tests, schema, interfaces, fixtures, docs, or a precise blocker.

## Decision acceleration rule

When milestone ordering, architecture, data contracts, orchestration, or operator workflow is unclear, use an orthogonal approach before settling on the next slice. Compare the obvious path with at least one materially different framing. For non-trivial choices, use the compact Talmudic debate format from `docs/governance/12_decision_methods.md` and `docs/governance/12_decision_methods.md`.

The chosen consensus must point to executable evidence: a test, fixture, schema/query proof, CLI behavior, replay result, local report, interface, or documented blocker.

## Parallel-safe tracks

These can advance without violating bottom-up discipline:

| Track | Can advance when | Must not do |
|---|---|---|
| Postgres proof | Docker Desktop/local DB available | Add another durable DB first. |
| Fixture pipeline | DB unavailable or live feeds unverified | Pretend fixtures are complete venue contracts. |
| CLI/operator workflow | Needs product clarity | Hide network calls or bypass raw evidence. |
| Alert examples | Metrics still basic | Claim predictive reliability. |
| Live adapter interface | Official docs/API access uncertain | Make live calls part of default verification. |
| Reports/backtest shape | Scoring still evolving | Freeze premature metrics as final. |
| Orthogonal decision spike | Architecture/orchestration choice is unclear | Produce debate without payback artifact. |
| Docs/ADR cleanup | Stale docs block implementation choices | Rewrite broad docs without changing executable truth. |

## Recommended next-action selector

1. Run `python scripts\verify.py`.
2. Run `python scripts\task.py status`.
3. Identify the lowest failing or unproven layer that blocks two or more later layers.
4. If the choice is unclear, use the orthogonal/Talmudic decision method and end with a payback artifact.
5. If that layer is blocked by external environment, implement the closest fixture/fake-backed contract and record the blocker.
6. Add or update the narrowest test that would fail without the intended change.
7. Implement a small slice.
8. Re-run the narrow check, then `python scripts\verify.py` before handoff when feasible.

## Milestone flexibility

- M1 should be attacked early because Postgres is the backbone.
- M2/M3 can partly advance with repositories/fakes before local Docker is proven.
- M4 can advance with file-backed fixture replay while DB write paths mature.
- M5 should remain opt-in and bounded.
- M6/M7 can start with deterministic fixture metrics before live history exists.
- M8 should start with stdout/file delivery only.
- M9 can begin once replay produces stable normalized events and alert decisions.
- M10 should not become SaaS/productization work; it is local operator hardening.

## Completion evidence

A milestone is complete only when there is executable evidence. Documentation alone does not complete a milestone. A top-down or orthogonal spike is complete only when it has been converted into at least one durable artifact: a test, schema change, interface, fixture, config, local report, or documented blocker.
