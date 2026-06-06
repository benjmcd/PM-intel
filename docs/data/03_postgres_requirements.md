# 03 — Postgres Requirements

## Why Postgres first

Postgres is the primary storage choice because it supports durable relational structure, JSONB raw payloads, indexes, partitioning, materialized views, advisory locks, `SKIP LOCKED` job queues, and local operation with minimal operational burden.

## Required tables

- venues
- markets
- market_outcomes
- raw_events
- event_dedupe_keys
- normalized_trades
- market_snapshots
- orderbook_snapshots
- metric_windows
- alert_rules
- alerts
- alert_deliveries
- dead_letters
- data_quality_incidents
- job_queue
- system_heartbeats

## Partitioning stance

Partition time-series tables by received/captured timestamp after the schema is proven. Early MVP can use default partitions or monthly partitions.

## Retention stance

Suggested local default:

- raw events: 30–90 days initially;
- normalized trades: retained indefinitely unless storage pressure dictates otherwise;
- market snapshots: retained with downsampling later;
- alert records: retained indefinitely;
- dead letters/incidents: retained until reviewed.
