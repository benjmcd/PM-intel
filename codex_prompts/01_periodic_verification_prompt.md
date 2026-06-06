# Periodic Verification Prompt

```text
Stop building and run a verification pass. Re-read AGENTS.md, docs/governance/02_verification_cadence.md, docs/governance/03_review_and_coherence_pass.md, and the current milestone in docs/implementation/01_bottom_up_work_orders.md.

Run `python scripts\verify.py` and any milestone-specific gate that exists. Inspect whether code, tests, SQL, configs, and docs still align with the product goal: local-first, Postgres-first, raw-before-derived, replayable, explainable anomaly alerts.

Append a verification/coherence note to WORKLOG.md. If anything is red, fix the narrowest cause before continuing. If all is green, identify the next smallest slice.
```
