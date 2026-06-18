"""Local ingest-metrics dashboard (rate/volume per venue plus alert review).

Read endpoints remain non-mutating. Alert review writes are a narrow localhost-only
append-only POST path into alert_reviews.
"""
