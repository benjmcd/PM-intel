# Spec — Cross-leg co-fire grouping/suppression (M-ALERT-COFIRE) — 2026-07-05

> **SUPERSEDED CURRENT-STATE BANNER (2026-07-06):** This spec is retained as historical design context only. Current co-fire authority is ADR-0009 plus `plans/2026-07-06-state-and-roadmap.md`; `origin/main=4ea003e` has PRs #97/#98 merged. The shipped surface is read-side grouped triage, default-OFF, with no emit-path or DB-schema change. Current facts: reval `LABELING_RULE v1.2`, `tp=22`, both post-close ids are noise in the reval JSON; labeler proposal rule is v1.3; 81-tp is a lane mismatch, not a defect; DB re-ratification remains held.

> **Status:** DESIGN / PROPOSAL — **agent2 adversarial review incorporated 2026-07-06** (criteria hardened; agent2 explicitly does NOT endorse R3 implementation from this spec until M-ALERT-COFIRE-0 settles key granularity + window). Not authorized for implementation. Gated on: (a) M-ALERT-COFIRE-0 (read-only semantics classifier) — ✅ **DONE 2026-07-06, cross-verified** (`reports/alert-quality/co-fire-semantics-2026-07-06.md`: window 900 s, key **event_ticker** [match_key REJECTED—over-groups], pairwise-radius clustering, leg-aware visibility mandatory), (b) M-ALERT-COFIRE-1 (generalized offline harness) existing, (c) operator greenlight.
>
> **Category:** alert-emission semantics — a repo-fenced category requiring separate authorization (per `state/agent-inbox/HANDOFF-agent1-*`, AGENTS.md planning threshold). This spec is the required plan artifact; a decision block (below) records the consensus method; an ADR is required only if the chosen design changes the DB schema.

## Goal
Remove the `cross_market_hedge` false-positive class from fp-governance **without dropping a single labeled true positive**, by recognizing at fire time that many co-firing legs of one Kalshi event are one basket, not N independent signals.

## Problem (grounded)
- 12 of 13 recorded fps are `cross_market_hedge`. Root event example: `KXWCGAME-26JUN20GERCIV` fired across GER/CIV/TIE/corners/totals/1H-total legs and across 4 rules (directional_cluster, momentum, market_relative, volume_spike).
- Both current BREACHes (`volume_spike_v1` 31.5%, `directional_cluster_v1` 19.7%) are the same structural pattern through two rules.
- The detector has no fire-time feature distinguishing "one hedged basket" from "N informed trades," so no single-rule threshold can fix it.

## Current state (code reality — must be preserved)
- Emission: per-trade synchronous, raw-payload-first (`runner.py:process_event`: `insert_raw_event` `:191`, `insert_trade` `:388`, both before `engine.evaluate` `:413`).
- Existing suppression tier: in-memory + DB-backed, key `(venue_code, market_id, rule_id, outcome_key)`, event-time window `suppression.default_window_seconds: 300` (`runner.py:426-436`, `alerts.py:966-993`). **Co-fire logic is a NEW tier layered on this; it must not alter or regress single-leg suppression.**
- No event key stored: `markets.venue_event_id` exists but is always NULL (`markets.py:23-31`/`59-81` never write it; `git grep venue_event_id` → only the DDL). **OPEN DECISION (agent2, do not treat as solved):** `venue_market_id.rsplit("-",1)[0]` yields the Kalshi *event_ticker*, but that **under-groups the match basket** — `KXWCGAME-26JUN20GERCIV`, `KXWC1HTOTAL-26JUN20GERCIV`, `KXWCCORNERS-26JUN20GERCIV` are one match's hedge exposure yet split to three keys (the labeling script admits this, `fetch_outcomes.py:39-40`). The basket key is therefore either the event_ticker **or** a broader **match key** (`segments[1]`). **R0 RESOLVED (2026-07-06, cross-verified two passes, `reports/alert-quality/co-fire-semantics-2026-07-06.md`):** **window = 900 s** (300 s misses 7/33 labeled hedge pairs, all on `39bd1f35`; 0 missed at 900 s); **key = `event_ticker`** — CLEAR choice (== Kalshi `facts.event_ticker` 43/43; unifies every labeled hedge cluster with zero under- AND zero over-grouping). **`match_key = segments[1]` REJECTED** — it over-groups cross-market-type markets (`KXWCGAME`/`KXWC1HTOTAL`/`KXWCCORNERS`-`GERCIV`) that the labeling rule excludes by design (`fetch_outcomes.py:39-40`), gives no unification benefit, fragile derivation. The cross-market-type "match basket" (agent2's concern) is an UNVALIDATED HYPOTHESIS needing a **labeling-rule extension + new labels**, not a key swap. **Clustering = pairwise-radius** (match ground truth `fetch_outcomes.py:244-249`), NOT consecutive-chain. **Leg-aware visibility MANDATORY** (one event holds both tp + fp legs, timing-separated by coincidence only; tp+fp groups = 0 here is fragile). Polymarket = 1 no-op. Kalshi-scoped.
- Alert schema (`sql/001_init.sql:287-312`): no group/parent field today. `evidence jsonb` is the low-friction place to record co-fire metadata without DDL.

## Non-goals
- No change to Polymarket emission. No change to single-leg 300s suppression. No threshold retuning (esp. `min_baseline_trades` — proven tp-killing). No live-API dependence. No delivery/notification changes. No schema change *unless* the group/rollup design is chosen and adversarial review confirms `evidence` jsonb is insufficient.

## Decision method (Talmudic — per docs/adr/0008, docs/governance/12)

**Question:** How to neutralize cross-leg hedge fps at fire time with zero tp-loss and minimal blast radius?

**Candidate A — Annotate-only (governance-side or evidence-tag).**
- *Strongest case:* zero emission-behavior change; add `evidence.co_fire_group = {event_ticker, sibling_count, window_s}` (and/or compute at governance time). fp-rate gains a hedge-basket-aware cohort. Reversible, cannot drop a tp by construction. Smallest diff. Directly extends M-GAUGE-HONESTY.
- *Objection:* does not reduce operator queue volume — all 8 legs still fire; it makes the breach *interpretable*, not *smaller*. If the goal is only honest measurement, this suffices; if the goal is a quieter queue, it does not.

**Candidate B — Group/rollup (N legs → 1 parent alert).**
- *Strongest case:* collapses a basket to one operator-facing item carrying all legs as evidence → cuts fp *count* ~Nx while **dropping nothing** (the winning leg is still inside the group) ⇒ structurally zero tp-loss. Best queue reduction with best safety.
- *Objection:* needs a grouping identity (parent_alert_id or a co_fire_group_id) — likely a schema addition (or an `evidence`-only soft-group), plus governance/review-UI changes to label a group. Higher complexity; must define the within-window buffering without breaking per-trade synchronous emission and raw lineage.

**Candidate C — Suppress-keep-one (extend existing suppression).**
- *Strongest case:* simplest code — add an event-level key tier to the existing suppression cache; when ≥2 distinct legs of one event fire within window, keep the highest-conviction leg, drop the rest.
- *Objection:* **tp-loss risk** — "conviction" (score) does not predict the settlement winner; dropping the wrong leg drops a labeled tp (e.g. `92c182df` GERCIV-GER = tp co-fired with hedge legs). Fails the hard gate unless a keep-rule can be proven zero-tp-loss offline, which is unlikely for a lossy drop.

**Consensus (post-agent2-review, incorporated 2026-07-06 from `for-claude-agent2-2026-07-05.md`):** Lead with **A now**; pursue **B** as the gated detection-time fix *if* queue-volume reduction is wanted. **Reject C** as lead design (lossy). **agent2 correction, accepted:** B is **NOT zero-tp-loss "by construction"** — mixed-label groups exist (GERCIV basket has fp `97ee5ccb` + tp `92c182df`/`d1e48c62`), so one parent labeled "fp" can make a real tp **invisible to governance** even with no row dropped. Zero-tp-loss must therefore cover **visibility**, not just non-deletion. agent2 does **not** agree R3 is ready for implementation from this spec — a read-only semantics classifier (M-ALERT-COFIRE-0) must run first to settle key granularity + window.

**Payback artifact:** read-only semantics classifier (M-COFIRE-0) → generalized offline replay (M-COFIRE-1) + a co-fire fixture test using `tests/fixtures/raw/polymarket_cluster_{a,b,c}.json` shape adapted to a Kalshi multi-leg event.

**Review outcome:** agent2 CONFIRMED the diagnosis + A>B>C ranking + measurement-before-enforcement; REFUTED "single-transaction" (each insert wraps own txn); found under-grouping (match-key), mixed-label hidden-tp, and the 300s-vs-15min window mismatch (all observed, cited). Criteria hardened below.

## Proposed design (Candidate B, for review — historical)
- **Historical proposal, not current implementation.** It considered deriving `event_ticker` at emission, using an emit-time co-fire tier, and possibly writing soft-group metadata. That path is superseded.
- **Current shipped design:** derive/group on read, with a 900 s inclusive pairwise-radius grouped triage view behind `--group-cofire`; no emit-path write, no DB schema change, and no group-level review label.
- **Current labeler proposal rule:** `LABELING_RULE v1.3` treats hedge membership as a caveat on an outcome-evaluated leg rather than a pre-outcome shortcut.
- Preserve every leg's raw/normalized lineage rows unchanged (raw-payload-first intact); grouping remains a presentation/governance concern, not a capture concern.

## Files likely to change (Candidate B) — historical
The shipped read-side path did not require the emit/storage changes listed in the original proposal. Current authority for shipped files is ADR-0009 and `plans/2026-07-06-state-and-roadmap.md`.

| File | Expected change |
|---|---|
| `src/pmfi/pipeline/runner.py` | New co-fire tier in `process_event` around the existing suppression check (`:426-436`); event-key derivation; buffered group assembly within window. |
| `src/pmfi/db/repos/markets.py` | Populate `venue_event_id` (Kalshi) in `upsert_market*`. |
| `src/pmfi/db/repos/alerts.py` | `evidence.co_fire_group` write (+ optional `co_fire_group_id` if DDL chosen). |
| `src/pmfi/pipeline/rules.py` | (only if the window/definition needs a shared helper) — no threshold changes. |
| `src/pmfi/data_reports.py` / `commands/alerts.py` | co-fire-aware fp-rate cohort (R2). |
| `src/pmfi/replay.py`, `calibration.py` | generalized comparator + engine-variant flag (R1 — prerequisite). |
| `tests/` | co-fire fixture test (no DB) + offline zero-tp-loss cohort test. |
| `config/alert_rules.yaml` | at most a new `co_fire:` block (window, min_distinct_legs) — additive, no threshold retune. |
| `sql/0NN_*.sql` | only if a group column is chosen (ADR required then). |

## Milestones

### M-ALERT-COFIRE-0 — Read-only semantics classifier — ✅ DONE 2026-07-06 (`reports/alert-quality/co-fire-semantics-2026-07-06.md`)
- [x] Read-only analysis over `outcomes-2026-07-02.json` (44 labels). No engine change, no DB write, no live API.
- [x] Key-derivation table computed over the real tickers (`KXWCGAME`/`KXWC1HTOTAL`/`KXWCCORNERS`-`26JUN20GERCIV`, `KXBTC15M-…`, `KXBTCD-…`, `KXT20MATCH-…`, `KXMVESPORTS…`) — 43/43 Kalshi tickers are 3-segment; `event_ticker` vs `match_key=segments[1]` both derived.
- [x] Grouping measured under 300 s × 900 s × both keys: `missed_labeled_hedge_fp`, mixed-label groups (3–4, pooling 8/16 tp), singleton-tp control (`36cdc737` ungrouped ✓), Polymarket no-op (1).
- **SETTLED (cross-verified 2 passes):** window **900 s**; key **event_ticker** (== `facts.event_ticker` 43/43); **match_key REJECTED** (over-groups vs the labels' event_ticker-scoped hedge definition); clustering **pairwise-radius** (not consecutive-chain); **leg-aware visibility mandatory**.
- **Remaining (deferred to R1/R3 when code lands):** lock the `event_ticker` derivation with a table test; if the cross-market-type "match basket" is ever pursued, it needs a labeling-rule EXTENSION + new labels first (not a key swap). Re-validate constants on a larger post-07-02 cohort (n=44/3 clusters/33 pairs, defects each driven by the single GERCIV event).

### M-ALERT-COFIRE-1 — Harness generalization (built around M-COFIRE-0's settled semantics)
- [ ] Generalize `replay_from_db`/`volume_spike_calibration.py`/`calibration.py` to arbitrary `rule_key` + **injectable engine variant** (`replay.py:159` currently hard-constructs `AlertEngine(rules_config=...)` — needs a variant injection point; agent2 #4).
- **Acceptance:** reproduces existing volume_spike calibration packet byte-identically; evaluates a directional candidate; offline-only.
- **Verification:** `scripts\verify.py` green; new parity test.

### M-ALERT-COFIRE-2 — Annotation + co-fire-aware governance (R2, safe, leg-aware)
- [ ] `evidence.co_fire_group` derivation (using M-COFIRE-0's chosen key) + fp-rate hedge-basket cohort.
- **Acceptance:** the 12 labeled `cross_market_hedge` rows recognized; zero change to what fires; **per-leg labels still countable** (agent2 #4 hardening); offline-validated against 44 labels; DB fingerprint unchanged.

### M-ALERT-COFIRE-3 — Operator-facing grouped VIEW (R3) — **ARCHITECTURE REFRAMED 2026-07-06 (post-R1 PASS): read-side, not emit-suppression**

**Key realization (from R0/R1 = Candidate B drops nothing):** R3 is **NOT** a suppression-at-emit change. Every leg still fires + is stored (leg-visibility). The queue-reduction win (44→22) is delivered by **grouping the operator's READ/TRIAGE view**, not by changing emission. So R3 touches the **read/delivery layer**, and live emission (`runner.py`/`engine.py`) stays **byte-identical**. This dissolves most of the "fenced emission" risk — R3 is closer to R2 (read-side) in risk profile.

**Talmudic — where does grouping happen?**
- *Option buffer-and-flush (rejected):* hold alerts in a 900 s buffer at emit, group on flush. Objection: delays real-time alerts up to 900 s, adds buffering state + crash-recovery risk, changes emission timing. Fails the "no emission delay / raw-first / no new durable state" bar.
- *Option annotate-at-emit + group-at-VIEW (chosen):* every leg fires immediately (unchanged); the operator's **read surfaces** (`alerts list` @ `alerts.py:458`, `review-packet` @ `:814`) gain a flag-gated grouped mode that applies the merged `cofire.group_cofire` to the fetched rows and renders one operator item per event-group with **leg drill-down**. `event_ticker` is **derived on-read** (`cofire.derive_event_ticker` from the already-stored `venue_market_id`) — no emit-side write, no schema change. Consensus: this delivers the reduction, preserves every leg + its lineage + per-leg reviews/governance, is reversible (flag off = today's ungrouped view), and touches nothing in the emission path.

**Scope (read-side only):**
- [ ] `alerts list` + `review-packet` gain `--group-cofire` (config `co_fire.grouping_enabled`, **default OFF**) applying `cofire.group_cofire` (event_ticker, 900 s pairwise-radius) to fetched rows; grouped render = one line per event-group (group size, rules, worst severity) + `--expand`/drill-down to legs.
- [ ] `event_ticker` derived on-read via `cofire`; **no** emit-side change, **no** schema change.
- [ ] **Governance stays leg-level:** reviews remain per `alert_id`; the grouped view is triage presentation only. If a "review whole group" convenience is added, it writes **per-leg** `alert_reviews` rows (never a single group label) so fp-rate math (R2) is unchanged.
- **Acceptance (pass/fail):**
  - Flag OFF → `alerts list`/`review-packet` output **byte-identical** to today (regression test).
  - Flag ON → co-firing legs of one event collapse to one operator item; **every leg still individually listable** (`--expand`); the 44→22 / fp 13→5 reduction reproduced against the labeled cohort via the merged `cofire`; singleton-tp `36cdc737` shown standalone; Polymarket alert ungrouped.
  - **`runner.py`/`engine.py`/`rules.py` byte-identical** (`git diff` empty) — no emission change; `cofire` still not imported by any emit path.
  - Per-leg `alert_reviews` semantics unchanged; fp-rate (R2) output identical for the same reviews.
  - `scripts\verify.py` green; DB read-only (fingerprint unchanged).
- **Verification:** orchestrator Opus verification workflow (flag-off byte-identical mutation check + flag-on grouping correctness against the cohort) + fence diff + agent2 adversarial review.
- **Gate:** operator greenlit 2026-07-06 ("Proceed"). Delivered behind a **default-OFF flag** so the operator flips it when satisfied.

#### agent2 adversarial-review hardening (2026-07-06, `for-claude-agent2-2026-07-06.md`) — MANDATORY before merge
Verdict: R1 PASS is "cohort-viable, not a live-operator-safety proof"; R3 read-side is the right direction but **not yet merge-safe** without these. All confirmed against code:
1. **Pagination / boundary partial-groups (REFUTED as first-specified — the critical one).** `alerts list` applies `--since` + `ORDER BY fired_at DESC LIMIT` **before** grouping (`alerts.py:493-578`); `review-packet` limits before export (`db/repos/alerts.py:596-632`). So a co-firing sibling truncated out of the fetch window shows a **partial group as complete** (`--since 20:40` includes `39bd1f35`@20:48, excludes `623164c5`@20:35). **FIX:** overfetch a boundary window (`since − window_s`, extra rows beyond `limit`) → group → apply the operator limit **after** grouping; if completeness can't be proven, render **`partial_group=true` + hidden-sibling count** and exclude partial groups from the 44→22 claim. Add since-boundary + limit-boundary tests.
2. **Group-review semantics.** **Forbid "label whole group"** in R3 (mixed tp/noise groups exist). Require explicit expanded per-leg selection; any bulk action must show every `alert_id` + current review state and **refuse mixed-label groups** by default. Reviews stay one `alert_reviews` row per leg; R2 fp-rate byte-identical for identical reviews.
3. **Exhaustive flag-OFF acceptance.** Byte-identical must cover: table output, JSON output, review-packet JSON schema/content, **all existing filters + error paths** — not just the happy path.
4. **Window boundary `<` → `<=` (R1 fix).** `cofire.group_cofire` uses `delta < window_s` but the labeling authority is **inclusive** `<= GROUP_MIN*60` (`fetch_outcomes.py:249`). Change `cofire.py` (and `validate_cofire.py`) to `<=`; add an exact-`delta==900` test. Also guard **>3-segment** Kalshi tickers in `derive_event_ticker` (currently only <3 rejected).
5. **Validation gate rigor (R1 fix).** `validate_cofire._candidate_hedge_pairs` rebuilds fp-only pairs, not the artifact's actual `hedge_group.with` graph (misses e.g. `fa202af3`→noise siblings). Re-derive the expected-pair set from the real `hedge_group.with` edges.
6. **View-level tp-visibility gate.** Beyond the primitive's leg-retention, assert: same `alert_id` set before/after grouping; every tp `alert_id` present in default expanded JSON + drill-down; no top-level group label replaces per-leg labels.

**Merge policy:** items 1-3 + 6 gate the R3 PR (grouped view must not ship a known partial-group/bulk-label hazard even behind a flag). Items 4-5 are small fixes to the merged R1 primitive/harness (latent on n=44 but real) — bundle into the same hardening pass. R1 PASS alone is **not** merge authorization for the grouped view.

## Risks / implications
- **tp-loss (highest):** mitigated by design choice (B drops nothing) + the hard offline gate. Any design failing the gate is rejected.
- **Window mismatch:** resolved for the shipped read-side grouped view as 900 s inclusive pairwise-radius. The 300 s window is insufficient for co-fire and remains only the separate single-leg suppression window.
- **Kalshi ticker-grammar assumption:** `rsplit("-",1)` assumes the leg suffix grammar; must validate against real tickers (BTC15m, corners, totals use different suffixes) — a bad split over-groups. Needs a tested derivation, not a naive split.
- **Raw lineage:** unaffected by design (grouping is post-capture) — must stay that way.
- **Small cohort:** n=61 directional; grow via R1 labeling before trusting the enforcement decision.
