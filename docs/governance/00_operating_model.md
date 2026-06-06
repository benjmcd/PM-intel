# 00 — Operating Model

## Purpose

This document governs how agents should work in this repository. The project is local-only and evidence-driven. Bottom-up work is the default because raw data lineage matters, but the repo should not block useful implementation with ceremony.

## Operating rule

```text
Advance local utility while preserving evidence, replayability, tests, and local-only scope.
```

The evidence path is:

```text
fixture/raw payload -> raw event store -> normalized record -> derived metric -> alert decision -> local delivery/audit
```

## Work cycle

Use the smallest useful cycle:

1. **Reconnaissance** — inspect the relevant current repo state, not every file.
2. **Choose** — select the highest-leverage safe next slice.
3. **Build** — implement a lower-layer proof or a thin local vertical slice.
4. **Verify** — run the narrow check and then broader verification when feasible.
5. **Record** — update `WORKLOG.md`, docs, or ADRs only where they help the next agent.

## Adaptive bottom-up standard

Prefer lower-layer work when feasible. A later feature may be explored first only when it is a bounded spike that clarifies missing contracts, operator utility, or data requirements. The spike is not complete until it produces executable evidence or a precise blocker.

## Orthogonal and Talmudic decision standard

When a task is architectural, organizational, orchestration-heavy, data-shape-related, or unclear, agents should briefly examine the problem through orthogonal lenses before choosing a slice: data lineage, operator utility, failure modes, module boundaries, and local-only/Postgres constraints. For non-trivial choices, use the compact Talmudic debate method from `FAST_ADVANCE.md` and `docs/governance/12_decision_methods.md`: strongest case, strongest objection, strongest alternative, consensus, payback artifact, and next check.

This standard avoids narrow-centric implementation and hidden assumptions. It is not a mandate for long essays. If the path is obvious, build the slice and record the reason afterward.

## Material-results standard

When the user asks for speed, material local progress outranks low-impact governance work. Prefer tests, local commands, schemas, fixtures, interfaces, replay reports, and precise blockers. Update plans/docs only when they preserve a real decision, remove ambiguity, or protect later implementation.

## Prohibited shortcuts

- Do not send alerts from live event handlers without raw-event persistence or an equivalent raw evidence record.
- Do not normalize events without preserving raw payloads for replay/audit.
- Do not build cross-venue matching before market identity/equivalence rules exist.
- Do not add ML scoring before transparent rule scoring is measurable.
- Do not add Kafka/ClickHouse/Kubernetes before local Postgres has been measured and found insufficient.
- Do not add hosted/SaaS/user-account/billing/external-secret-manager work while local-only progress remains possible.

## Proof standard

A component is considered proven when it has enough executable evidence for the next layer to rely on it:

- deterministic tests or a verified local command;
- at least one negative/degraded case where relevant;
- a typed or documented contract;
- replayability or a clear reason replay is not yet applicable.

## Agent autonomy

Agents may refactor, add files, improve tests, update docs, and choose implementation details within the local-only and no-trading boundaries. They should not remove safety constraints, weaken acceptance gates, or bypass `docs/governance/08_local_only_exclusion_policy.md` without a stronger human-approved replacement.
