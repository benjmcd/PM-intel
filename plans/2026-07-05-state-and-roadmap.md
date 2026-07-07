# PMFI — Master Context, State of Record & Forward Roadmap (2026-07-05 → 2026-07-06)

> **SUPERSEDED CURRENT-STATE BANNER (2026-07-06):** This file is retained as historical planning context only. Current co-fire authority is `plans/2026-07-06-state-and-roadmap.md` plus ADR-0009; `origin/main=4ea003e` has PRs #97/#98 merged. Current facts: reval `LABELING_RULE v1.2`, `tp=22`, both `9934a6e1` and `9a357683` are noise in the reval JSON; labeler proposal rule is v1.3; 81-tp is a lane mismatch, not a defect; DB re-ratification remains held.

> **Purpose.** Historical canonical record for the 2026-07-05 through early-2026-07-06 planning state: (a) **what PMFI is** (domain + architecture, §0.5); (b) **what had been accomplished/verified** (§1); (c) the then-current state (§2); (d) loose ends (§3); (e) intended next work and why each choice was adequate (§4); (f) the orchestration/delegation model (§5); (g) operational/hardware/safety guardrails (§6); (h) acceptance-criteria conventions (§7); (i) open operator decisions (§8); (j) the decision ledger with alternatives + justification (§10); (k) glossary (§11); (l) risk register (§12). Current plan-of-record is `plans/2026-07-06-state-and-roadmap.md`. `WORKLOG.md` = *verified code progress only*; `docs/` = durable architecture/ADRs; `MEMORY.md` = orchestrator cross-session pointer.
>
> **Historical status at this file's superseded anchor (not current):** `origin/main = 5957c69` (co-fire program + artifacts through PR #92); suite `1386 passed / 94 skipped / 0 failed`; 0 open PRs. Later authority is `origin/main=4ea003e` with #97/#98 merged; see the banner above and `plans/2026-07-06-state-and-roadmap.md`. The rest of this paragraph records the old anchor: co-fire read-side `--group-cofire`/`--expand` grouped view at `alerts list` + `review-packet`; live emission byte-identical; queue reduction (44→22) at the read layer, flag default-OFF.
>
> **Provenance.** Grounded in: repo at `origin/main = f98c222` (was `a487ede` when first authored); digests of 3 session logs (Claude orchestrator `4974866c` 2026-07-02; Codex `019f2203`=M-REVIEW-BURNDOWN; `019f2204`=M-GAUGE-HONESTY); a 4-agent code-recon workflow; agent2's adversarial plan review (`for-claude-agent2-2026-07-05.md`); two cross-verified R0 measurement passes; agent1's merged closeout (PR #87). Every load-bearing claim cites a file:line, commit, PR#, or measured value. Corrections applied after self-verification are noted inline (verify-don't-trust — the record shows its own error-corrections rather than hiding them).

---

## 0. TL;DR

- The product is **feature-complete, hardened, and green** (`scripts\verify.py` = 1349 passed / 94 skipped / 0 failed on `f98c222`; #84/#85/#86 merged 2026-07-02, #87 on 2026-07-06).
- For the first time, alert governance is **honest and outcome-grounded**: 44 real alerts labeled from settlement truth (16 tp / 13 fp / 15 noise), current-floor cohort headlined.
- That honesty surfaced the real next problem: **two fp-governance BREACHes whose root cause is structural** — a single Kalshi multi-leg event (e.g. a World-Cup match) fires many co-firing "hedge" legs the detector cannot recognize as one basket at fire time.
- **Recommended forward path (measurement-before-enforcement):** (1) generalize the offline validation harness + operationalize outcome-labeling [safe, foundational, no emission risk]; (2) make fp-governance co-fire-aware [honesty]; (3) *only then*, gated on operator greenlight + zero-tp-loss offline proof, design co-fire grouping/suppression at emission. Plus bounded hygiene (dropped review-thread closeout, dirty sandbox config, worktree/venv sprawl).
- **Delegation:** Claude authors/verifies plans + owns merges; Codex executes plans and supplies adversarial review; workflows (Sonnet/Haiku) do recon/verification. Self-verification of every delegated output is mandatory.

---

## 0.5 Orientation — what PMFI is (self-contained primer)

**Product.** PMFI (prediction-market flow intelligence) is a **Windows-native, local-only** tool that captures public market events from Polymarket/Kalshi-style venues, preserves raw external payloads, normalizes trades, computes rolling baselines, and emits **explainable local anomaly alerts**. Runtime Python 3.11+; storage **Postgres-first** (Docker Desktop locally). It is **local-only for the current build horizon** — no SaaS/hosted/billing/RBAC/external-notification (see `LOCAL_ONLY_SCOPE.md`, `docs/governance/08_local_only_exclusion_policy.md`). It does **not** place trades/orders. Depth: `docs/`, `AGENTS.md`.

**Pipeline (bottom-up; data lineage is the organizing principle).** Per-trade, synchronous:
`raw_events` (raw payload, stored FIRST — `runner.py:191`) → `normalized_trades` (canonical, `runner.py:388`) → rolling `metric_windows` (`runner.py:408`) → rule evaluation (`engine.evaluate`, `runner.py:413`) → per-decision **suppression** check (`runner.py:426-436`) → `alerts` insert (`db/repos/alerts.py`) → out-of-band delivery (console/file/dashboard). **Invariant:** raw is persisted before anything derived is trusted (structurally enforced — `trade_id` exists only post-commit). Replay: `replay.replay_from_db(rules_config=…, persist=False)` re-runs the engine over historical `raw_events` with zero writes — the basis for offline validation.

**Detection rules** (`config/alert_rules.yaml`, 6 rules): `large_trade_absolute_v1` (sev medium), `market_relative_large_trade_v1` (medium), `directional_cluster_v1` (**high**; window 300 s, ≥3 trades, ≥$15k net, ≥2¢ impact), `open_interest_shock_v1` (high), `momentum_v1` (high; 900 s, ≥5 trades, ≥$75k), `volume_spike_v1` (**low**; ≥5× multiplier, ≥20 baseline trades, **≥$850 notional floor**, fp-target 30%). Each rule builds its own `AlertDecision`; there is **one** existing suppression tier keyed `(venue, market, rule, outcome)` @ 300 s event-time.

**Governance / "the gauge".** `pmfi alerts fp-rate` reads `alert_reviews` (human/rule labels: `tp`/`fp`/`noise` + `false_positive_category`) joined to `alerts`, and reports per-rule **not-actionable rate** = `(fp+noise)/reviewed` against each rule's `acceptable_fp_rate_percent` → OK / BREACH / INSUFFICIENT (`data_reports.py:build_fp_rate_governance_rows`). `volume_spike_v1` also has a **current-floor cohort** (only alerts clearing the live `$850` floor) so the gauge reflects what can actually fire today, not a stale all-time cohort.

**Operational guards (live daemon).** DiskHeadroom (blocks-intake, DEGRADED, 5 GiB/10%), UnresolvedDeadLetterHalt (blocks-intake, HALTED, async ~60 s), DeadLetterRate (DEGRADED 5%), PoolAcquireWait (DEGRADED 100 ms). **No memory or throughput guard exists** (soak proved plateau/no-leak → YAGNI).

---

## 1. What has been accomplished (verified)

### 1.0 Milestone timeline (the full arc, for context)

Concise lineage (detail in `WORKLOG.md` + the linked reports). Bottom-up: **M-DUR** durability (feature-complete, PRs #16-22) → **local-v1** baseline + **retro audit** (integrity gaps at local scale: FP-gate, dead-letter hole, circuit-breaker trickle — all since floored) → **DQ / data-plane qualification** (capture/semantics/recovery/live/restore gauntlets) → **test-isolation sweep** (Waves 1-4, #76-79; scratch/guarded/read-only/mock manifest complete) → **test-strength** (#82-83, mutation-hardened DQ + integration tests) → **soak dashboard** (#80-81) → **24 h soak** `soak2d` PASS → **M2-SOAK-APPLY** closed (ratify, zero config change) → **M-TRUTH** realized re-baseline (44 settlement-grounded labels) → **M-GAUGE-HONESTY** (#84/#85, honest gauges + recommender) → **M-REVIEW-BURNDOWN** (#86, 57-thread backlog + 16 defects) → **M-REVIEW-CLOSEOUT** (#87, thread hygiene) → **M-COFIRE-0** (co-fire semantics settled) → **M-COFIRE-GOV/R2** ✅ (#88 — the gauge now shows each BREACH's structural cross_market_hedge share). The product has moved from "build the pipeline" to "make its governance honest and its detection structurally-aware."

### 1.1 The 2026-07-02 milestone arc (all merged, all green)

| PR | Commit | Milestone | Substance | Verification |
|---|---|---|---|---|
| #84 | `7615d7c` | M-GAUGE-HONESTY (gauge) | fp-governance headline → enforceable current-floor cohort; all-time demoted to labeled secondary. Soak recommender made honest: `null` for degenerate-0.0, basis/`unknown` metadata, uncontended-p95 warning, `memory_peak_mb` serialized. | 3-lane adversarial workflow (mutation-tested: revert-a-fix → test fails). Suite 1332/94/0 at PR head. |
| #85 | `2725e89` | M-GAUGE-HONESTY (cleanup) | M3 debt cleared (`DEFAULT_BASELINE_MANIFEST` removed; the rest verified already-fixed on main), dry-run label regression test, soak-deep RESOLUTION backfill. | Suite 1330/94/0. Two sub-items confirmed already-fixed-on-main (WORKLOG claim accurate). |
| #86 | `a487ede` | M-REVIEW-BURNDOWN | 57-thread bot-review backlog re-verified vs main; **16 still-real defects fixed test-first** (supervisor DB-outage circuit accounting, engine reload rollback, single-active reacquire after pool recreate, Kalshi page ordering, baseline pruning, runner dead-letter over-counting, review-time filtering, config bool strictness, fee fail-soft, backtest parsing, DQ1 lineage proof, +5). `classification-2026-07-02.md` covers all 57 indices, 0 unaccounted. | Suite 1345/94/0 at PR head; mutation spot-checks pass; commit hygiene clean (no AI trailers). |

Merge mechanics of note (recorded so they are not rediscovered): all three PRs conflicted on `WORKLOG.md` appends → resolved by merge-from-main per branch (no force-push), suite re-run before each merge; `gh` mergeable state lags after each merge (poll again before concluding conflict); an untracked-file collision on `reports/dataplane/soak-deep-verification-spec-2026-06-22.md` was resolved by preserving the orchestrator original as `*.orchestrator-original.md` (no-deletion policy).

### 1.2 M-TRUTH realized re-baseline (the substantive product win)

- Prior "operator-irreducible labeling" doctrine **correctly narrowed**: it holds for *fire-time-evidence* labels, not for *post-alert settlement* outcomes — settlement is objective ground truth the detector never saw at fire time, so labeling from it is forward-outcome evaluation, not circular grading.
- Built `reports/alert-quality/fetch_outcomes.py` (read-only, opt-in-live Kalshi/Polymarket settlement + candles) → `LABELING_RULE v1.1`. Two defects self-caught before ratification (hedge-rule mislabeling settlement-winning legs; orphaned-else leaving 4 rows unlabeled).
- Operator-ratified; **44 labels recorded** (`reviewed_by='ben/rule-v1.1'`): 16 tp / 13 fp / 15 noise; categories `cross_market_hedge`=12, `low_price_lottery`=1. DB fingerprint unchanged (`alerts`=318). Queue drained to the 19 synthetic fixtures.

### 1.3 Verified sub-state (prior open items now closed)

- **Fire-time floor enforcement is real and regression-tested** (closes the 2026-06-20 recalibration-spec finding #3): `VolumeSpikeRule.evaluate` gate at `src/pmfi/pipeline/rules.py:540-549` ANDs `_this_cap >= self._min_trade_usd` (+ multiplier + baseline). `tests/test_pipeline_engine.py:456-480` fires $849 (suppressed) / $850 (emits) against the *live* `config/alert_rules.yaml`; a second config-parameterized variant at `:412-453`. Verified passing.
- **`volume_spike_v1` is already `severity: low`** (`config/alert_rules.yaml`), `min_trade_usd: 850`, `acceptable_fp_rate_percent: 30`. The "demote volume_spike" option is therefore *already done* — its BREACH is on a low-severity rule that does not dominate the medium/high queue.
- Soak: 24h `soak2d` run PASS; M2-SOAK-APPLY closed (ratify evidence, **zero** live daemon-config change — audited guard set is DiskHeadroom / UnresolvedDeadLetterHalt / DeadLetterRate / PoolAcquireWait; no memory or throughput guard exists, so soak "thresholds" are harness verdict criteria only).

---

## 2. Current true state (repo evidence, 2026-07-05)

- `origin/main = f98c222` (= `a487ede` + #87 closeout), local main equal, **0 open PRs** at last check. Suite green (1349/94/0). Primary DB fingerprint stable (`alerts=318`).
- **Two genuine fp-governance BREACHes** (real, not stale-cohort artifacts):
  - `volume_spike_v1` current-floor: **31.5%** not-actionable (89 reviewed: 61 tp / 4 fp / 24 noise; target ≤30%; 49 below-floor excluded). Severity **low**.
  - `directional_cluster_v1`: **19.7%** (61 reviewed: 49 tp / 6 fp / 6 noise; target ≤15%). Severity **high**.
- **Shared root cause (evidence).** The fps are `cross_market_hedge` (12 of 13). A single Kalshi event fires many legs at once — e.g. `KXWCGAME-26JUN20GERCIV` produced co-firing alerts across **GER / CIV / TIE / corners / totals / 1H-total** legs *and across four different rules* (directional_cluster, momentum, market_relative, volume_spike). The detector has no fire-time signal that these legs are one hedged basket rather than N independent informed trades. **No parametric threshold on any single rule can remove a cross-leg pattern.** Both BREACHes are the same structural problem seen through two rules.

### 2.1 Grounded facts that constrain any fix (from code recon)

1. **Emission is per-trade, synchronous, single-connection with ordered persistence** (`src/pmfi/pipeline/runner.py` `process_event`) — **not** one atomic transaction (agent2 review, confirmed): `process_event` acquires one connection, but `insert_raw_event` and `insert_trade` each wrap their **own** transaction (`raw_events.py:47-51`, `trades.py:26-28`). Order is raw first (`insert_raw_event`, `runner.py:191`), canonical trade next (`insert_trade`, `runner.py:388`), rules after (`engine.evaluate`, `runner.py:413`); raw-payload-first lineage is structurally enforced (`trade_id` exists only post-commit). Any co-fire logic must key off already-lineage-traced records.
2. **One suppression layer already exists** and is the natural extension point: in-memory + DB-backed, keyed `(venue_code, market_id, rule_id, outcome_key)`, event-time window `suppression.default_window_seconds: 300` (`runner.py:426-436`, `alerts.py:966-993`, `config/alert_rules.yaml`). It is single-leg / single-rule. Cross-leg co-fire suppression is a *new tier* on this mechanism, and must not conflict with it.
3. **No event key is stored.** `markets.venue_event_id` exists in DDL (`sql/001_init.sql`) but is **never written or read** (always NULL). Kalshi event ticker is derivable string-wise (`venue_market_id.rsplit("-",1)[0]` strips the leg suffix); **Polymarket has no multi-leg event convention** in this codebase (`0x…` condition ids). ⇒ **co-fire grouping is effectively Kalshi-scoped**, which is sufficient: 12/12 observed `cross_market_hedge` fps are Kalshi legs; the lone Polymarket fp is a different category (`low_price_lottery`).
4. **An offline zero-tp-loss validation harness already exists — but only for `volume_spike_v1`.** `replay.replay_from_db(pool, rules_config=<candidate>, persist=False)` (`src/pmfi/replay.py:109-342`) re-runs the engine over historical `raw_events` with a candidate rule set, zero DB writes; `run_volume_spike_calibration_replay` (`src/pmfi/volume_spike_calibration.py:78-139`) diffs current-vs-candidate alert sets and joins `alert_reviews`; `removed_review_labels["tp"]` is precisely the "tp-loss" signal. It is hard-scoped to `volume_spike_v1` (`calibration.py:14` `VOLUME_SPIKE_RULE`, numeric-knob-only candidate builder). **Co-fire suppression is a behavioral engine change, not a YAML knob**, so `rules_config` diffing cannot express it — validating it offline requires (a) a feature-flagged engine variant and (b) generalizing the volume_spike-only comparator to any rule / engine variant. This is a scoped extension of proven code, not a new harness.
5. `reports/alert-quality/fetch_outcomes.py` is a **one-shot, opt-in-live** labeling generator (hardcoded input/output constants, `assert len(real)==44`, records nothing to DB). Turning it into a repeatable governance input is net-new (small) work.

---

## 3. Loose ends (evidence-backed, ranked by materiality)

| ID | Issue | Evidence | Impact | Disposition |
|---|---|---|---|---|
| **L1** | **Post-merge review-thread follow-up was delegated but never delivered.** 15 `still_open` GitHub threads (now fixed at `a487ede`) remain open with "fixed in #86" comments; 8 `handoff_to_gauge_lane` threads unadjudicated vs merged #84/#85; no classification addendum; no `codex/review-burndown-close` branch. | Agent1's own log: its 2nd invocation found `for-codex.md` overwritten by agent2's completion receipt → read a *completion notice*, did read-only reconciliation, stood by ("No tests were run because no code change was assigned"). Verified at investigation: branch was absent, 0 open PRs, `for-claude.md` shows `still_open:15 / handoff:8` left open. Root cause = single shared inbox file (last-writer-wins) + watcher `b39npavik` dying at session teardown. MEMORY.md recorded this as "delivered" — **incorrect**. | Low functional (all code landed); it is dropped hygiene silently logged as done, and a correctness stain on the record. | **RESOLVED 2026-07-05** — agent1 closeout PR #87 merged (`f98c222`): 15 `still_open` threads resolved (idx 7 was a distinct already-resolved DQ1 thread = the 16th, reconciling the 16-vs-15 count), 8 gauge-handoff threads honestly left OPEN (#84/#85 never touched the implicated `daemon.py`/`_shared.py`/coverage code), `classification-addendum-2026-07-05.md` added. Independently verified: fence PASS (docs-only), suite unchanged 1349/94. MEMORY.md corrected. |
| **L2** | `.codex/config.toml` `sandbox_mode = danger-full-access` — uncommitted, unlogged, live since 07-02; contradicts AGENTS.md local-only/scope posture. | `git diff .codex/config.toml` (tracked). Operator "confirmed intentional" in-session; still neither committed-with-rationale nor reverted. | Security-relevant; a permanently dirty tree that re-surfaces every `git status` and could silently widen a future Codex lane's blast radius. | **Operator decision** (§6.4). Do not unilaterally commit a sandbox-widening or revert against stated intent. |
| **L3/L4** | **67 worktrees under `worktrees/`** (68 rows in `git worktree list` incl. the root checkout); ~60 merged/done. `.venv` editable `pmfi` resolves to `worktrees/dq5` (stale code) → bare `python -m pmfi.cli` runs old code, misdirects outputs. | `git worktree list`; MEMORY.md dq5 gotcha; recon confirmed `rules_config` path etc. only correct from root via `PYTHONPATH=src`. Disk 806 GB free / 58% — not pressure. | Cleanliness + a live foot-gun (wrong-code execution). | **Gated** (§6.5): re-point venv first, then prune (respecting [No Deletions] — operator-authorized). |
| **L5** | Minor honesty nits flagged by verifiers, unfixed (non-blocking): #84 WORKLOG cites pre-labeling fp numbers (78/28.2%); #85 dry-run test asserts negative `[dry:poly]` not the literal gate string; #86 "fence-clean" bullet true only vs the narrower 8-file list; `soak_stability` reason-string `insufficient_signal` vs `degenerate_zero_measurement`, untested. | 3-lane verification workflow findings. | Documentation/precision only. | Fold into agent1's addendum PR (L1). |
| ~~L6~~ | ~~Unconfirmed fire-time floor enforcement~~ | **RESOLVED** — see §1.3. Enforcement + regression test exist and pass. | — | Closed. |

---

## 4. Forward roadmap — ranked, with justification, acceptance criteria, and implications

**Governing principle (repo precedent):** *measurement before enforcement.* The soak/M2 decision was explicitly "bigger baseline before enforcing"; the same discipline applies here. We do **not** change alert-emission semantics on a 61-alert cohort until (a) the measurement is co-fire-aware, (b) an offline harness can prove a candidate change is zero-tp-loss, and (c) the operator greenlights the fenced emission change. This sequencing is why the roadmap leads with measurement, not suppression.

> **RECONCILIATION with agent2 adversarial review (2026-07-05, `for-claude-agent2-2026-07-05.md`).** agent2 confirmed the diagnosis + the A>B>C ranking + "measurement before enforcement", and **does not agree R3 is ready for implementation** from the current spec. Three material risks the first draft under-weighted, each with observed evidence — these REVISE the sequence below (a read-only semantics classifier now leads, ahead of harness plumbing):
> 1. **Event-key granularity is unsettled, not solved.** `venue_market_id.rsplit("-",1)[0]` yields the Kalshi *event_ticker* but **under-groups the match basket**: `KXWCGAME-26JUN20GERCIV`, `KXWC1HTOTAL-26JUN20GERCIV`, `KXWCCORNERS-26JUN20GERCIV` are one match's hedge exposure yet split to three keys. The labeling script itself admits this (`fetch_outcomes.py:39-40`). ⇒ the basket key may need to be a **match key** (the shared `-<DATE><TEAMS>` core), not the event_ticker. Must be decided + tested, not assumed.
> 2. **Candidate B is NOT zero-tp-loss "by construction".** Mixed-label groups exist (GERCIV basket has fp `97ee5ccb` **and** tp `92c182df`/`d1e48c62`). One parent labeled "fp" makes a real tp **invisible to governance** even if no row is dropped. Zero-tp-loss must therefore cover **visibility**, not just non-deletion — new counters required (see spec).
> 3. **300s emission window ≠ 15-min labeling window — observed.** Hedge pair `39bd1f35`↔`623164c5` fired **726 s apart** (inside 15 min, outside 300 s). A 300 s co-fire window would miss it; either prove `missed_labeled_hedge_fp==0` at the chosen window or widen to ~900 s (with over-group guards).
>
> **REVISED sequence: R0 (read-only classifier, settle semantics) → R1 (harness, built around settled semantics) → R2 (governance annotation) → R3 (emission, gated).** Full hardened acceptance criteria live in `plans/2026-07-05-cofire-suppression-spec.md` (updated post-review).

### Tier 1 — product truth (the substantive frontier)

#### R0 — Co-fire semantics classifier — ✅ DONE 2026-07-06 (`reports/alert-quality/co-fire-semantics-2026-07-06.md`)
> **Result (cross-verified, 2 independent passes):** window **900 s** (300 s misses 7/33 labeled hedge pairs); key **event_ticker** = clear choice (== Kalshi `facts.event_ticker` 43/43, zero under/over-grouping); **`match_key` REJECTED** (over-groups cross-market-type markets the labels exclude by design; the "match basket" is an unvalidated hypothesis needing a label-rule extension, not a key swap); clustering **pairwise-radius** (not consecutive-chain); **leg-aware visibility mandatory** (one event holds both tp+fp legs, timing-separated by coincidence). Polymarket no-op. R1 can now build around these knowns.

- **What.** A read-only analysis over the 44-label artifact (`reports/alert-quality/outcomes-2026-07-02.json`) + `raw_events` that *settles the unknowns before any code that changes behavior*: (a) event_ticker vs match-key derivation, tested against the real ticker grammars agent2 listed (`KXWCGAME-…`, `KXWC1HTOTAL-…`, `KXWCCORNERS-…`, `KXBTC15M-…`, `KXBTCD-…`, `KXT20MATCH-…`, `KXMVESPORTS…`); (b) grouping under 300 s vs 900 s — report `missed_labeled_hedge_fp` + over-group count each; (c) mixed-label group inventory; (d) singleton-tp negative controls (`36cdc737` must not group); (e) Polymarket no-op count.
- **Why first (agent2 challenge, accepted).** The highest-risk unknowns are key granularity + window semantics, **not** generic comparator plumbing. Settling them read-only (no engine change, no DB write) de-risks R1–R3 and is the cheapest risk reducer. Produces a decision artifact (`reports/alert-quality/co-fire-semantics-2026-07-XX.md`).
- **Acceptance.** Key-derivation table test passes for every listed ticker; window table reported for both 300/900 s; no live API; primary DB read-only (fingerprint unchanged).

#### R1 = M-COFIRE-VALIDATE — Offline viability validation — ✅ DONE 2026-07-06 (PR #89 `d69a4f7`) — **GATE PASS → R3 VIABLE**
> **Result (independently verified — re-ran tests + harness, read the union-find primitive + the gate-computation logic):** Candidate B (event_ticker, 900 s pairwise-radius, co-present) on the 44-label cohort → **`removed_tp=0`, `tp_leg_visible=16/16`, `missed_labeled_hedge_fp=0`** (the `39bd1f35`↔`623164c5` pair groups), **queue 44→22, fp operator items 13→5, cross_market_hedge fp groups 12→4**; `parent_label_conflicts=4` (diagnostic — proves leg-visibility is mandatory, which B provides). **Live emission byte-identical** (`runner.py`/`engine.py` diff empty; `cofire` imported nowhere in `src/`). ⇒ **R3 is viable**; the only remaining step is the fenced live wiring, gated on operator greenlight + agent2 adversarial review.

- **What (lean, decisive; historical wording later corrected).** (a) Build a **standalone co-fire primitive** `src/pmfi/pipeline/cofire.py` — pure functions: `derive_event_ticker(venue_market_id)` (Kalshi `rsplit("-",1)[0]`, == `facts.event_ticker`, guarded to `None` for non-3-segment/non-Kalshi) and `group_cofire(alerts, window_s=900)` (**pairwise-radius**, event_ticker-scoped, Kalshi-only, **Candidate B: co-present, drop nothing**, every leg's identity/label retained). **Not wired into any live path.** (b) A validation harness applies `group_cofire` to the 44-label cohort (`outcomes-2026-07-02.json`). Later audit corrected this section: `parent_label_conflicts` is diagnostic evidence requiring leg-aware review, not a hard pass/fail gate. (c) Report `reports/alert-quality/co-fire-validation-2026-07-XX.md` = the **go/no-go evidence for R3**.
- **Why this framing (safe "proceed").** Building the primitive + validating it offline **changes zero live behavior** (the function is called only by the harness/tests) — an AGENTS.md-sanctioned bounded spike that removes the R3-viability uncertainty. It decides whether the irreversible R3 (live wiring) is even worth doing, *before* touching live emission.
- **Acceptance criteria (pass/fail).**
  - `cofire.py` unit tests green: derivation table (R0's 9 tickers + non-3-segment guard), radius grouping, singleton-tp control (`36cdc737` ungrouped), Polymarket no-op.
  - Validation on the 44 cohort: current interpretation is `removed_tp`/`tp_visible` are structural leg-retention checks, `missed_labeled_hedge_fp` plus the hardcoded GERCIV anchor pair are the window-sensitive grouping checks, and `parent_label_conflicts` is diagnostic.
  - **Live emission provably unchanged:** `runner.py`/`engine.evaluate` byte-identical; a test/assertion that no live path imports/calls `group_cofire`.
  - Offline only (no live API); primary DB read-only (fingerprint unchanged).
- **Risks/implications.** Low (no live change; reversible — the module is deletable). If the gate FAILS (a tp is hidden or hedges missed), **R3 is not viable as specified** → surface, do not wire live. If it PASSES → R3 (live wiring in `runner.py`) returns for a **final operator go** with this evidence + agent2 adversarial review.

#### R2 — Category-attributed fp-governance — ✅ DONE 2026-07-06 (PR #88 `27381f7`)
- **Delivered:** `alerts fp-rate` now carries latest-review `false_positive_category` for fp/noise labels and renders a per-rule `not_actionable_by_category` breakdown (rich + plain), current-floor cohort attributed to the same floor as the headline. Live: `directional_cluster_v1 cross_market_hedge=5`, current-floor `volume_spike_v1 cross_market_hedge=4` — the BREACHes are now shown as structurally hedge-driven, on the gauge itself.
- **Verified (orchestrator, independent):** fence PASS (5 owned files); design **additive/backward-compatible** — `build_fp_rate_governance_rows` gained an *optional* `category_totals` kwarg, existing reviewed/fp/noise/rate/target/status fields byte-identical (dedicated invariant test `..._keep_existing_shape_without_category_totals`); suite **1353/94/0** (+4 tests); independently re-ran `test_data_reports.py`+`test_alerts_review.py` = **97 passed**; primary DB fingerprint unchanged (`alerts=318`, read-only); no emission/event-ticker/schema change.
- **Re-sequencing rationale:** R2 had **no** R1 dependency and was ungated read-side governance (like M-GAUGE-HONESTY), so it went next for immediate honest-gauge value at low risk. Sequence was R0 ✅ → **R2 ✅** → (R1 + R3 gated on greenlight).
- **What (lean).** Extend fp-governance to break each rule's not-actionable count down by `alert_reviews.false_positive_category` (existing labels: `cross_market_hedge`=12, `low_price_lottery`=1). So a BREACH line shows *how much* of the not-actionable rate is structural hedge co-fires vs standalone fp. **Uses existing labels — no `event_ticker` derivation** (that fragile primitive is deferred to R3 where suppression needs it).
- **Anchors.** `cmd_alerts_fp_rate` (`src/pmfi/commands/alerts.py:2707`) query must also select `false_positive_category`; `build_fp_rate_governance_rows` (`src/pmfi/data_reports.py:202-253`) gains a per-rule `not_actionable_by_category` map; renderer shows it. Tests in `tests/test_data_reports.py` + `tests/test_alerts_review.py`.
- **Acceptance criteria.** directional_cluster_v1 + volume_spike_v1 BREACH lines show the `cross_market_hedge` share of not-actionable; the 12 labeled hedge fps attributed; **zero change to what alerts fire**; governance rate/status math unchanged for existing rows; offline, DB read-only, fingerprint unchanged; red→green tests.
- **Implication.** Governance-side only → safe, reversible; does **not** reduce operator queue volume (all legs still fire) — that is R3's job, gated. R2 is the honest interim truth layer.

#### R3 (gated) — Co-fire grouping/suppression at emission — **fenced, operator-greenlight required**
- **What.** The actual detection-time fix. Design space is deliberately *not pre-decided* — see `plans/2026-07-05-cofire-suppression-spec.md`, which frames a Talmudic debate among three candidates: **annotate-only** (least invasive), **group/rollup** (N legs → 1 parent alert, zero signal loss, schema change), **suppress-keep-one** (extends existing suppression, simplest, highest tp-loss risk).
- **Why gated, not now.** It is **alert-emission semantics** — a category this repo already fences as requiring separate authorization (verified: agent1's handoff explicitly lists "alert emission semantics" as a NO-touch category). AGENTS.md planning threshold (architecture/scope) → dedicated plan + inline Talmudic consensus (ADR if it changes schema). And it *cannot* be safely validated until R1 exists.
- **Hard acceptance criterion (non-negotiable): zero tp-loss.** Some co-fired legs are genuine tp (e.g. `92c182df` GERCIV-GER settled yes = tp). The offline replay (R1) against the 44 labels must show `removed_review_labels["tp"] == 0` for whatever design is chosen; a design that drops a labeled tp fails, full stop. This is why "group/rollup" (co-present, drop nothing) is a priori safer than "suppress" (drop legs) and is the recommended lead candidate — but the choice is deferred to the debated, offline-validated consensus.
- **Implications.** Medium-to-high invasiveness depending on candidate; must preserve the existing 300s single-leg suppression; must preserve per-leg raw lineage; Kalshi-scoped (Polymarket unaffected). Delivered only after operator greenlight, as a Codex lane with mandatory adversarial review + orchestrator merge gate.

### Tier 2 — bounded hygiene (cheap, unblocks cleanliness)

- **H1 — DONE (2026-07-05).** L1 closed via agent1 PR #87 (`f98c222`): 15 threads resolved, 8 gauge threads adjudicated + left open, `classification-addendum-2026-07-05.md`, L5 nits folded in.
- **H2 — DECIDED (operator, 2026-07-05): LEAVE `.codex/config.toml` `danger-full-access` AS-IS. Do not change.** No commit, no revert. Worktree isolation + explicit per-lane fences remain the operative blast-radius controls (§6.4).
- **H3 — DECIDED (operator, 2026-07-05): DO NOT clean up worktrees/venv now.** The 67 worktrees + the `.venv`→`dq5` coupling stay as-is; re-point/prune is deferred until the operator authorizes (§6.5). Constraint reminder: bare `pmfi` from root still runs stale dq5 code — use `PYTHONPATH=src`.

### Recommended sequence
**R0 ✅ → R2 ✅ → (R1 + R3, gated on operator greenlight)** — revised 2026-07-06. R0 (semantics) settled key/window; **R2 (category-attributed governance) ✅ merged (PR #88 `27381f7`)** delivered the honest hedge-attribution on the gauge; **R1 (replay harness) + R3 (emission) are the gated pair** — R1 only earns its keep once R3 is greenlit (validating an emission change not yet authorized). R3 without R1 would be an unvalidated emission change — the anti-pattern FAST_ADVANCE.md forbids. H1 (review-thread closeout) DONE (PR #87 `f98c222`).

---

## 5. Orchestration & delegation model

**Roles (fixed for this program):**
- **Claude (this session) = orchestrator/verifier.** Authors and owns plans (this file, the co-fire spec), verifies/validates every delegated output against repo evidence, makes all merge decisions. Does **not** hand-implement fenced code; does not self-approve in the same active context.
- **Codex `019f2203` (agent1) = execution + hygiene lane.** Primary executor for approved plans. Currently: L1/H1 closeout.
- **Codex `019f2204` (agent2) = adversarial review + (later) second execution lane.** Currently: adversarial review of the co-fire spec (read-only critique, no code).
- **Workflows (Sonnet/Haiku only) = recon + verification fan-out.** Never the sole approver of anything shipped.

**Isolation & anti-collision (lessons applied from L1):**
- **Per-agent inbox files**, never a single shared `for-codex.md` for two live agents (the L1 root cause). Each handoff is its own file; each IPC message points to its own file.
- Each Codex lane works in its **own worktree off `origin/main`**, `worktrees/<short>`, disjoint file ownership, PRs left **open** (no self-merge, no force-push, no AI attribution).
- **File-ownership fences** stated per handoff. R3 (emission) and the gauge-lane files remain mutually exclusive from review/hygiene lanes.
- **Self-verification is mandatory** for every delegated task: the delegate runs its own red→green + `scripts\verify.py` + DB fingerprint; the orchestrator then *independently* re-verifies (mutation spot-check / fence diff / suite re-run) before merge. Delegated ≠ trusted.

**Verification protocol before any merge (from the 07-02 pattern that worked):**
1. Fence diff (changed files ⊆ declared ownership).
2. Offline suite green at PR head (record counts).
3. Mutation spot-check (revert one fix → its test fails).
4. Commit hygiene (author, no AI trailers).
5. Primary DB fingerprint unchanged (read-only lanes).
6. `gh` mergeable state polled twice (it lags).

---

## 6. Operational, hardware & safety guardrails

The user flagged driver/storage/memory/disk/security/integrity and "too many python processes." Concrete rules:

### 6.1 Concurrency / "too many python processes" (single-box, shared Postgres)
- **One DB-gated pytest run at a time.** All lanes share one Docker Postgres; concurrent `scripts\verify.py` (each spawns many python subprocesses) + soak DB + Codex + workflow pytest = CPU thrash and DB connection-pool contention. **Serialize suite runs**; use `run_in_background` and *wait* rather than fanning out N simultaneous suites.
- **≤2 Codex threads** (the two named), each single-worktree. Do not spawn a third.
- **Workflows capped** (the tool caps concurrent agents ~10-16); use them for recon/verify, not for parallel heavy pytest.
- **Primary DB is read-only** for all verification/recon; write tests use scratch/ephemeral DBs. Fingerprint before/after any DB-touching lane.
- Never run bare `python` (Windows Store alias); use the explicit interpreter or `.venv\Scripts\python.exe`; from root use `PYTHONPATH=src` until the venv is re-pointed (L4).

### 6.2 Storage / disk
- C: ~800+ GB free (~57-58% used, drifts) — no pressure. The debt is **67 worktrees** (L3), gated cleanup (§6.5). PMFI-related Docker containers are all stale exited `land-dd-*` (2 weeks old, ~16 kB each) — harmless; unrelated other-project containers also exist on the box (not PMFI's concern). Operator may prune.

### 6.3 Integrity / data lineage (non-negotiable)
- Raw-payload-first is structurally enforced (§2.1.1) and must stay so. No co-fire logic may bypass raw storage or key off derived-only records.
- No live API calls in default tests/verify. No weakening of tests/type-checks/gates to pass. No secrets/`.env`/DB dumps committed.

### 6.4 Security — the sandbox config (L2)
- `.codex/config.toml` grants `danger-full-access` to Codex lanes. **DECIDED (operator, 2026-07-05): leave as-is — do not commit, do not revert.** Rationale that makes this acceptable: the operative blast-radius controls are **worktree isolation + explicit per-lane file-ownership fences** (each Codex lane branches its own `worktrees/<short>` off origin, disjoint ownership, PRs left open for orchestrator verification), not the sandbox mode. The mode stays a persistent uncommitted local diff by design; orchestration must not rely on the sandbox for containment. Do not change this file.

### 6.5 dq5 / venv coupling (L4) — no cleanup now (operator)
- **DECIDED (operator, 2026-07-05): do NOT clean up worktrees/venv now.** The 67 worktrees and the `.venv`→`worktrees/dq5` editable coupling stay. Operative constraint until further notice: **run pmfi from root with `PYTHONPATH=src`** (or from the correct worktree) — bare `python -m pmfi.cli` silently runs stale dq5 code and misdirects outputs.
- *If/when* the operator later authorizes cleanup, the safe order is: (1) `pip install -e .` from root, (2) verify `python -c "import pmfi; print(pmfi.__file__)"` resolves to root `src\pmfi`, (3) *then* prune merged worktrees. Pruning first breaks every pmfi invocation. **Not authorized now.**

---

## 7. Acceptance criteria & pass/fail — what belongs in scope (and what does not)

**In scope for any Tier-1 change:** red-first failing test per changed behavior; `scripts\verify.py` green (or failure + narrow next fix recorded); `db_local.py verify` if DB-touching; offline zero-tp-loss proof against the labeled cohort for R3; plan/WORKLOG/docs updated; no unrelated churn; handoff lists changed files + checks + residual risk + any Talmudic consensus.

**Explicitly out of scope (do not add):** live-API dependence in default tests; threshold retuning of `min_baseline_trades` (proven to kill all tp — recalibration spec finding #1); any SaaS/hosted/notification-external work (local-only non-negotiable); trading/order-placement; a second durable store; changing `volume_spike` severity (already `low`).

**Redundancy/safeguards:** every delegated lane is independently re-verified by the orchestrator (§5); R3 carries a hard leg-aware zero-tp-loss gate + adversarial review + Talmudic consensus before merge; measurement (R0 done, R2 executing) precedes any enforcement (R3).

---

## 8. Open decisions requiring the operator

**Still open (the genuine calls):**
1. **R3 emission greenlight** — the one substantive open decision: authorize building co-fire grouping/suppression (R1 harness → R3 emission) after R2 lands? R3 is fenced alert-emission semantics; agent2 does not endorse it from the current spec, and the hard leg-aware zero-tp-loss gate must pass offline first. *Recommendation: hold until R2 delivers the honest attribution and you've seen whether the governance layer alone suffices — R3 (reducing queue volume) is optional, not required for honesty.*
2. **Cross-market-type "match basket"** — pursue grouping across market types (`KXWCGAME`+`KXWC1HTOTAL`+…) or stay with `event_ticker`? R0 showed this needs a **labeling-rule extension + new labels** (the current labels are event_ticker-scoped by design), not a key swap. *Recommendation: defer; `event_ticker` covers every currently-labeled hedge with zero under/over-grouping.*

**Decided (2026-07-05, do not revisit):**
3. **L2 sandbox config** — LEAVE `.codex/config.toml` `danger-full-access` as-is (worktree isolation + fences are the containment, not the sandbox mode). §6.4.
4. **H3 worktree/venv cleanup** — DO NOT clean up now (dq5/venv coupling stays; `PYTHONPATH=src` from root). §6.5.
5. **Roadmap sequence** — R0 ✅ → R2 (ungated, executing) → R1+R3 (gated). Re-sequenced 2026-07-06 (R2 has no R1 dependency). §4.

---

## 9. Artifacts

- **This file** — plan-of-record / master context.
- `plans/2026-07-05-cofire-suppression-spec.md` — R3 design spec (Talmudic A/B/C debate + hardened acceptance; M-COFIRE-0 settled).
- `reports/alert-quality/co-fire-semantics-2026-07-06.md` — **R0 measurement** (window/key/clustering settled, cross-verified).
- Handoffs: `HANDOFF-agent1-review-thread-closeout-2026-07-05.md` (H1, done), `HANDOFF-agent2-cofire-plan-review-2026-07-05.md` (R3 review, done), `HANDOFF-agent1-cofire-governance-2026-07-06.md` (R2, executing). Replies: `for-claude-agent{1,2}-2026-07-05.md`, `for-claude-agent1-2026-07-06.md`.
- Evidence base: `reports/alert-quality/m-truth-status-2026-06-25.md`, `…/m-truth-autolabel-proposal-2026-07-02.md`, `…/volume-spike-recalibration-spec.md`, `reports/review-threads/classification-2026-07-02.md` + `classification-addendum-2026-07-05.md`.
- Cross-session memory: `~/.claude/…/memory/MEMORY.md` (⭐⭐⭐ line) + `pmfi_orchestration_history.md`.

---

## 10. Decision ledger (material decisions → why-this-over-alternatives)

Consolidated so the *reasoning* behind each choice is in one place. Format: **Decision — Alternatives considered — Why this is most adequate — Status.**

1. **Label alert outcomes from settlement (M-TRUTH), narrowing the "operator-irreducible" doctrine.** *Alt:* keep all labeling manual/operator-only. *Why:* settlement is objective ground truth the detector never saw at fire time → labeling from it is forward-outcome evaluation, not circular grading; only *fire-time-feature* labeling stays operator-irreducible. Broke the volume_spike tp/noise feature-overlap ceiling. *Status:* done, ratified, 44 recorded.
2. **Headline fp-governance on the current-floor cohort (M-GAUGE-HONESTY).** *Alt:* keep the all-time cohort as headline. *Why:* the all-time cohort includes alerts that can no longer fire under the enforced $850 floor → a false BREACH; current-floor reflects reality. *Status:* merged #84.
3. **Re-verify the 57-thread review backlog rather than blind-fix (M-REVIEW-BURNDOWN).** *Alt:* fix every flagged thread. *Why:* backlog was 11 days / 22 PRs stale; spot-checks showed the "scariest" items already fixed on main → blind-fixing wastes effort + risks regressions. *Status:* merged #86 (16 real defects), #87 (thread hygiene).
4. **Measurement before enforcement for co-fire.** *Alt:* build suppression directly on the BREACH signal. *Why:* repo precedent ("bigger baseline before enforcing"); FAST_ADVANCE forbids "scoring that cannot be replayed"; the BREACH cohort is small (n=61). *Status:* R0 done, R2 executing, R3 gated.
5. **R0 read-only semantics classifier leads the co-fire program (agent2 challenge, accepted).** *Alt:* generalize the harness (R1) first. *Why:* the real unknowns were key-granularity + window, not comparator plumbing; settle them read-only before any code. *Status:* done, cross-verified.
6. **Co-fire key = `event_ticker`, reject `match_key` (R0, cross-verified).** *Alt:* `match_key=segments[1]` (agent2's "match basket"). *Why:* `event_ticker` == Kalshi `facts.event_ticker` 43/43 and unifies every labeled hedge with zero under/over-grouping; `match_key` over-groups across market types the labeling rule *excludes by design*, adds no benefit, and has a fragile derivation. The match-basket is an unvalidated hypothesis needing a label-rule extension. *Status:* settled.
7. **Co-fire window = 900 s in a separate namespace (R0 + agent2).** *Alt:* reuse the existing 300 s single-leg suppression window. *Why:* 300 s misses 7/33 labeled hedge pairs (observed: `39bd1f35`↔`623164c5` = 726 s); 900 s misses 0; a separate namespace prevents poisoning single-leg suppression. *Status:* settled.
8. **R2 before R1; R2 scoped to category-attribution using existing labels.** *Alt:* R1 first / R2 via `event_ticker` grouping. *Why:* R2 has no R1 dependency and is ungated (read-side governance); using the existing `false_positive_category` labels avoids the fragile `event_ticker` derivation (deferred to R3). Delivers honesty now at low risk. *Status:* delegated, executing.
9. **Candidate B (group/rollup) over C (suppress-keep-one) for R3 — but not "zero-tp-loss by construction".** *Alt:* C (drop redundant legs). *Why:* C is lossy (score ≠ settlement winner); B drops nothing. But R0 showed mixed-label groups exist → B still needs **leg-aware visibility** (a tp under an fp parent is hidden). *Status:* design, gated.
10. **Per-agent inbox files; delegate via /ipc with probe-first.** *Alt:* single shared `for-codex.md`/`for-claude.md`. *Why:* the shared inbox's last-writer-wins race silently dropped a delegated task (L1 root cause); per-agent files eliminate it. *Status:* adopted.
11. **Leave `.codex/config.toml danger-full-access`; no worktree cleanup (operator).** *Why:* containment is worktree isolation + fences, not sandbox mode; cleanup has a dq5/venv-coupling foot-gun and no urgency (806 GB free). *Status:* operator-decided.

---

## 11. Glossary

- **alert / leg** — one emitted anomaly on one market/outcome. A multi-leg event fires many legs.
- **co-fire / cross_market_hedge** — many legs of one real-world event firing together; often a structural hedge, not N independent signals. The dominant fp class (12/13 labeled fps).
- **event_ticker** — Kalshi's event id = `venue_market_id` minus the trailing outcome segment (`rsplit("-",1)[0]`); == the venue's own `facts.event_ticker`.
- **match_key** — a broader (rejected) key merging market *types* of one match (`KXWCGAME`+`KXWC1HTOTAL`+…); over-groups vs the labeling definition.
- **current-floor cohort** — governance cohort of only alerts clearing the live `$850` floor (what can fire today).
- **not-actionable rate** — `(fp+noise)/reviewed`; the gauge metric vs each rule's `acceptable_fp_rate_percent`. **BREACH** if over target with enough reviews.
- **tp / fp / noise** — true positive / false positive (wrong-or-misleading; carries a `false_positive_category`) / valid-but-not-worth-acting-on.
- **suppression** — existing single-leg cooldown keyed `(venue, market, rule, outcome)` @ 300 s. Distinct from the proposed co-fire tier.
- **dead-letter** — a payload that failed normalization/processing, preserved for recovery (durability invariant).
- **soak** — a multi-hour endurance run (`soak2d` = 24 h PASS) checking rss/db-growth/recovery.
- **DQ** — data-quality qualification gauntlets (capture/semantics/recovery/live/restore).
- **fence** — the explicit allowed/forbidden file list a delegated lane must stay within.
- **R0/R1/R2/R3** — co-fire program stages: semantics classifier / harness generalization / category-attributed governance / emission grouping (gated).

---

## 12. Risk register

| Risk | Likelihood | Impact | Mitigation / status |
|---|---|---|---|
| **R3 over-suppresses a real tp** (hidden under an fp parent) | med (if R3 built) | high (silent signal loss) | Current interpretation: grouped read-side view must preserve per-leg visibility; any future emit-side suppression needs a non-tautological leg-level gate, not `parent_label_conflicts==0`. |
| **Co-fire constants overfit n=44** | med | med | R0 flags it explicitly; re-validate window/key on a larger post-07-02 cohort before finalizing; defects each driven by one event (GERCIV). |
| **`event_ticker`/derivation breaks on unseen Kalshi grammar** | low-med | med | Guard non-3-segment tickers; lock with a derivation-table test; no bare `split` in production. |
| **Delegated Codex lane drifts scope** | low | med | Per-lane fence + orchestrator fence-diff before merge; mutation spot-check; PRs left open (no self-merge). |
| **Silent delegation drop** (inbox race / watcher death) | low (fixed) | med | Per-agent inbox files; do not rely on fire-and-forget watchers; verify reply-file existence before claiming done. |
| **Hardware: too many concurrent python/pytest vs one Postgres** | med | med | Serialize DB-gated suites; ≤2 Codex threads; workflows Sonnet/Haiku recon only; primary DB read-only for verification. §6. |
| **venv→dq5 stale-code execution** | med | med-high (wrong results) | `PYTHONPATH=src` from root until re-pointed; cleanup gated (operator declined for now). |
| **Sandbox `danger-full-access` blast radius** | low | med | Contained by worktree isolation + fences (operator accepts as-is). |
| **DB integrity during read-heavy verification** | low | high | Fingerprint (`alerts=318` etc.) before/after every DB-touching lane; primary DB read-only; scratch DBs for write tests. |
| **Disk/storage** | low | low | 806 GB free; 67 worktrees are cleanup debt not pressure; stale Docker containers harmless. |
