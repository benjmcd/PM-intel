# PMFI — Master State-of-Record & Forward Roadmap (2026-07-06)

> **Purpose.** The single canonical, evidence-grounded, self-contained record of **what PMFI is**, **what has been accomplished and verified**, the **current true state**, the **honest open/known-limits**, **what we intend next and why each choice is the most adequate given the constraints**, plus the **orchestration/delegation model**, **operational/hardware/safety guardrails**, **acceptance conventions**, and a **decision ledger** with alternatives and justification. A reader should need nothing outside this file + the repo to act.
>
> **Supersedes** `plans/2026-07-05-state-and-roadmap.md` and `plans/2026-07-05-cofire-suppression-spec.md` for current co-fire status. Those files remain historical records only; their stale #92/current-R3 framing is superseded by this file and ADR-0009.
>
> **Provenance.** Grounded in the repo at `origin/main = 4ea003e` after PRs #97/#98 merged, plus the full 2026-07-06 working session: PRs #88–#98, three session-log reviews, multiple Opus verification workflows, an 318-alert population analysis, an operator-authorized live settlement re-label, and two orthogonal Codex audits (`M-AUDIT-CODE-INTEGRITY` / `M-AUDIT-RECORD-COMPLETENESS`, findings folded in §12). Every load-bearing claim cites a file:line, commit, PR#, or measured value. Corrections made after verification are shown inline (verify-don't-trust — the record shows its own error-corrections rather than hiding them; see the #94 overclaim in §2.3 and the post-merge status in §16).

---

## 0. TL;DR — current state at a glance

- `origin/main = 4ea003e` after PRs #98/#97; latest merged-lane full checks recorded in WORKLOG: `scripts\verify.py` green (`1395` then `1396` passed / `94` skipped in the two fix lanes) plus `scripts\db_local.py verify` green where DB scope applied.
- **The co-fire alert-quality program is feature-complete, durable, and honestly scoped across #88–#98.** It groups Kalshi multi-leg event co-fires so cross-market-hedge false positives are visible at review time. It is **read-side only and default-OFF**; live emission is byte-identical; governance stays per-alert.
- **A larger-cohort re-validation (#94/#95/#98) confirmed the constants hold at population scale and produced a corrected, honest conclusion** (a prior "zero-tp-loss guarantee at scale" claim was caught as a Candidate-B tautology by dual adversarial review and reworded before merge). It surfaced and fixed the settlement-TP temporal guard, then fixed the second post-close label (`9a357683`) in #98. The current enlarged reval state is `LABELING_RULE v1.2`, `fp=64`, `noise=24`, `tp=22`; `9934a6e1` and `9a357683` are both noise in the reval JSON.
- **The earlier "no-risk toggle" over-assumption is corrected.** The `review-packet --group-cofire` reduction=0 bug found in §14 was fixed in #98; a live read-only smoke reported `non_partial_reduction=185`. Flipping grouped co-fire on remains an operator decision because it changes operator workflow, not because a known reduction-count defect remains. Everything else forward is explicitly gated, dormant, or a deliberate non-goal (§5/§16).

---

## 1. Product & architecture context

- **Product.** Windows-native, local-only prediction-market flow intelligence for Polymarket/Kalshi-style venues. Capture public market events, preserve raw external payloads, normalize trades, compute baselines, emit explainable local anomaly alerts. (See `AGENTS.md`, `LOCAL_ONLY_SCOPE.md`.)
- **Runtime/storage.** Python 3.11+, Postgres-first (local Docker Desktop). No second durable store until Postgres constraints are measured.
- **Emission pipeline (load-bearing facts).** Per-trade synchronous, raw-first: `insert_raw_event` (`runner.py:191`) → `insert_trade` (`:388`) → `engine.evaluate` (`:413`). Single connection, NOT a single wrapping transaction — each repo write commits in its own transaction (`raw_events.py`, `trades.py`); the raw-first invariant holds (raw is never lost) but derived records can be missing on a mid-sequence crash by design. One existing suppression tier keyed `(venue, market, rule, outcome)` @300s (`runner.py:426-436`).
- **Local-only, non-negotiable boundaries.** No SaaS/hosted/billing/RBAC; no live API in default tests/verify; `fetch_outcomes.py` is the ONE opt-in, operator-authorized, read-only public live check; no order placement; raw stored before derived trusted.

---

## 2. What we have accomplished (verified)

### 2.1 The co-fire program (#88–#92) — why it exists and why this design

**Problem.** M-TRUTH labeling of 44 settlement-truth-labeled alerts (16 tp / 13 fp / 15 noise) revealed genuine false-positive-rate BREACHes. `directional_cluster_v1` reproduces at **19.7%** (severity high) on the live DB. The `volume_spike_v1` current-floor **31.5% IS reproducible** — `28/89`, denominator = latest-reviewed `volume_spike_v1` rows where `evidence.this_trade_usd >= 850` (the min-trade floor); all-reviewed is `77/138 = 55.8%` (the `51.6%` cited in an earlier draft was a meta-verify mis-derivation that does NOT reproduce — corrected via #97's `reports/alert-quality/breach-denominators-2026-07-06.md`). A third rule, `momentum_v1`, `4/36 = 11.1%` (below target — same `cross_market_hedge` exposure but not a breach), was outside the original "two BREACHes" frame (§13 #5). Root cause of both: **Kalshi multi-leg event co-fires** — e.g. World Cup GER-CIV fires 8 legs × 4 rules; the legs are `cross_market_hedge` false positives that are invisible at fire time because each leg is evaluated independently.

**Sequenced, measurement-before-enforcement roadmap (chosen for safety):**
- **R0 (semantics, done):** established the grouping key = Kalshi `event_ticker` (== `facts.event_ticker` 43/43, zero under/over-group), window = **900s** pairwise radius (300s misses the observed 726s hedge pair `39bd1f35↔623164c5`), clustering = pairwise-radius (not consecutive-chain), and that **leg-aware visibility is mandatory** (a group can hold both tp and fp legs, timing-separated). `match_key` grouping was REJECTED (over-groups cross-market-type tickers that labels exclude by design; a match-basket is an unvalidated hypothesis needing a labeling-rule extension, not a key swap).
- **R2 (category governance, #88 `27381f7`):** `alerts fp-rate` now shows each rule's not-actionable share by the existing `alert_reviews.false_positive_category`. Additive/backward-compat; measures the problem without touching emission. Chosen first because it does not depend on R1.
- **R1 (offline viability harness, #89 `d69a4f7`):** standalone `src/pmfi/pipeline/cofire.py` (pure `derive_event_ticker` + `group_cofire` union-find pairwise-radius 900s, **Candidate-B "drop-nothing"** — retains every leg) + `validate_cofire.py` gate. Gate on the 44-cohort: `removed_tp=0`, `tp_visible=16/16`, `missed_labeled_hedge_fp=0`, queue 44→22. Proves the grouping is safe offline before any live change.
- **R3 (read-side grouped view, #90 `917c0c1`, default-OFF):** because Candidate-B drops nothing, R3 was **reframed from emit-suppression to an operator-facing GROUPED VIEW** at `alerts list` + `review-packet` behind `--group-cofire`/`--expand`, default OFF. Queue reduction lands at the READ layer; live emission (`runner.py`/`engine.py`) is byte-identical; governance stays per-`alert_id` (no group label).
- **Durable docs (#91 `b1bbaa8`, #92 `5957c69`):** tracked the validation inputs (the previously-untracked `outcomes-2026-07-02.json` DEFAULT_INPUT, a fresh-clone-repro fix) + plan/spec/report; recorded the decision in `docs/adr/0009-cofire-event-grouping.md` (Accepted) + `docs/ops/OPERATOR_QUICKSTART.md`.

**Why this design is the most adequate (Talmudic A/B/C, from `plans/2026-07-05-cofire-suppression-spec.md`):**
- **Candidate A (annotate):** lead now — surfaces the co-fire context without altering the queue. Safe, low-value alone.
- **Candidate B (group/rollup, drops nothing):** chosen as the grouping primitive — collapses operator queue items while retaining every leg, so no tp can be structurally dropped. This is why R3 could be read-side.
- **Candidate C (suppress-keep-one) — REJECTED:** score/conviction cannot predict the settlement-winning leg, so suppression would drop labeled tp (e.g. `92c182df`). Rejected as lead. Emission-side suppression, if ever wanted, is gated behind a hard leg-aware zero-tp-loss gate (§5).
- **Leg-aware visibility is mandatory** because a lossless group can still HIDE a tp if a display collapses the group to a single parent label — proven at scale in §2.3 (`parent_label_conflicts`). ADR-0009 therefore mandates reviews stay per-`alert_id` with no group-level label.

**Process note that shaped the design.** The R3 hardening exposed a real limit-frontier pagination bug that BOTH Codex implementer and Codex reviewer missed; a separate Opus verification lane caught it. This established the standing rule: **never merge co-fire/emission-adjacent changes on Codex self-report alone; run an independent Opus verification.**

### 2.2 Co-fire residuals (#93 `b529d82`)

A log-review assessment surfaced three low-severity residuals; all landed as three isolated commits, independently verified before merge:
1. **Stale plan pointer** — `plans/2026-07-05-state-and-roadmap.md` L5 `b1bbaa8`/#91 → `5957c69`/#92 (the plan predates #92's own doc-only commit).
2. **Scratch-db orphan janitor** — `scripts/db_local.py sweep-scratch` (dry-run default; `--apply` drops). **Safe by design:** drops only `pmfi_testiso_%` databases with **zero `pg_stat_activity` connections**, via `\gexec`, **no `WITH (FORCE)`**. A live test holds a connection → excluded by construction; the TOCTOU window is fail-safe (a no-FORCE `DROP DATABASE` refuses an in-use db and aborts under `ON_ERROR_STOP=1` → never data loss). `verify.py` deliberately UNTOUCHED (do not couple the sacred gate to a low-severity janitor). Why this addresses a real risk: per-process scratch dbs (`pmfi_testiso_<pid>_<uuid>`) drop in a `finally` that does NOT survive SIGKILL/OOM → orphans accumulate; the "serialize DB tests for integrity" folklore was a partial misdiagnosis (per-process dbs cannot cross-corrupt; the true ceiling is `max_connections` + orphan accumulation).
3. **`>3`-segment ticker guard** — `src/pmfi/pipeline/cofire.py` `derive_event_ticker` `< 3` → `!= 3`. **Grounded, not assumed:** the live Kalshi `venue_market_id` segment distribution is 2-seg=4 (only the `KXEXAMPLE` seed) / 3-seg=278 / 4-seg=0 at the alert level, so `!= 3` changes derivation for ZERO alert-bearing tickers and the validate gate is unchanged. Conservative reject (unknown grammar → singleton, no mis-group) was chosen over speculative `rsplit`, per spec §4.

### 2.3 Larger-cohort constant re-validation (#94/#95) — and the tautology lesson

The operator asked to re-validate the co-fire constants (900s window / `event_ticker` key / `!=3` grammar) on a larger cohort than n=44. Executed as three tasks:
- **A — window-robustness sweep (offline, on the 44-cohort).** *Committed evidence* (tracked validation reports): `--window-s 300` FAILs (misses the labeled 726s hedge pair), `900` PASSes, `1800` PASSes. *Session-measured* (an in-session `validate_cofire --window-s` sweep whose full granularity was NOT committed as artifacts): the gate held across roughly **[600s, 3600s]** with `queue_reduction` growing ~22→25 and no tp-loss up to 1h, and the failure cliff sits near ~600s (the 726s pair closes transitively below its direct link). Treat the exact band endpoints + the 22→25 figure as session-measured, not artifact-backed; the load-bearing, artifact-backed conclusion is: **300s fails, 900s passes with margin above the 726s cliff, up to 1800s stable** — robust on the labeled cohort, which is the ceiling of what n=44 can show. (Commit the granular sweep outputs if this band must be independently reproducible.)
- **B — population structural (all 318 alerts, no labels, read-only).** Ran the production `group_cofire` over the full population: `!=3` drops only the seed; **0 cross-event merges**; 900s chains are dense/legitimate (max consecutive intra-group gap 817s < 900); GERCIV correctly SPLITS where it has >900s gaps. **GERCIV is NOT unique** — the multi-leg co-fire pattern recurs on MEXKOR (47 alerts/3 legs, larger than GERCIV), NZLEGY (11/2), and a Bitcoin strike ladder KXBTCD (17/2). Independently SQL-verified. Constants structurally safe at 7.2× scale — but structure ≠ the label-dependent zero-tp-loss gate.
- **C — larger labeled gate (operator-authorized opt-in live settlement fetch, #94 `ef15cfb`; corrected in #95/#98).** Enlarged the labeled cohort to **110** (44 ∪ 66 new, deduped, existing labels win) by live-fetching Kalshi/Polymarket settlement for the new events and labeling under the same LABELING_RULE. The originally generated gate was pre-correction; after the v1.2 temporal corrections for both `9934a6e1` and `9a357683`, the current gate is PASS with `removed_tp=0`, `tp_visible=22/22`, `missed_labeled_hedge_fp=0`, `parent_label_conflicts=7`.

**The lesson (why the record is trustworthy).** The first C report claimed a "zero-tp-loss guarantee preserved at scale." Two independent adversarial reviews — an Opus verification workflow and Codex agent2 (a different model) — **both** caught that `removed_tp=0`/`tp_visible=N/N` are **unfailable identities under Candidate-B drop-nothing** (every leg is always in the group index → these checks cannot fail for any cohort). The current corrected value is `tp_visible=22/22`. The genuinely load-bearing result is (b) `parent_label_conflicts=7`, which shows real mixed-label groups now exist (a BTCD group holds tp legs with an fp leg) — this **strengthens** the ADR-0009 leg-aware-review mandate, it does NOT prove parent-level trust. (Note per §13: `missed_labeled_hedge_fp=0` is a derive/bucketing *consistency* guard — 0-by-construction when the candidate and grouping windows match — NOT an independent grouping discriminator; the window-sensitive 300s→FAIL is driven by the *hardcoded* GERCIV anchor-pair check in `validate_cofire.py`, not by `missed_labeled_hedge_fp`.) The report was corrected before merge; the overclaim never landed.
- **Two known-limits surfaced.** (1) **Strike-ladder hedge gap:** LABELING_RULE R3 (≥2 distinct market legs, same event, ≤15min) does not model a Kalshi strike ladder (adjacent OTM bands of one underlying) as a correlated hedge/survivorship family → some BTCD "tp" are provisional. RECORDED (open). (2) **Settlement-TP temporal gap:** FIXED in #95.
- **Temporal-guard fixes (#95 `5f7db80`, #98 `cbb9198`).** `fetch_outcomes.py` `settled_within_7d` had no lower bound → a fire AFTER `close_time` could be labeled tp via settlement (`9934a6e1` fired ~3.5h post-close; `9a357683` fired ~38m post-close). Fix: require `timedelta(0) <= (close - fired) <= SETTLE_D`; LABELING_RULE v1.1→v1.2; `9934a6e1` and `9a357683` are now noise in the enlarged reval JSON; gate re-run PASS `tp_visible=22/22`.

### 2.4 Repo hygiene (#96 + worktree cleanup)

- **#96 `b5f7e2f`:** added `.gstack/` to the tracked `.gitignore` (gstack skill cache).
- **Worktree cleanup (operator-authorized, reversed the 07-05 "no cleanup" decision):** 71 → 3 worktrees. **Key lesson recorded:** squash-merge makes `git merge-base --is-ancestor` (and diff-vs-main) FALSELY report merged branches as unmerged; the authoritative merged-detection is the GitHub merged-PR head-ref set (`gh pr list --state merged`). Removed with `git worktree remove` and NO `--force` (git self-refuses dirty trees); the only force-removal was after confirming the dirty file was a regenerated-report byproduct. **All branch refs preserved → every removal reversible** via `git worktree add`.

---

## 3. Current true state

- `origin/main = 4ea003e` (#98/#97 merged); latest merged-lane verification is recorded in WORKLOG (`scripts\verify.py` green in both fix lanes; DB verify green for the cohort/view fix).
- Co-fire feature is **default-OFF**; live emission byte-identical; `pmfi.pipeline.cofire` imported ONLY in read-side `src/pmfi/commands/alerts.py`; `runner/engine/rules` untouched.
- Worktree inventory is local-session state, not product state. After the earlier cleanup only the intentionally retained local lanes remained; this doc-consistency lane uses `worktrees/docsync` on `codex/cofire-doc-consistency`.
- Local Postgres UP (`pmfi-postgres`, 5433); primary fingerprint `alerts=318, alert_reviews=301, raw_events=661380` (read-only throughout).
- Working tree dirty by the operator's established pattern: `.codex/config.toml` modified (danger-full-access, intentionally uncommitted + gitignored), plus many untracked report/state artifacts (canonical, live in root).
- 75 local branches preserved (post worktree cleanup); both Codex threads idle/standing-by after the audits (§12).

---

## 4. Known limits & honest open questions

1. **Constants are n≈110-validated, still smallish.** 900s/`event_ticker`/`!=3` are structurally safe at 318-population scale and pass the 110-label gate, but each labeled defect is still driven by a handful of events. A full v1.2 re-label of the whole cohort would require a fresh operator-authorized live fetch. Advisory, not blocking.
2. **Strike-ladder hedge gap (OPEN).** R3 does not treat adjacent-band ladders (e.g. KXBTCD) as hedge/survivorship families; BTCD outcome-test tp on such ladders are provisional and must be reviewed leg-by-leg. Fixing this is a LABELING_RULE extension (§5).
3. **`!=3` future-grammar assumption.** Holds for all 278 observed 3-segment Kalshi tickers; a future >3-segment alerting Kalshi market would silently not-group (degrade to singleton, no mis-group — safe, but a benefit gap) until the grammar is re-validated.
4. **The fp BREACHes are VISIBLE, not SUPPRESSED.** `directional_cluster_v1` 19.7% (`12/61`, high, breach); `volume_spike_v1` 31.5% (`28/89` floor-conditioned `this_trade_usd>=850`, low, breach) / 55.8% all-reviewed; `momentum_v1` 11.1% (`4/36`, below target, same cross_market_hedge exposure, was out-of-frame — §13 #5). All reproduced on the live DB via #97's `breach-denominators-2026-07-06.md`. All are now attributable (#88) and collapsible in the default-OFF grouped view (#90), but emission still fires every leg. Whether governance-visibility is sufficient, or recalibration/suppression is warranted, is an operator decision (§5). This must not be recorded as "resolved."
5. **`tp_visible` is not a regression detector.** Under Candidate-B it is a structural invariant; only `missed_labeled_hedge_fp` and `parent_label_conflicts` carry information. Any future validation must not lean on `tp_visible` as evidence.

---

## 5. Forward roadmap (each: what · why · gating · why-optimal)

1. **Operator flips `--group-cofire` on** — *what:* enable the read-side grouped view for triage. *why:* collapses co-fire legs into groups at review time. *gating:* the view must be run end-to-end read-only before enablement; this is now done for both `alerts list` and `review-packet` after #98. The prior `review-packet --group-cofire` reduction=0 defect is fixed, with a branch-pinned live read-only smoke reporting `non_partial_reduction=185`, `operator_group_count=62`, and `partial_group_count=8`. *acceptance:* no error on real DB; correct partial_group on `--since`/`--limit` boundaries; DB fingerprint unchanged. *owner:* operator (toggle). *why-optimal:* read-side + reversible, but "verify-before-enable" replaces the earlier "zero-risk" framing.
2. **Decide the two fp BREACHes' disposition** — *what:* choose between (a) accept governance-visibility as sufficient, (b) recalibrate the rules, (c) pursue emission-side suppression. *why:* they are genuine breaches; visibility mitigates operator burden but does not change the emitted stream. *gating:* operator judgment; (c) is itself gated below. *why-optimal:* sequencing measurement (#88/#90) before enforcement keeps optionality without irreversible emission changes.
3. **Emission-side co-fire suppression (R3-emit) — GATED / optional** — *what:* actually suppress redundant hedge legs at emission. *why:* only if visibility proves insufficient. *gating:* operator greenlight + generalize `replay_from_db` to an injectable engine variant + `alert_reviews` join (currently volume_spike-only, `calibration.py:14`) + a HARD leg-aware zero-tp-loss offline gate + adversarial + Opus verification. *why-optimal:* emission is fenced and Candidate-B drops nothing, so suppression cannot be validated safely without the general harness + a real (non-tautological) gate — the reviewer will not endorse it otherwise.
4. **Strike-ladder / match-basket hedge modeling** — *what:* extend LABELING_RULE to model adjacent-band ladders and cross-market-type match baskets as correlated hedge families. *why:* closes the known-limit that inflates provisional tp on ladders. *gating:* a labeling-rule extension + new labels (not a key swap); operator decision. *why-optimal:* R0 already showed `match_key` over-groups by design; the correct fix is rule semantics + labels, done deliberately, not a grouping-key change.
5. **Larger-cohort constant re-validation** — *what:* re-run the labeled gate on more settlement-truth labels. *why:* de-risk the n≈110 constants. *gating:* a fresh operator-authorized live fetch (crosses the no-live-API-default boundary). *why-optimal:* the gate is label-dependent; only more labels move it, and labels require the opt-in live check.
6. **R1 replay-harness generalization** — *what:* generalize `replay_from_db(rules_config=)` beyond volume_spike-only. *why:* prerequisite ONLY for R3-emit. *gating:* dormant unless (3) is greenlit. *why-optimal:* build it exactly when needed, not speculatively.
- **Deliberate non-goals (do not "fix"):** group-level review labels / group-level `alert_reviews` write path (ADR-0009 mandates per-`alert_id`); a second durable store; any SaaS/hosted/live-default scope.

---

## 6. Orchestration & delegation model

- **Roles.** Claude orchestrates, plans, fences, verifies, and merges. **Codex executes** implementation on isolated worktrees (per-agent inbox reply files avoid the shared-inbox last-writer race). **Opus workflows** do read-only exploration/verification. Per operator: all Claude agents/workflows are Opus-only; Codex threads stay gpt-5.5/xhigh (`019f2203`=agent1 implementer, `019f2204`=agent2 reviewer).
- **The review→verify separation is load-bearing, not ceremony.** Evidence: an Opus lane caught the R3 limit-frontier bug both Codex lanes missed (#90); a dual Opus+agent2 review caught the #94 tautology overclaim. Standing rule: **never merge co-fire/emission-adjacent or methodology-laden changes on Codex self-report alone.**
- **Proportionality.** Match verification depth to risk: for a trivial deterministic change (e.g. #93 slices, #95 guard) independent reproduction by the orchestrator suffices and a separate agent2 lane is skipped; for methodology/emission-adjacent changes, run the full adversarial stack. Do not over-orchestrate a one-line edit.
- **Coordination hygiene.** Inspect a Codex thread (read-only) before sending; do not send mid-turn; confirm the message materialized; **arm a background watcher after every /ipc delegation** (Codex threads are not harness-tracked); reply files are the durable signal.

---

## 7. Operational / hardware / safety guardrails

- **One shared local Postgres.** Serialize DB-gated pytest; never launch parallel suites against it. verify.py is single-process (no `-n`) — keep it that way. If ever parallelized, the ceiling is `max_connections`, not cross-corruption (per-process scratch dbs isolate data).
- **Scratch-db hygiene.** `db_local.py sweep-scratch [--apply]` clears orphaned `pmfi_testiso_%` dbs (zero-connection-guarded, no FORCE); run when no DB tests are in flight.
- **Python invocation.** NEVER bare `python` (Windows Store alias). Use `C:/Users/benny/AppData/Local/Programs/Python/Python311/python.exe` or a `.venv` / `PYTHONPATH=src`.
- **Worktree hygiene.** Now 3 (cleaned). Merged-detection MUST use the GitHub merged-PR set, not ancestor/diff (squash-merge lies). `git worktree remove` preserves branches and self-refuses dirty trees — never blind `--force`.
- **Security boundary.** `.codex/config.toml` danger-full-access stays UNCOMMITTED + gitignored (containment = worktree isolation + read-only review fences, which every lane honored). Local-only; no-live-API-in-default (`fetch_outcomes.py` is the sole opt-in public read-only check); no secrets committed; no order placement.
- **Disk.** ~806G free; the worktree tree was ~1.2G before cleanup (shared `.git/objects`) — never a pressure point.

---

## 8. Acceptance-criteria & verification conventions

- **"Done/verified" means independently reproduced**, not self-reported: the orchestrator (or an Opus lane) re-runs the gate/fence, not just reads the implementer's numbers.
- **Standard gates per change class.** Code touching pipeline: `scripts\verify.py` green + emission-fence diff empty + `consistency_audit` + `git diff --check`. Co-fire/labeling: also the `validate_cofire.py` gate on the relevant cohort. DB work: `db_local.py verify` + primary fingerprint unchanged. Data/label changes: audit the whole cohort for the same class of defect (not just the one instance).
- **No weakening.** Never relax a test/gate to pass. Banned committed words `h·o·o·k(s)`/`call·back(s)` (`consistency_audit.py:154-155`). No AI/assistant authorship trailer on commits/PRs.

---

## 9. Decision ledger (material decisions · alternatives · why chosen)

| Decision | Alternatives | Why chosen |
|---|---|---|
| R3 as read-side grouped view, default-OFF | Emit-suppression; always-on | Candidate-B drops nothing, so read-side realizes the benefit with byte-identical emission and full reversibility; default-OFF keeps operator control |
| Candidate-B grouping primitive | A (annotate only); C (suppress-keep-one) | B collapses queue items without dropping any leg; C can drop a labeled tp (winning leg unpredictable); A alone is low value |
| Reviews stay per-`alert_id` (no group label) | Group-level label / review | A single parent label can hide a tp in a mixed group (`parent_label_conflicts`); leg-aware review is mandatory (ADR-0009) |
| `derive_event_ticker` rejects `!=3` | Keep `rsplit` for >3-seg | DB shows zero >0-alert non-3-seg tickers; conservative reject avoids mis-grouping unknown grammar, no regression |
| sweep zero-connection guard, no FORCE, dry-run default | FORCE; PID-liveness; verify.py integration | Zero-connection + no-FORCE is fail-safe under concurrency with no Windows PID logic and no coupling to the sacred gate |
| Settlement-TP requires fire ≤ close (v1.2) | Leave as `<=SETTLE_D` only | A post-close fire cannot be pre-event informed flow; `9934a6e1` was a concrete artifact |
| Correct the #94 report before merge | Merge as-is; block the PR | Data was sound; only the conclusion overclaimed (tautology) → correct the record, keep the valid data |
| Worktree merged-detection via GitHub PR set | ancestor / diff-vs-main | Squash-merge makes ancestor/diff falsely report merged branches as unmerged |

---

## 10. Key code facts / anchors

- Co-fire primitive: `src/pmfi/pipeline/cofire.py` — `derive_event_ticker` (`!=3` guard), `group_cofire` (`<= window_s`, union-find, Candidate-B).
- Read-side view: `src/pmfi/commands/alerts.py` (imports cofire at ~483/520; `--group-cofire`/`--expand` gating).
- Gate: `reports/alert-quality/validate_cofire.py` (`tp_visible`/`removed_tp` are Candidate-B invariants; `missed_labeled_hedge_fp` is a derive/bucketing consistency guard, 0-by-construction when windows match; the **hardcoded GERCIV anchor-pair check** is the actual window-sensitive lower-bound gate; `parent_label_conflicts` is the substantive diagnostic).
- Labeler: `reports/alert-quality/fetch_outcomes.py` (LABELING_RULE v1.3; opt-in live; `settled_within_7d` lower-bounded; R3 hedge is now a caveat on an outcome-evaluated leg, not a pre-outcome short-circuit).
- Cohorts: `outcomes-2026-07-02.json` (44, tracked, immutable v1.1 snapshot with `_audit_note`), `outcomes-cofire-reval-2026-07-06.json` (110, `LABELING_RULE v1.2`, `9934a6e1`=noise, `9a357683`=noise, `tp=22`).
- Ops: `scripts/db_local.py` (`sweep-scratch`), `scripts/verify.py` (single-process gate).
- Decision docs: `docs/adr/0009-cofire-event-grouping.md`, `docs/ops/OPERATOR_QUICKSTART.md`, `plans/2026-07-05-cofire-suppression-spec.md`.

---

## 11. Glossary

- **Co-fire:** multiple alerts on distinct legs of the same Kalshi event firing within the window — typically a `cross_market_hedge`, not independent informed flow.
- **Candidate-B / drop-nothing:** grouping that collapses queue items while retaining every leg; makes `removed_tp`/`tp_visible` structural invariants.
- **`parent_label_conflicts`:** count of groups holding legs of differing labels — the real signal that leg-aware review is required.
- **`missed_labeled_hedge_fp`:** labeled hedge pairs that end up in different groups — a derive/bucketing *consistency* guard (0-by-construction when the candidate and grouping windows match), NOT the discriminating gate. The hardcoded GERCIV anchor-pair check is the window-sensitive lower-bound gate.
- **Provisional tp:** an outcome-test tp on a structure the labeling rule does not yet model as a hedge (e.g. a strike ladder) — low-confidence, review leg-by-leg.

---

## 12. Audit reconciliation (2026-07-06)

**Method.** Two orthogonal read-only audits were commissioned: agent2 (Codex) = record/consistency/assumptions/forward-scoping; agent1 (Codex) = code/data/test ground truth. **agent1 misfired** (a shared-inbox delegation race conflated its task; it produced only a thin record-side supplement and did NOT run the gates/DB checks). Its lane was therefore re-run by an **Opus verification workflow** (gates + completeness scans + fence/edges/sweep). Net coverage is complete: record audit (agent2) + code/data audit (Opus).

**CONFIRMED (independently reproduced):**
- Gates at audit time: `verify.py` 1393/94; `validate_cofire` 44-cohort PASS (16/16), enlarged cohort PASS at the then-current count; `db_local.py verify` PASS. Post-#98 current reval gate: `tp_visible=22/22`; full verification and DB verify are recorded in WORKLOG.
- **Emission fence clean** — `cofire` imported ONLY in two lazy read-side sites (`alerts.py:483,520`); zero matches in runner/engine/rules; a test self-enforces it. Read-side/default-OFF isolation holds.
- **The multi-leg event set is COMPLETE** — cross-validated two ways over all 282 Kalshi alerts: exactly {MEXKOR, GERCIV, KXBTCD-26JUN1817, NZLEGY} have ≥2 distinct legs within 900s. No missed event; the reval cohort's 4-event choice was right. (This assumption held.)
- cofire.py edges mostly SAFE+TESTED: 2/4-seg and empty/None `venue_market_id` degrade to harmless singletons; exact-900 boundary inclusive & tested; non-kalshi no-op tested.
- Assumptions (agent2): `!=3` grammar / 900s / Candidate-B-safe / sweep-testiso-only / per-alert-review-mandate are all judged SAFE-for-current-use with recorded latent risks (§4) — no landmine.
- The two fp BREACHes are HONESTLY recorded as visible-not-suppressed. Governance boundary (local-only, opt-in live, danger-full-access uncommitted) shows no drift.

**DEFECT FOUND (real; this is what the audit was for) — a SECOND missed post-close-fire tp:**
- **`9a357683`** (`directional_cluster_v1`, the HIGH-severity rule; market `KXWCSCORE-26JUN18CANQAT-CAN6QAT0`, a SINGLE-leg event so no R3 override) fired **2026-06-19T00:39, ~38 min AFTER its live `facts.close_time` 2026-06-19T00:00:43Z**, earned tp SOLELY via settlement (no kept move). Under LABELING_RULE v1.2 the temporal guard flips it tp→noise — the exact case #95 fixed for `9934a6e1`. #95 re-labeled only `9934a6e1`; #98 corrected `9a357683` in the enlarged cohort JSON. The immutable 44-cohort JSON remains a flagged v1.1 snapshot, and DB `alert_reviews` still serves both post-close ids as tp until operator ratifies DB writes. **Lesson (now a standing rule, §8): a labeling-rule bug fix must re-apply the fixed rule to the WHOLE cohort, not just the surfaced instance.**
- **Correct v1.2 enlarged-cohort count is tp=22** (not 23): both `9934a6e1` and `9a357683` are post-close noise.
- Note the `close_time` source discrepancy: the labeling authority is the live `facts.close_time` (post-close for `9a357683`), NOT the DB `markets.close_ts` (which differs by ~13.7d) — a DB-only scan misses this; the JSON-facts scan catches it.

**RECORD INCONSISTENCIES (agent2; real, none are code bugs):**
- **Stale companion artifacts (fixed/superseded in #98):** `co-fire-validation-reval-2026-07-06.md` (+ w300/w1800) now reads `fp=64, noise=24, tp=22`; `cofire-reval-new-labels-2026-07-06.md` and `outcomes-cofire-reval-new-2026-07-06.json` carry superseded v1.1 banners; the enlarged JSON's top-level `"rule"` metadata is `LABELING_RULE v1.2`.
- **Stale committed plan/spec (fixed in this doc-consistency pass):** `plans/2026-07-05-state-and-roadmap.md` and `plans/2026-07-05-cofire-suppression-spec.md` are historical records and now carry supersession pointers to this file.
- **Omissions:** the strike-ladder known-limit is not in `fetch_outcomes.py`'s LABELING_RULE docstring (only in the reval report/WORKLOG); ADR-0009's "zero true-positive loss" should tighten to "zero TP leg-visibility loss in the validated cohort"; ADR "always expose every leg" is overbroad vs `--expand` (full leg rows require it); the plan/WORKLOG cite untracked `m-truth-*` files as if durable-tracked.

**LATENT (low):** cofire.py `_item_event_ticker` bypasses the `!=3` guard if a caller pre-injects `event_ticker`/`facts.event_ticker` — NOT reachable in the production read path (`_cofire_item` at `alerts.py:501` injects a **pre-guarded** `event_ticker = derive_event_ticker(market,venue)`, never a raw `facts.event_ticker`), latent only for a future caller feeding a >3-seg `facts.event_ticker`. See §13 #4 for the related view-vs-labeling event-ticker source divergence. Naive/aware datetime mixing in `_parse_fired_at` (low).

**SECONDARY (operator awareness, NOT v1.2 misses):** 6 alerts (`75de0e9a`, `efb6d971`, `422e02cd`, `a10d4a13`, `c9e5bcd6`, `7c9e2d62`) labeled tp fire after their DB `close_ts` on 15-minute markets, but via earlier live-proof/overlap-proof *dominant-side* reviews, NOT the settlement OT-TP path — so the v1.2 settlement guard does not govern them. An operator may wish to sanity-check whether tp on 15m markets firing after close is intended.

**OPERATOR DIRECTION (2026-07-06, ratified; historical before PRs #97/#98):** (1) finalize the record first; (2) investigate 81-tp vs R3 before deciding; (3) correct JSON cohorts but hold DB re-ratification for explicit operator ratification. PRs #97/#98 executed the authorized non-DB fixes and resolved 81-tp vs R3 as a lane mismatch; DB `alert_reviews` re-ratification remains held.

**FIX PLAN (current status after PRs #97/#98 and this doc-consistency pass; see §16 sequencing):**
1. DONE #98: re-label `9a357683` tp→noise in the enlarged cohort; re-run the gate (`tp_visible=22/22` PASS); flag the 44-cohort JSON as an immutable v1.1 snapshot rather than relabeling it.
2. DONE #98: regenerate or supersede-banner the stale companion artifacts to the corrected tp=22 / v1.2 state.
3. DONE #97 / this pass: narrow R3 in `fetch_outcomes.py` to v1.3, record the strike-ladder known-limit, tighten ADR wording, and add supersession pointers on the 07-05 plan/spec.
4. **DB re-ratification of `9934a6e1` + `9a357683` tp→noise in `alert_reviews` is operator-gated** (labeling rule: operator ratification gates any DB write) — do NOT do unilaterally; surface for decision.
5. Consider whether to fold the `!=3`-via-facts bypass into a guard/test (low priority).

---

## 13. Meta-verification (2026-07-06) — "are we entirely certain?"

A 4-lane adversarial Opus meta-verification re-checked §12's conclusions, the doc, and every assumption. **Overall verdict: RESIDUAL_UNCERTAINTY** — certain within the labeled-cohort scope, NOT certain DB-wide.

**RE-CONFIRMED with high rigor:**
- **Exactly two mislabels in the m-truth cohorts** (`9934a6e1` + `9a357683`) — an exhaustive symmetric v1.2 re-derivation over all 44+110 alerts found **no third**, 0 category/hedge-membership/false-negative divergences; R0/R1/R2/R3 (incl. R2-before-R3 precedence) + the outcome test all consistent. `close_time` authority = live `facts.close_time` (code-confirmed, guarded, tested).
- **Multi-leg 4-event set complete** — triple-confirmed (gap-sessionization, production union-find, SQL recursive transitive-closure); near-900s boundary pairs exist but none load-bearing.
- **This doc is accurate/coherent** (all SHAs, counts, anchors verified) and **all five of agent2's record findings are true** (not false positives).

**NEW residual gaps this meta-pass surfaced (these are why we are NOT yet "entirely certain"):**
1. **[HIGH] The live grouped view has NEVER been run end-to-end.** `alerts list --group-cofire`/`--expand`/`review-packet --group-cofire` is proven only by unit tests + flag-off byte-identity — never executed against the real 318-alert/301-review DB. Partial-group / pagination correctness on real data is unverified by execution. (Highest-value missing check; read-only-runnable.)
2. **[HIGH] 81 of 197 DB-wide `tp` are multi-market co-fire legs never reconciled against R3.** LABELING_RULE R3 marks *all* sweep members (incl. the settlement winner) `fp/cross_market_hedge`; DB-wide, 81 ratified `tp` sit in ≥2-distinct-market events (e.g. MEXKOR = 34 tp + 3 fp + 10 noise in one event). The program validated grouping mechanics but never re-audited the historical `tp` corpus against the hedge rule it adopted. Whether these are genuine informed-flow tp or survivorship hedge-fp — i.e. whether R3 governs the whole ratified corpus or only the m-truth cohort — is an **unreconciled governance question**, not a proven mislabel.
3. **[RESOLVED via #97] The `volume_spike_v1` "31.5%" BREACH figure IS reproducible** — `28/89` with denominator = latest-reviewed `volume_spike_v1` where `evidence.this_trade_usd >= 850` (the current floor); all-reviewed = `77/138 = 55.8%`; `directional_cluster_v1` `12/61 = 19.7%`; `momentum_v1` `4/36 = 11.1%`. The earlier "51.6% floor-conditioned" was a meta-verify mis-derivation that does NOT reproduce. This finding — that 31.5% is real, not stale — is the OPPOSITE of an earlier draft's claim; the exact denominators are now recorded in `reports/alert-quality/breach-denominators-2026-07-06.md`.
4. **[MEDIUM] Event-ticker source divergence (view vs labeling authority):** the live view (`_cofire_item`, `alerts.py:501`) derives `event_ticker` ONLY via the 3-seg string split, never reading `facts.event_ticker`, whereas the labeling authority (`cofire._item_event_ticker` / `fetch_outcomes`) *prefers* `facts.event_ticker`. Latent today (all real Kalshi tickers 3-seg) but the two grouping paths would diverge for any real non-3-seg ticker carrying `facts.event_ticker`. Cross-path equivalence is untested.
5. **[LOW] `momentum_v1` carries the same `cross_market_hedge` defect** (3 fp, 11.1% not-actionable) but sits outside the "two BREACHes" narrative that scoped governance + the grouped view.
6. **[LOW] 900s does not bind all legs of a real multi-market event** — MEXKOR (47 legs) has a 1676s consecutive gap, so union-find at 900s *fragments* the event into ≥2 temporal components. The assumption "900s generalizes" is therefore **false**; the window is a fitted constant validated on n=110.
7. **[INFO] The live DB has drifted from the MEMORY banner's snapshot:** `alert_reviews=301` (banner said 318), Kalshi segment-dist now `2:4 / 3:278` with **zero** real 4-seg tickers (banner: `2:2/3:118/4:1`). Validation snapshots and the current DB are not the same state; the `!=3` guard's >3-seg branch is unexercised on real data.

**Assumptions that DO NOT hold (corrected here):** "900s generalizes" (false — MEXKOR splits); "reval-set complete" (false — curated; 81 hedge-pattern tp unexamined); "only 2 post-close tp" (true cohort-locally, but NOT verified DB-wide — non-cohort tp lack a stored `facts.close_time` and can't be checked offline). **Assumptions that hold:** `!=3` (by construction, unexercised on real >3-seg), `facts.close_time` authority, Candidate-B does-no-harm, sweep testiso-only.

**Path to "entirely certain" (current status after PRs #97/#98):**
- DONE #98: run the grouped view end-to-end against the live DB (`--group-cofire`/`--expand`/`review-packet`, with/without `--since`/`--limit` near a co-fire boundary); confirmed no error + correct partial_group/reduction.
- DONE §15/#97: reconcile the 81 multi-market-event `tp` against R3; resolved as proposal-vs-ratified lane mismatch, not a defect. Remaining action is the 25-leg operator adjudication worklist, not an 81-row relabel.
- DONE #97: re-derive BREACH denominators; `volume_spike_v1` current floor reproduces at `28/89 = 31.5%`, all-reviewed is `77/138 = 55.8%`, and the earlier `51.6%` does not reproduce.
- DONE #98 for current grammar: added a cross-path equivalence guard for real three-segment Kalshi event-ticker grouping. Future non-3-segment grammar remains a low-risk benefit gap.
- HELD operator decision: decide `momentum_v1` (and any other cross_market_hedge rule) in-scope vs explicitly out-of-scope.
- OPTIONAL external housekeeping: refresh any non-repo memory/snapshot banner to the current DB state if it is used as an authority pointer.

---

## 14. Fresh sonnet+opus pass (2026-07-06) — record fidelity, scoping, and a live-run bug

A 4-lane pass (2 sonnet: recording-fidelity + scoping-adequacy; 2 opus: fresh-gap-hunt + over-simplification critic) re-checked the record and, crucially, **ran the grouped view end-to-end** for the first time.

**Live view executed end-to-end (read-only, real 318-alert/301-review DB) — the #1 prior gap, now closed:**
- `alerts list --group-cofire` and `--group-cofire --expand --format json` run clean (exit 0), render real co-fire groups (NZLEGY 11 legs, KXWNBAGAME 7 legs), confirm live mixed-label groups (tp + noise legs together). `--since` mid-group correctly splits with `partial_group=True`/`since_boundary`; `--limit` does not falsely mark partial. **The read view is idempotent (byte-identical repeat JSON), has NO write path, and the DB fingerprint was unchanged.**
- **[RESOLVED #98] `review-packet --group-cofire` previously reported zero reduction and marked every group partial.** Root cause: `cmd_alerts_review_packet` (`alerts.py:1319-1326`) included the mandatory default `review_state='reviewed'` in the filter-boundary test. #98 fixed that path; a live read-only smoke reported `non_partial_reduction=185`, `operator_group_count=62`, and `partial_group_count=8`.
- **[RESOLVED for review-packet #98] Product-facing grouped reduction is now visible in review-packet JSON.** `alerts list` still renders the grouping benefit as fewer rows rather than a numeric reduction counter.
- **RE-CONFIRMED holds:** the co-fire read view and the existing 300s suppression tier are orthogonal (key `(venue,market,rule,outcome)@300s` event-time vs `event_ticker@900s` read-side) — no conflict. `!=3` guard sound on live tickers.

**Corrections to over-assertions in earlier sections (the over-simplification critic's findings, accepted):**
- **Mislabel certainty is CLASS-SCOPED, not general.** "Exactly two mislabels, no third, with high rigor" (§12/§13) is certainty *within one searched defect class* (post-close-settlement-fire tp, evaluated from stored facts) over the *labeled cohorts only*. It is NOT general exhaustiveness — the very same "found the class we searched" logic had already missed `9a357683` once. DB-wide, non-cohort tp lack a stored `facts.close_time` and cannot be checked offline.
- **The 81-tp-vs-R3 framing was itself over-corrected — see §15 for the investigated truth.** (The claim here that "R3 is adopted into ADR-0009, so the 81 violate the authority / one of {rule,labels} is defective" is WRONG: R3 is a *non-authoritative proposal* in `fetch_outcomes.py`, and ADR-0009 explicitly *refuses* a hedge label. §13's original "governance question, not a proven mislabel" was the more accurate framing. The real relationship is a proposal-vs-ratified **lane mismatch**, not a defect — details, evidence, and the recommended HYBRID resolution in §15.)
- **Contamination propagation.** The 44-cohort's two frozen mislabels (`9934a6e1`, `9a357683`) feed the 110-cohort via the "existing labels win" union and would propagate into any future derived cohort unless explicitly overridden. "Immutable v1.1 snapshot" (§10/§12) is not a clean virtue — it permanently embeds two known-wrong tp. The fix plan must decide whether to correct the 44-cohort too, not just leave it.
- **JSON-vs-DB divergence.** DB re-ratification is operator-gated (§12 #4); until it happens, `alert_reviews` continues serving `9934a6e1` + `9a357683` as tp to any DB-reading consumer even after the JSON cohorts are corrected. This divergence must be flagged wherever the DB labels are consumed.
- **"Two BREACHes" is really three rules.** `momentum_v1` shares the identical `cross_market_hedge` root cause (3 live fps) but was scoped out of the governance narrative + grouped view. §2.1/§4 now note it; §5 item 4/BREACH-disposition must include it.
- **MEXKOR max-gap reconciled (was a doc contradiction).** §2.3's "817s" and §13's "1676s" describe DIFFERENT gaps of the same event: MEXKOR's 47 alerts split into **two** 900s components (10-alert + 37-alert) separated by the **1676s** gap; the 817s is the max gap *within* the 37-alert component (internally dense). So MEXKOR both fragments into 2 groups (1676s) AND has a dense largest component (817s) — no contradiction, but the record now states it explicitly. This confirms "900s binds a whole real multi-market event into one group" is FALSE.
- **`volume_spike` "31.5%" was a denominator-definition question — now RESOLVED (#97): it reproduces** at `28/89` with the floor denominator `this_trade_usd >= 850`. The "51.6%" figure in an earlier draft was the non-reproducing artifact. Severity "low" stands (it is a real, small breach).

**Scoping/specification gaps to close (sonnet scoping lane):**
- §5 roadmap + §12 fix-plan items lack **acceptance criteria, pass/fail gates, owners (by role), and sequencing/gating cross-references** — despite §6 defining the delegation model. Each item should name its owner (operator / Claude / Codex / Opus) and its done-criteria. §13's path-to-certain and §5's roadmap and §12's fix-plan describe overlapping work and should be unified with explicit gating (e.g. §13 #2 [81-tp] gates the BREACH-disposition and R3-emit decisions).
- **§5 item 3's "HARD leg-aware zero-tp-loss offline gate" is undefined and dangerously reuses the tautological metric.** Because `removed_tp`/`tp_visible` are Candidate-B invariants (proven), a worker could "satisfy a HARD gate" with an already-invalidated check. Any R3-emit gate MUST be specified as a NON-tautological metric (e.g. a leg-level held-move + settlement re-derivation that can actually fail), not the drop-nothing invariants.

**Recording gaps (sonnet fidelity lane):**
- **[RESOLVED] WORKLOG.md was not updated** for this audit/meta-verify/fresh-pass session at the time of the finding. The top M-COFIRE-AUDIT entry now records the `9a357683` discovery, the RESIDUAL_UNCERTAINTY verdict, the review-packet bug, and the fix plan.
- Minor: the "untracked-files-cited-as-durable" finding (§12) lost agent2's specific file:line anchors (`plans/2026-07-05-...:234`, `WORKLOG.md:7818`) + the 4 named files — restore if acting on it.

**Convergence status:** this pass found one real code bug (review-packet reduction) + material over-assertions, so we had NOT yet reached the floor before it. With these folded in, the remaining open items are enumerated and scoped; further passes are expected to yield diminishing (refinement-only) returns.

---

## 15. The 81-tp vs R3 — investigated (2026-07-06), operator-requested before deciding

A read-only 3-lane Opus investigation (data characterization + rule-authority/semantics → synthesis) resolved the 81-tp question. **It corrected §14's over-statement:** the "81 violate the adopted authority / one of {rule,labels} is defective" framing was wrong.

**Authority (decisive).** R3 (cross_market_hedge → all sweep members incl. the winner = fp) is a **non-authoritative PROPOSAL in `fetch_outcomes.py` only** — the script self-declares "proposes labels," "records NOTHING to the database," output stamped "NOT RECORDED … operator ratification gates any DB write." Nothing in `docs/adr/`, `sql/`, or `config/` encodes an R3 hedge-labeling rule. **ADR-0009 (the only Accepted co-fire artifact) adopts GROUPING at the read layer and explicitly DECLINES a hedge label** ("no group-level review label; reviews stay per-leg; grouped views always expose every leg; drop nothing — dropping a leg risks dropping a true positive"). So R3-labeling ≠ adopted; event-grouping = adopted (read-side, default-OFF).

**The real relationship = a lane/authority mismatch, by design — NOT a defect.** The 81 are ratified `alert_reviews` produced by independent evidence lanes; a conservative auto-proposal disagreeing with evidence-grounded operator labels is exactly the intended proposal-vs-ratification pipeline.

**Data (81 tp, 4 events):** MEXKOR (34), GERCIV (31), NZLEGY (8), KXBTCD (8). *Grain note:* 81 is event-grain; strict 900s `group_cofire` = 71; interpretation-C = 79.
- **Ratification path:** only **15/81 rest on settlement survivorship**; **66/81 rest on fire-time flow/magnitude** evidence (live-window proofs, large-trade magnitude) that the auto-rule R3 never had.
- **Hedge vs directional:** MEXKOR + GERCIV are **genuine all-outcomes hedges** (every outcome-market + both sides covered → exactly one arithmetic winner → survivorship applies; 65 of 81 tp); NZLEGY is a partial hedge (8 tp); **KXBTCD is a one-sided DIRECTIONAL ladder, NOT a hedge** (8 tp all on the `no` side, both strikes settled `no` → correct, not survivorship). R3 would wrongly sweep KXBTCD into hedge-fp.
- **Real distortions (the only truth-problems):** a handful of survivorship-flattered *losing* legs kept `tp` on flow-dominance alone — e.g. `2f7a4f82` (MEXKOR-TIE-yes) and `340c12f4` (MEXKOR-KOR-yes) both settled `no`.
- **R3 is genuinely over-broad + has an ordering bug:** the `elif r.get("hedge_group")` branch (`fetch_outcomes.py:~318`) short-circuits to fp BEFORE the else-branch OT-TP outcome test (`:~322-325`), so a leg that settled in-side AND kept a favorable move is denied its corroboration; it groups on `event_ticker`+900s only, with no same-actor/size/offset test. Its non-flipping safety on the shipped 44-cohort is "a timing accident," not a safety property.
- **JSON-vs-DB divergence:** the reval JSON (proposal) proposes 45 of the 52 overlapping DB-tp as NOT-tp (39 fp/cross_market_hedge + 6 noise) — 87% of the overlap. This is expected proposal-vs-ratified behavior, not a contradiction of truth.

**RECOMMENDATION — HYBRID (highest defensibility; no mass relabel):**
1. **Do NOT mass-relabel** (reject relabel-81) — would overturn ADR-0009, promote a self-declared-non-authoritative script rule to truth, and sweep genuine directional (KXBTCD) + real large trades (e.g. `8e904181` = a $432k trade) into fp on temporal coincidence.
2. **Narrow R3 at the source** (v1.2→v1.3, `fetch_outcomes.py` only, outside `src/`): remove the pre-outcome-test short-circuit so `hedge_group` becomes a *caveat* on an otherwise-evaluated leg; require ≥2 opposite-side legs or size-symmetry before asserting cross_market_hedge. Improves every future proposal; touches zero ratified labels.
3. **Document the override relationship** — the JSON-vs-DB divergence is by design; ADR-0009 vests label authority per-leg. Dissolves the false "contradiction."
4. **Confine actual relabeling to the evidence-warranted set** — the survivorship-flattered *losing* legs that settled against their own side (**25 legs** per #97's worklist: MEXKOR=21, NZLEGY=4; incl. `2f7a4f82`, `340c12f4`), by per-leg operator adjudication, NOT an 81-row sweep. (The "single-digit" estimate in the initial §15 was low — the actual settled-against-side count is 25; see `reports/alert-quality/cofire-losing-leg-adjudication-2026-07-06.md`.)

**Operator decision still required:** (a) DB re-ratification remains held for the two post-close ids; (b) adjudicate per-leg the 25-leg losing subset (keep tp on independent magnitude/live-window evidence, or flip to fp as survivorship artifacts); (c) decide `momentum_v1` scope and whether to flip grouped co-fire on for routine triage. R3 v1.3 narrowing already landed in #97. Everything else — the 66 evidence-grounded tp, the KXBTCD directional, ADR-0009, live emission — stays untouched.

**Practical implications:** the two fp BREACHes are UNCHANGED (no bulk relabel); the reval JSONs remain correctly-read PROPOSALS; `alert_reviews` stays per-`alert_id`; **all ADR-0009 co-fire claims are UPHELD** (queue reduction, removed_tp=0, emission byte-identical, default-OFF). **Residual uncertainties:** same-actor gate may be un-implementable (no actor field in the packet); the survivorship critique's exact scope on the baskets; small cohort (4 events, GERCIV under-sampled in the reval); the 81/71/79 grain sensitivity.

---

## 16. Unified execution table — owners, acceptance, gating (remediates the §14 scoping gap)

The open items are scattered across §5 (roadmap), §12 (fix plan), §13 (path-to-certain), §14 (fresh-pass), §15 (81-tp). §14 flagged that they lacked uniform owners / acceptance / gating; this table supplies them in one dependency-ordered place. Owner legend: **Op**=operator, **Cx**=Codex (impl), **Op-us**=Opus workflow (verify/analyze), **Cl**=Claude (orchestrate/verify/merge).

> **STATUS (2026-07-06, post-merge — origin/main `4ea003e`):** items **#1–#8 LANDED** via **PR #98** (`cbb9198` — relabel `9a357683`→noise/tp=22, regenerate v1.2 reports, flag 44-cohort, review-packet reduction bug fixed [now `185`], cross-path test) and **PR #97** (`4ea003e` — narrow R3 to v1.3, losing-leg worklist [25 legs], BREACH denominators [31.5% reproduced]). Each was independently Opus-verified (cohort diff = only `9a357683`; review-packet fix preserves `--since`/`--limit` safety; R3 v1.3 red-first non-vacuous; reports accurate) before merge. **HELD** (operator gate): #9 (momentum scope), **#10 (DB re-ratification — `alert_reviews` still serves `9934a6e1`+`9a357683` as tp; JSON-vs-DB divergence persists until you ratify)**, #11 (flip `--group-cofire` on — now unblocked, #4 fixed), #12 (R3-emit, far-future).

| # | Item | Owner | Acceptance (pass/fail) | Gated by |
|---|---|---|---|---|
| 1 | Relabel `9a357683` tp→noise in the reval cohort | Cx impl + Op-us verify + Cl merge | reval gate PASS `tp_visible=22/22`, `removed_tp=0`, `missed=0`; ONLY `9a357683` changed (diff = 1 label + why); DB fingerprint unchanged | Op go (direction given: correct JSON) |
| 2 | Decide + apply 44-cohort `outcomes-2026-07-02.json` (embeds both mislabels) | Op decision → Cx | either both post-close tp flagged in-file as v1.2-superseded, or corrected; "existing-labels-win" contamination into derived cohorts addressed | #1 + Op decision |
| 3 | Regenerate/supersede stale v1.2 companion artifacts | Cx | `co-fire-validation-reval*.md`(base+w300+w1800), `cofire-reval-new-labels*.md`, `outcomes-cofire-reval-new*.json`, and the enlarged-JSON top-level `rule` all read v1.2 / tp=22 (or carry a superseded banner) | #1 |
| 4 | Fix `review-packet --group-cofire` zero-reduction bug | Cx impl + Op-us verify | DONE #98: red-first test covered the always-zero behavior; `review_state`'s mandatory default no longer forces `filter_boundary`; live read-only run shows non-zero `non_partial_reduction=185`; flag-off byte-identical | completed |
| 5 | Re-derive BREACH denominators; document the real 31.5% | Op-us/Cl analysis | DONE #97: `volume_spike_v1` current-floor `28/89 = 31.5%`, all-reviewed `77/138 = 55.8%`; `directional_cluster_v1` `12/61 = 19.7%`; `momentum_v1` `4/36 = 11.1%`; earlier `51.6%` does not reproduce | none |
| 6 | Narrow R3 (v1.2→v1.3) in `fetch_outcomes.py` | Cx impl + Op-us verify | DONE #97: `elif r.get("hedge_group")` no longer short-circuits before the OT-TP test (becomes a caveat on an otherwise-evaluated leg); requires ≥2 opposite-side legs or size-symmetry before asserting `cross_market_hedge`; red-first test: a genuine in-side + kept-move co-firing leg is NOT auto-fp'd; docstring v1.3; touches only this report-dir script (no `src/`) | completed |
| 7 | ~~Surface~~ (DONE #97, worklist=25 legs) + adjudicate the survivorship-flattered losing-leg subset | ~~Op-us surfaces~~ + Op adjudicates | worklist in `cofire-losing-leg-adjudication-2026-07-06.md` (25 legs settled against own side: MEXKOR=21, NZLEGY=4, incl `2f7a4f82`,`340c12f4`); Op keeps-or-flips each per-leg, NOT an 81-row sweep | Op (per-leg) |
| 8 | `_cofire_item` event-ticker divergence | Cx | `_cofire_item` prefers `facts.event_ticker` before `derive_event_ticker`, OR a cross-path equivalence test proves the view and labeling paths group identically on all real data | none (low) |
| 9 | `momentum_v1` scope decision | Op decision | `momentum_v1` (3 cross_market_hedge fp) explicitly declared in-scope (governed + view-grouped) or out-of-scope with justification | none |
| 10 | DB re-ratification of `9934a6e1` + `9a357683` in `alert_reviews` | Op (ratification-gated) | `alert_reviews` writes ONLY after explicit operator ratification; documents the JSON-vs-DB divergence until done | Op ratification (Q3 = HOLD) |
| 11 | Flip `alerts list --group-cofire` on for triage | Op | view runs clean (done §14) AND #4 review-packet bug fixed/accepted | #4 |
| 12 | (Far-future, gated) emission-side R3-emit suppression | Op greenlight → Cx + Op-us | see the non-tautological gate below; must NOT reuse `removed_tp`/`tp_visible` | Op greenlight + gate below + R1-harness generalization |

**Dependency notes:** #15 (81-tp) gates the BREACH-disposition (§5 item 2 — do not "fix" the breaches by relabeling co-fire tp) and any R3-emit (#12). #1 gates #2 and #3. #4 gates #11.

**Definition of the "HARD leg-aware zero-tp-loss gate" (for #12 / §5 item 3) — the non-tautological metric §14 demanded.** `removed_tp`/`tp_visible` are unusable (Candidate-B drop-nothing invariants — always pass). A real emission-suppression gate must be able to FAIL because suppression *does* drop legs. Define: over a labeled cohort, **simulate the emission-side suppression** (via the generalized replay harness, R1) and compute **`suppressed_genuine_tp` = the number of alerts the suppression WOULD drop that independently qualify as a genuine tp** — where "genuine tp" is a per-leg re-derivation requiring BOTH (a) `0 ≤ close_time − fired_at ≤ SETTLE_D` AND settled in-side, AND (b) a kept, non-reverting favorable move ≥ $0.10 (i.e. it must clear the *outcome* test, not merely be the arithmetic winner of a hedge basket). **Gate = `suppressed_genuine_tp == 0`** on the largest available labeled cohort, adversarially + Opus-verified, with the suppression logic mutation-proven to be able to fail (drop a synthetic genuine-tp leg → gate FAILs). This is the only zero-tp-loss claim that carries information for a suppression design.
