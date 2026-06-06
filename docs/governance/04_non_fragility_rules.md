# 04 — Non-Fragility Rules

## Ingestion resilience

Every collector must eventually support:

- reconnect with exponential backoff;
- heartbeat/staleness detection;
- idempotent raw-event insert;
- dedupe keys;
- parser failure dead-lettering;
- degraded data-quality flags;
- fixture replay for parser/normalizer tests.

## Storage resilience

Postgres tables must support:

- raw payload retention;
- schema version fields;
- append-first event capture;
- derived records rebuildable from raw events;
- indexes aligned with query patterns;
- partitioning where time-series growth justifies it;
- migration/replay testing before destructive changes.

## Alert resilience

Alerts must include:

- rule ID and rule version;
- reason codes;
- severity;
- confidence;
- data-quality status;
- evidence payload or references;
- dedupe/suppression metadata;
- enough normalized metrics to reconstruct why the alert fired.

## Dependency restraint

New infrastructure requires an ADR. Do not add heavy dependencies because they are standard in larger systems. Add them only when a measured local bottleneck or required local capability cannot be satisfied with Postgres and simple workers. Hosted/SaaS, billing, registry/release, managed-secret, and full auth/account infrastructure are excluded by `docs/governance/08_local_only_exclusion_policy.md`.
