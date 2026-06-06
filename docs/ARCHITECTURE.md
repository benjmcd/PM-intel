# Architecture

## System purpose

Prediction Market Flow Intelligence is a Windows-native, local-only system for observing public prediction-market activity, preserving raw event data, normalizing executed trades, computing market-relative baselines, and emitting explainable anomaly alerts.

## Explicit non-purpose

The system is not an automated trading bot, a paid market-data redistribution layer, an insider-identification service, a hosted/SaaS product, a multi-user account system, or a generic fixed-threshold whale alert clone.

## Primary constraints

- Local-only current build horizon.
- Windows local directory first.
- Postgres-first persistence.
- Raw-before-derived data model.
- Executed-trades-first MVP.
- Offline tests by default.
- Live venue access isolated behind opt-in adapters.
- Alert explanations must include evidence and data-quality state.
- SaaS/hosted/multi-tenant/external-control-plane work is excluded by `docs/governance/08_local_only_exclusion_policy.md`.

## Component map

| Component | Path | Responsibility | Should not do |
|---|---|---|---|
| Domain models | `src/pmfi/domain.py` | Common typed entities and value semantics. | Venue API parsing. |
| Normalizers | `src/pmfi/normalization.py` | Convert venue-specific raw events into common records. | Network I/O or alert delivery. |
| Scoring | `src/pmfi/scoring.py` | Transparent alert decisions and evidence. | Data ingestion or persistence side effects. |
| CLI | `src/pmfi/cli.py` | Local operator commands and replay entrypoints. | Hidden live network calls by default. |
| SQL | `sql/` | Durable schema, indexes, views, seed data. | Business logic that belongs in tested code unless intentionally DB-native. |
| Config | `config/` | Local examples and declarative alert rules. | Secrets or hosted-control-plane config. |
| Tests | `tests/` | Offline verification with fixtures/fakes. | Required live API access. |

## Data flow

```text
venue raw event or fixture
  -> raw event validation
  -> immutable Postgres raw_events record
  -> normalization into venue-neutral trade/event records
  -> rolling metrics and baselines
  -> alert scoring and suppression
  -> local delivery adapter and alert audit record
```

## Architectural invariants

- Every derived record must be traceable to raw input or a documented synthetic fixture.
- Every alert must be replayable from persisted raw/normalized data and rule configuration.
- Venue-specific fields do not leak across the system except through typed raw payload storage.
- Live adapters cannot run in default test/verification paths.
- Postgres remains the primary durable state engine until a documented scale gate is met.
- New infrastructure must solve a measured local bottleneck, not an imagined future bottleneck.
- Any exception to the local-only boundary requires a future human-approved ADR.

## Extension points

- Add a venue: create a venue adapter, fixture examples, normalizer tests, config entry, and source-map documentation.
- Add an alert rule: add YAML config, scorer implementation, positive/negative/suppression fixtures, and explanation fields.
- Add a storage table: add SQL migration, data dictionary entry, query/index rationale, and rollback note.
- Add live smoke check: keep it opt-in and bounded; default verification must stay offline.

## Known tradeoffs

- Postgres-first is slower to outgrow than a lightweight local file store and less complex than Kafka/ClickHouse-first.
- Executed-trades-first misses order-book-only anomalies but avoids the fragility of full book reconstruction before the data model is proven.
- Transparent rules are less glamorous than ML, but they are testable, explainable, and easier to debug.
- Local-only scope delays hosted/team workflows by design.

## Open risks

- Feed schemas and rate limits may change.
- Market identifiers and equivalent contracts across venues may not map cleanly.
- Thin markets can produce exaggerated percentile signals.
- Alert usefulness cannot be known until backtested against real historical activity.
