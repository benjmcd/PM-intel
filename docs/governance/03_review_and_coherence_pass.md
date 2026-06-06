# 03 — Review and Coherence Pass

Use this pass after meaningful implementation, before marking a milestone complete, or when a fresh agent inherits unclear state.

## Review questions

1. Does the implementation preserve local-only scope?
2. Does it preserve raw evidence lineage or clearly document why a temporary spike has not yet reached persistence?
3. Does it improve executable truth: tests, CLI behavior, schema, fixture replay, or DB verification?
4. Are docs/plans helpful for the next agent, or are they stale ceremony?
5. Did any top-down spike get paid back with tests/contracts/schema/fixtures/interfaces or a precise blocker?
6. Does the change preserve modularity, venue isolation, Postgres-first storage, and replayability?
7. Are failures/degraded states represented explicitly rather than hidden?
8. For unclear architecture/data/orchestration decisions, was at least one orthogonal framing considered or consciously skipped because the path was obvious?
9. If Talmudic debate was warranted, did it produce a concise consensus and validation target rather than ceremony?
10. Did the work produce material results rather than low-impact governance churn?

## Output

Record concise findings in `WORKLOG.md`:

```markdown
### Coherence pass
- Checks run:
- Product progress:
- Architecture/data risks:
- Orthogonal/debate decision used or skipped:
- Local-only scope status:
- Next highest-leverage action:
```
