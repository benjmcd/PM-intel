# 02 — Observability

## Minimum metrics

- raw events received by venue/channel;
- parser failures;
- duplicate events;
- normalized trades created;
- dead letters;
- feed lag p50/p95;
- scoring latency;
- alerts fired by rule/severity;
- delivery attempts/failures;
- data-quality incidents.

## Minimum logs

- connection start/stop;
- reconnect attempts;
- schema/parser failures;
- degraded-state transitions;
- alert fired/suppressed;
- delivery failure;
- replay start/end.

## Health check

A local health check should eventually report:

- database connectivity;
- last event time by venue/channel;
- open data-quality incidents;
- pending/dead-letter counts;
- recent alert count;
- config validity.
