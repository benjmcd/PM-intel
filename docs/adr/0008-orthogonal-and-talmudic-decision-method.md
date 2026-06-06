# ADR 0008: Orthogonal and Talmudic decision method

## Status

Accepted

## Context

Fast advancement can fail in two opposite ways: rigid governance can slow material progress, while unexamined speed can lock in brittle architecture. The repo needs a lightweight way for agents to reason through unclear architectural, organizational, orchestration, or product-utility choices without generating ceremony.

## Decision

Use `FAST_ADVANCE.md` and `docs/governance/12_decision_methods.md` to define two lightweight methods:

1. An orthogonal problem-solving pass across data lineage, operator utility, failure modes, module boundaries, and local-only/Postgres constraints.
2. A compact Talmudic debate for non-trivial decisions: strongest case, strongest objection, strongest alternative, consensus, payback artifact, and next check.

These methods are advisory for simple fixes and expected for unclear significant choices. They must produce a material next action or precise blocker.

## Consequences

- Agents may move faster without being trapped by milestone ceremony.
- Significant unclear choices should be more coherent and less single-framed.
- Decision work that does not produce tests, schema, fixtures, interfaces, local commands, reports, or precise blockers should be treated as low value.
- Local-only, no-trading, Postgres-first, raw-before-derived, and offline verification constraints remain binding.
