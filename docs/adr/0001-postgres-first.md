# ADR 0001 — Postgres First

## Status
Accepted.

## Decision
Use Postgres as the primary durable store for the MVP.

## Rationale
Postgres gives enough flexibility for raw JSONB payloads, relational market metadata, normalized trades, alert audits, job queues, indexes, partitioning, and local operation. It avoids premature infrastructure complexity.

## Consequences
- Use Postgres before adding ClickHouse/Kafka/Redis as durable foundations.
- Add specialized infrastructure only after measured constraints justify it.
- Schema and migration discipline matter early.
