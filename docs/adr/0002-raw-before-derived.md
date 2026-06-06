# ADR 0002 — Raw Before Derived

## Status
Accepted.

## Decision
Preserve raw external payloads before normalization, metrics, or alerts depend on them.

## Rationale
API semantics can drift and parser bugs are likely. Raw retention allows replay, audit, repair, and regression testing.

## Consequences
- Raw event schema is a core early milestone.
- Derived tables must be rebuildable.
- Alert evidence should trace back to raw or fixture evidence.
