# 03 — Scalability Plan

## Expected early scale

Prediction-market feeds are not equities-scale, but bursts around major events can still stress naive systems. The MVP should optimize for correctness and replayability before throughput.

## Scaling path

1. Single-process fixture replay.
2. Single-worker local ingestion.
3. Multiple worker processes with Postgres-backed jobs.
4. Redis for transient windows if Postgres becomes awkward.
5. ClickHouse only if analytical query volume or retained event volume justifies it.
6. Stream broker only if there are multiple independent consumers or high-throughput fanout needs.

## Throughput controls

- bounded queues;
- per-venue rate limits;
- per-market subscription caps;
- dedupe keys;
- backpressure handling;
- alert suppression windows;
- degraded-state flags when lag exceeds threshold.

## Metrics to collect before scaling

- events/sec by venue/channel;
- raw payload bytes/day;
- parse failures/day;
- duplicate rate;
- normalization latency p50/p95/p99;
- alert scoring latency;
- DB write latency;
- replay throughput;
- alert false-positive review rate.
