"""Local read-only ingest-metrics dashboard (rate/volume per venue).

Phase 1 exposes JSON query helpers + localhost HTTP endpoints; a browser UI is
layered on later. All queries are read-only against the existing Postgres tables.
"""
