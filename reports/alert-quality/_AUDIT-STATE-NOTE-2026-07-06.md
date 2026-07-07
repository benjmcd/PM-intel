# Co-fire alert-quality artifacts — audit state note (2026-07-06)

> **UPDATE (post-merge, origin/main `4ea003e`):** PRs #98/#97 landed the non-DB fixes. Current repo state: `outcomes-cofire-reval-2026-07-06.json` is `LABELING_RULE v1.2`, `tp=22`, and both `9934a6e1` + `9a357683` are noise; the three `co-fire-validation-reval-*.md` reports were regenerated to `tp=22`; the v1.1 new-label artifacts carry SUPERSEDED banners; the 44-cohort has an `_audit_note`; the R3 labeler was narrowed to v1.3; `breach-denominators-2026-07-06.md` records `31.5% = 28/89`; `cofire-losing-leg-adjudication-2026-07-06.md` records the 25-leg worklist. **STILL OPEN (operator-gated):** DB `alert_reviews` continues to serve `9934a6e1`+`9a357683` as `tp` until DB re-ratification; JSON-vs-DB divergence persists.

**Read this before trusting the older `*-reval-*` artifacts in this directory.** The canonical, self-contained record is `plans/2026-07-06-state-and-roadmap.md` §12–§16; this note is a directory-local pointer so frozen/superseded artifacts are not read as current.

## Current ground truth
- The enlarged co-fire re-validation cohort (`outcomes-cofire-reval-2026-07-06.json`) has **two** post-close-fire settlement-only ids that are noise under LABELING_RULE v1.2: **`9934a6e1`** and **`9a357683`**. The current v1.2 count is **tp=22**, not 23 or 24.
- `9a357683` = `directional_cluster_v1`, market `KXWCSCORE-26JUN18CANQAT-CAN6QAT0`, fired ~38 min AFTER its live `facts.close_time`; it is now noise in the reval JSON, still frozen as tp in the immutable v1.1 44-cohort snapshot, and still tp in DB `alert_reviews` until operator ratification.

## Per-artifact status
| Artifact | Status |
|---|---|
| `co-fire-reval-report-2026-07-06.md` | **CURRENT** after this doc pass: v1.2, `fp=64`, `noise=24`, `tp=22`; gate text reads `tp_visible=22/22`. |
| `co-fire-validation-reval-2026-07-06.md` (+ `-w300`, `-w1800`) | **CURRENT** regenerated v1.2 reports; label row is `fp=64, noise=24, tp=22`. |
| `cofire-reval-new-labels-2026-07-06.md` | **SUPERSEDED historical v1.1 auto-labeler output**; header/body remain v1.1 by design and must not be used as current labels. |
| `outcomes-cofire-reval-new-2026-07-06.json` | **SUPERSEDED historical v1.1 auto-labeler output**; top-level `_superseded_note` routes readers here/current reval JSON. |
| `outcomes-cofire-reval-2026-07-06.json` | **CURRENT reval JSON**: top-level `rule=LABELING_RULE v1.2`; `9934a6e1=noise`; `9a357683=noise`; `tp=22`. |
| `outcomes-2026-07-02.json` | The immutable v1.1 44-cohort snapshot; it carries `_audit_note` warning that both post-close ids are noise under v1.2 and are not corrected here by design. |
| `co-fire-validation-2026-07-06.md` | Frozen 44-cohort validation report (`tp=16`, `tp_leg_visible=16/16`); historical v1.1 evidence only. |

## Other recorded findings (full detail in plan §12–§15)
- **`review-packet --group-cofire` zero-reduction bug is fixed in #98**; live read-only smoke reported `non_partial_reduction=185`, `operator_group_count=62`, `partial_group_count=8`.
- **81 DB `tp` vs R3 = a lane mismatch, NOT a defect** (§15): R3 (`cross_market_hedge`) is a **non-authoritative proposal** in `fetch_outcomes.py`; ADR-0009 adopts grouping but declines a hedge label. Only 15/81 rest on settlement survivorship; 66/81 on fire-time flow/magnitude evidence. Recommendation = HYBRID (narrow R3, document, adjudicate a few losing legs — no mass relabel). KXBTCD is a directional ladder R3 would wrongly fp.
- **`volume_spike` "31.5%" BREACH figure is reproducible** on the live DB as `28/89` with `this_trade_usd>=850`; all-reviewed is `77/138 = 55.8%`; the earlier `51.6%` floor-conditioned figure does not reproduce. `directional_cluster` 19.7% reproduces; `momentum_v1` is 11.1%.
- **Assumptions that do NOT hold:** "900s generalizes" (MEXKOR fragments across a 1676s gap), "reval-set complete" (curated; 81 DB tp unexamined vs R3), "only 2 post-close tp" (true cohort-locally, unverified DB-wide).
- **Confirmed clean:** current reval gate reproduces (`tp_visible=22/22`), DB verify passed in the #98 lane, emission fence clean, multi-leg 4-event set complete (triple-method), exactly two cohort post-close ids via exhaustive symmetric v1.2 re-derivation.

## Remaining held work
DB re-ratification is ratification-gated; `alert_reviews` still serves `9934a6e1` + `9a357683` as tp until explicit operator approval. `momentum_v1` scope, flipping grouped co-fire on for routine triage, R3-emit, and per-leg adjudication of the 25-leg losing subset remain operator-owned. No DB writes or mass relabeling have been made.
