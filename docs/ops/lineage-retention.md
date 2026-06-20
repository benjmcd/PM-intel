# Alert Lineage And Retention

PMFI stores `alerts.raw_event_id` and `alerts.trade_id` as informational lineage pointers. They are not foreign keys because `raw_events` and `normalized_trades` are range-partitioned by timestamp, while alerts are not partitioned on the same key.

Retention ordering is explicit:

- Raw and normalized partitions are retained until the operator opts in to daemon pruning with both retention flags.
- Alerts may outlive dropped raw/trade partitions.
- Dangling alert lineage is reported, not deleted or repaired automatically.

Use this read-only check after retention maintenance, before long unattended runs, or when reviewing older alerts:

```powershell
python scripts\task.py lineage-check --format table
python scripts\task.py lineage-check --since 7d --format json
python scripts\task.py lineage-check --strict
```

`--strict` exits nonzero when any dangling `raw_event_id` or `trade_id` reference is found. That is an operator signal to treat the affected alert as missing raw/trade drill-down evidence; it is not permission to delete alerts.
