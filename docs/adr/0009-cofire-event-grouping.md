# ADR 0009: Co-fire event grouping for the alert triage view

## Status

Accepted (2026-07-06).

## Context

`volume_spike_v1` (current-floor 31.5%) and `directional_cluster_v1` (19.7%) breached their FP-governance targets after the M-TRUTH settlement re-baseline. Root cause is structural: a single Kalshi event (e.g. a World-Cup match) fires many legs at once — game winner, 1st-half total, corners, score — most of which are `cross_market_hedge` false positives that are invisible to the fire-time detector features. No per-rule threshold can separate a hedged basket from N independent informed trades. On the 44-alert labeled settlement cohort, 12 of 13 FPs are `cross_market_hedge`, all Kalshi multi-leg events.

## Decision

Group co-firing legs of one event at the operator **triage / read layer — not at emission**.

- **Key = Kalshi `event_ticker`** (`venue_market_id` minus the trailing outcome segment; equals the venue's own `facts.event_ticker`, verified 43/43 on the cohort). A broader "match key" (the shared date+teams token) was **rejected**: it over-groups cross-market-type markets that the labeling rule deliberately excludes, provides no unification benefit on the actual labeled clusters, and has a fragile derivation. Polymarket has no multi-leg convention, so it is a no-op there.
- **Window = 900 s, inclusive, pairwise-radius (transitive).** This matches the M-TRUTH labeling authority (`GROUP_MIN = 15 min`, `<=`). 300 s was proven insufficient — it misses a labeled hedge pair fired 726 s apart.
- **Grouping strategy = "co-present, drop nothing".** Every leg still fires and is stored; the grouped view collapses co-firing legs into one operator item with per-leg drill-down (`--expand`). A lossy "suppress and keep one" strategy was **rejected** — score/conviction does not predict the settlement winner, so dropping a leg risks dropping a true positive.
- **Leg-aware visibility is mandatory.** Reviews stay keyed per `alert_id`; there is no group-level review label; grouped views always expose every leg; groups whose siblings may be truncated by `--since`/`--limit` fetch bounds are flagged `partial_group=true` (never shown as complete) and excluded from reduction counts.
- **Delivered behind a default-OFF flag** (`alerts list --group-cofire` / `review-packet --group-cofire`). Live emission (`runner.py`/`engine.py`/`rules.py`) is byte-identical; the co-fire module is imported only by the read path.

Validated offline against the labeled cohort (`reports/alert-quality/validate_cofire.py`): `removed_tp = 0`, `tp_leg_visible = 16/16`, `missed_labeled_hedge_fp = 0`; operator queue 44 → 22; FP operator items 13 → 5.

## Consequences

- The operator queue for hedged baskets roughly halves with **zero true-positive loss**. Governance (`alerts fp-rate`) is unchanged because reviews remain per-leg; it additionally attributes each breach's `cross_market_hedge` share (M-COFIRE-GOV).
- The co-fire primitive (`src/pmfi/pipeline/cofire.py`) is a standalone, pure, offline-validated module that is **not** wired into the emission path. Wiring it live would be a separate, gated decision.
- Scoped to Kalshi. The cross-market-type "match basket" is an unvalidated hypothesis that would require a labeling-rule extension plus new labels before adoption.
- Constants (900 s window, `event_ticker` key, +50-row overfetch) were validated on n=44, where each observed defect is driven by a single event; re-validate on a larger cohort before treating them as durable market-grammar proof.
- Follows the Talmudic/orthogonal decision method (ADR 0008). Full design, acceptance criteria, and the adversarial-review trail: `plans/2026-07-05-cofire-suppression-spec.md`, `reports/alert-quality/co-fire-semantics-2026-07-06.md`; shipped in PRs #88 (governance), #89 (offline validation), #90 (read-side view).

## Post-adoption audit note (2026-07-06)

A multi-pass audit (2 Codex + several Opus/mixed workflows) clarified this ADR; the Decision stands. Follow-on fixes landed in PRs #97/#98 (origin/main `4ea003e`). Full detail: `plans/2026-07-06-state-and-roadmap.md` §12–§16.

- **"Zero true-positive loss" (Consequences) is a Candidate-B *structural invariant*, not semantic no-loss proof** — `removed_tp=0` / `tp_leg_visible=N/N` are unfailable identities under drop-nothing grouping (they attest leg retention). The substantive diagnostic is `parent_label_conflicts` (mixed-label groups → per-leg review mandatory); the window-sensitive lower-bound gate is the hardcoded GERCIV anchor-pair check in `validate_cofire.py`, not `missed_labeled_hedge_fp` (0-by-construction when candidate and grouping windows match). Read as "zero TP leg-*visibility* loss in the validated cohort."
- **"Grouped views always expose every leg" (Decision) is precise only with `--expand`** (default output = group summaries + leg IDs + partial indicators). A `review-packet --group-cofire` reduction=0 bug (a mandatory-default `review_state` forcing `filter_boundary`) was found by live run and **fixed in #98**.
- **The R3 hedge LABEL is a non-authoritative auto-proposal in `fetch_outcomes.py`, not adopted by this ADR** (which correctly declines a group hedge label). The 81 co-firing DB `tp` are a proposal-vs-ratified *lane mismatch* — only 15/81 rest on settlement survivorship; 66/81 on fire-time flow/magnitude evidence (§15). R3 was **narrowed to v1.3 in #97** (hedge_group is now a caveat on an outcome-evaluated leg, not a pre-outcome short-circuit; asserts `cross_market_hedge` only with ≥2 opposite-side legs or size-symmetry) — this touches only the labeler, no ratified DB label. A **25-leg** survivorship-flattered losing-leg worklist awaits per-leg operator adjudication; DB re-ratification of the two post-close mislabels (`9934a6e1`,`9a357683`) remains operator-gated.
- **Numbers confirmed (#97):** the Context `31.5%` volume_spike figure **reproduces** (`28/89`, floor `this_trade_usd>=850`; all-reviewed `55.8%`); `directional_cluster` `12/61=19.7%`; `momentum_v1` `4/36=11.1%` (out-of-frame, below target). Constants remain n≈110-small; 900 s does **not** bind a whole real multi-market event (MEXKOR fragments across a 1676 s gap).
