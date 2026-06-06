# 01 — Module Contracts

## Target module map

```text
src/pmfi/
  domain.py                 shared dataclasses and value objects
  config.py                 config loading and validation
  db.py                     Postgres connection/session helpers
  adapters/                 venue-specific API/feed adapters
  ingestion/                raw event persistence and dedupe
  normalizers/              venue raw -> normalized objects
  metrics/                  rolling baselines and derived metrics
  scoring/                  alert decision logic
  delivery/                 alert delivery adapters
  replay/                   replay/backtest workflows
  cli.py                    local CLI entrypoint
```

## Raw event contract

A raw event must include:

- venue code;
- source channel;
- source event type;
- optional source event ID;
- optional venue market ID;
- exchange timestamp if provided;
- received timestamp;
- parser/schema version;
- raw JSON payload;
- payload hash/dedupe key.

## Normalized trade contract

A normalized trade must include:

- venue code;
- venue trade ID if available;
- market identity;
- outcome key;
- price from 0 to 1;
- contract count;
- capital at risk;
- payout notional;
- aggressor/directional side where knowable;
- side-confidence label;
- exchange/received/processed timestamps;
- source raw event reference;
- warnings.

## Alert decision contract

An alert decision must include:

- emit_alert boolean;
- rule ID;
- rule version;
- severity;
- confidence;
- score;
- reason codes;
- evidence object;
- data-quality state;
- dedupe key.

## Adapter contract

A venue adapter should expose:

- market discovery;
- trade event retrieval or streaming;
- ticker/snapshot retrieval where available;
- health/status reporting;
- live-mode flag;
- fixture/simulated equivalent for tests.

No scoring logic belongs in adapters.
