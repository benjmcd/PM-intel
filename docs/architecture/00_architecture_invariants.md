# 00 — Architecture Invariants

These invariants should remain true throughout implementation.

## Invariant A — Raw before derived

All external data must be stored or captured as raw evidence before normalization, scoring, or alerting depends on it.

## Invariant B — Venue isolation

Polymarket and Kalshi differences must be isolated inside adapters/normalizers. Core scoring should consume normalized records and documented metric contracts.

## Invariant C — Postgres as primary durable state

Postgres is the default durable store for the MVP. Redis may be used later for transient dedupe/window state, but should not become the source of truth.

## Invariant D — Replayability

The system must be able to re-run normalization/scoring over a fixture or stored raw-event window and reproduce alert decisions.

## Invariant E — Alert explainability

Every alert must answer:

- What fired?
- Which rule version fired?
- Which data supported it?
- What was abnormal?
- What was the data-quality state?
- What uncertainty remains?

## Invariant F — Live API calls are optional

Default tests and fixture workflows must work without credentials or network access.

## Invariant G — Progressive complexity

Executed trades first. Periodic snapshots second. Full book reconstruction, wallet analytics, and cross-venue matching are later gates.
