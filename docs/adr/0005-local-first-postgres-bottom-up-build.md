# ADR 0005: Local-first Postgres bottom-up build

## Status

Accepted

## Context

The product needs durable raw-event storage, replayability, schema flexibility, auditability, and local development before any hosted runtime work. Introducing streaming infrastructure or cloud dependencies before proving the data path would increase fragility.

## Decision

Build bottom-up with Postgres as the primary durable store:

1. local verification harness;
2. local Postgres schema;
3. raw event store;
4. normalized trade records;
5. offline collectors/fixtures;
6. opt-in live adapters;
7. metrics, alerts, delivery, replay, and ops.

## Consequences

- Early work is slower than a direct WebSocket-to-alert bot but much more auditable.
- Postgres handles relational state, JSON payloads, idempotency, indexing, and local inspection without adding Kafka/ClickHouse prematurely.
- Any non-Postgres infrastructure requires a measured scale gate and ADR.
