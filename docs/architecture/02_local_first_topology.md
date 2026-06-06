# 02 — Local-First Topology

## Current topology

```text
Windows local machine
  ├─ Python CLI/workers
  ├─ Docker Desktop Postgres 16
  ├─ local config files
  ├─ fixture files
  ├─ local reports/exports
  └─ optional localhost-only HTTP receiver testing
```

## Docker Compose services

- `postgres`: primary durable state.
- `adminer`: optional DB inspection tool.

Do not add hosted services, hosted control planes, registry publishing, or external notification services to the default local path.

## Optional later local services

Only after evidence supports them:

- Redis for transient dedupe/window state.
- ClickHouse for high-volume analytical history.
- NATS/Redpanda/Kafka for multi-consumer stream fanout.
- Local dashboard process after CLI/reports are useful.

Each optional service requires an ADR and must preserve local-only operation.
