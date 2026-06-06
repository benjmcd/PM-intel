# 12 — Decision Methods for Unclear Work

## Purpose

Use this when the next move is architectural, organizational, orchestrational, data-semantic, or otherwise unclear. It exists to increase implementation velocity by avoiding shallow local optima. It is not a ceremony requirement and not ceremonial procedure.

## Core rule

```text
Think orthogonally, debate briefly, converge, then prove with the smallest executable slice.
```

The output should normally be executable progress: code, tests, schema, fixtures, replay evidence, a local command/report, or a precise blocker. Do not produce broad planning artifacts unless the repo is explicitly in a planning/specification task or the decision affects architecture, storage, scope, or public contracts.

## Orthogonal approach

Before committing to a design, check at least one materially different lens:

| Lens | Question | Useful output |
|---|---|---|
| Data lineage | What raw evidence, schema, fixture, or replay path must exist before this can be trusted? | test, schema, fixture, lineage field |
| Operator utility | What local command/report/alert would make the product more usable now? | CLI command, JSONL report, alert example |
| Failure modes | What breaks under duplicates, stale feeds, DB unavailability, bad payloads, or partial state? | degraded-state test, data-quality flag, blocker |
| Module boundaries | Which module owns the behavior, and what interface prevents coupling? | interface contract, adapter boundary |
| Local-only/Postgres | Can this be solved with local Python, files, and Postgres before adding infrastructure? | Postgres-backed design or measured blocker |
| Scalability | What fails first under event bursts or larger watchlists, and can it degrade safely? | benchmark, batching rule, queue/state boundary |
| Removal-first | What can be deleted, deferred, or replaced with a simpler local substitute? | smaller design, deferred non-goal, simplified interface |

Use the lenses to choose the next slice. Do not turn the lenses into a long essay. Do not use it for obvious one-file fixes or mechanical changes.

## Talmudic debate method

For non-trivial decisions, use compact adversarial reasoning:

```text
Question:
Option A / strongest case:
Objection / failure mode:
Option B or orthogonal alternative / strongest case:
Consensus for this repo state:
Payback artifact:
Next command/check:
```

A good consensus is not a compromise for its own sake. It is the most coherent action under current constraints: local-only scope, Postgres-first storage, raw-before-derived lineage, Windows-native operation, modularity, non-fragility, scalability, replayability, and executable verification.

## Material-results rule

When the user asks to advance quickly, material results outrank low-velocity governance. Prefer:

- a passing test;
- a working local command;
- a schema/interface improvement;
- a fixture or replay artifact;
- a local report/output;
- a precise blocker with evidence.

Avoid spending time on governance, broad planning, or doc reshuffling unless it removes real ambiguity or prevents a high-cost mistake.

## Relationship to bottom-up work

Bottom-up remains the default because PMFI depends on raw evidence lineage. Orthogonal/Talmudic decision work helps choose *which lower layer, top-down spike, or parallel slice* should be advanced next. It does not justify skipping raw evidence, weakening verification, adding SaaS/platform scope, relying on unproven live behavior, or using process as a substitute for implementation.
