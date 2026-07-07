# Co-Fire Larger-Cohort Revalidation - 2026-07-06

## Verdict

PASS, with corrected interpretation. The enlarged `M-COFIRE-REVAL` cohort confirms Candidate-B structural leg retention in the larger MEXKOR/NZLEGY/KXBTCD cohort: every leg remains present by construction, so leg-aware review can inspect each original alert. It does not prove a suppression-safe semantic no-loss guarantee; `removed_tp=0` and `tp_visible=22/22` are structural invariants of drop-nothing grouping, not discriminating gates.

- Structural invariants / regression guards: `removed_tp=0`, `tp_visible=22/22`
- Discriminating window-sensitive gate: `missed_labeled_hedge_fp=0`
- Substantive diagnostic: `parent_label_conflicts=7`, so mixed-label groups exist and leg-aware review is mandatory
- Hardcoded GERCIV pair `39bd1f35 <-> 623164c5` remains grouped at `window_s=900`

Production emission paths and `src/pmfi/pipeline/cofire.py` were not modified.

## Method

Operator authorization covered public read-only settlement fetches only. No credentials were used and no DB writes were performed.

1. Reconfirmed Kalshi pairwise co-fire event families from the live local DB with the exact-three-segment event-key rule.
2. Extracted the three new target families into `cofire-reval-packet-2026-07-06.json`: MEXKOR, KXBTCD-26JUN1817, and NZLEGY.
3. Ran `fetch_outcomes.py` with additive `--packet`, `--out`, and `--out-md` options. `LABELING_RULE v1.2` now adds the settlement-TP temporal guard.
4. Built `outcomes-cofire-reval-2026-07-06.json` as `outcomes-2026-07-02.json` union of the newly labeled target alerts, deduped by `short_id` with existing labels winning.
5. Ran `validate_cofire.py` on the enlarged cohort at `window_s=900`, plus sensitivity checks at `300` and `1800`.

## Cohort

| Source | Count |
| --- | ---: |
| existing 44-cohort | 44 |
| live-labeled target alerts | 75 |
| target overlaps already in 44-cohort | 9 |
| label conflicts on overlap | 0 |
| enlarged cohort total | 110 |

Enlarged label distribution after the v1.2 temporal corrections: `fp=64`, `tp=22`, `noise=24`.

## Target Event Results

| event_ticker | alerts | legs | labels | TP finding |
| --- | ---: | ---: | --- | --- |
| `KXWCGAME-26JUN18MEXKOR` | 47 | 3 | `fp=44`, `noise=3` | No TP legs. |
| `KXBTCD-26JUN1817` | 17 | 2 | `tp=8`, `noise=3`, `fp=6` | 8 provisional TP legs, all leg-visible after grouping. |
| `KXWCGAME-26JUN21NZLEGY` | 11 | 2 | `fp=7`, `noise=4` | No TP legs. |

BTCD has same-market two-sided co-fires and mixed TP/noise/FP groups, which reinforces the leg-visibility requirement. The 8 remaining BTCD TP labels are provisional rather than a clean block of informed-flow TP: all are side=`no` on a BTC daily strike ladder that settled `no`, and several are settlement-only or short-horizon. `9934a6e1` fired after market close and is re-labeled noise under `LABELING_RULE v1.2`. The candle-corroborated subset (`2f74584e`, `ecb9bfbc`, `f5f72655`, `c3ac573e`, `ee9c4b24`) is the stronger evidence. Candidate-B grouping remains reviewable because every leg is retained and visible, but this does not validate parent-level trust.

## Per-Market Live Fetch Results

| market | event | alerts | labels | status | result | candles | fetch |
| --- | --- | ---: | --- | --- | --- | ---: | --- |
| `KXBTCD-26JUN1817-T63249.99` | `KXBTCD-26JUN1817` | 9 | `tp=4`, `noise=2`, `fp=3` | finalized | no | 0 | ok |
| `KXBTCD-26JUN1817-T63749.99` | `KXBTCD-26JUN1817` | 8 | `tp=4`, `noise=1`, `fp=3` | finalized | no | 6 | ok |
| `KXWCGAME-26JUN18MEXKOR-KOR` | `KXWCGAME-26JUN18MEXKOR` | 17 | `fp=16`, `noise=1` | finalized | no | 2 | ok |
| `KXWCGAME-26JUN18MEXKOR-MEX` | `KXWCGAME-26JUN18MEXKOR` | 19 | `fp=17`, `noise=2` | finalized | yes | 2 | ok |
| `KXWCGAME-26JUN18MEXKOR-TIE` | `KXWCGAME-26JUN18MEXKOR` | 11 | `fp=11` | finalized | no | 2 | ok |
| `KXWCGAME-26JUN21NZLEGY-NZL` | `KXWCGAME-26JUN21NZLEGY` | 6 | `fp=4`, `noise=2` | finalized | no | 3 | ok |
| `KXWCGAME-26JUN21NZLEGY-TIE` | `KXWCGAME-26JUN21NZLEGY` | 5 | `fp=3`, `noise=2` | finalized | no | 3 | ok |

No target market was unfetchable. BTCD `T63249.99` had no candle rows in the fetched window, but settlement facts were available and labelable.

## Gate Output

`window_s=900`:

```text
PASS: removed_tp=0 tp_visible=22/22 missed_labeled_hedge_fp=0 fp_group_reduction=56 queue_reduction=81
```

Hard gates from `co-fire-validation-reval-2026-07-06.md`:

| Gate | Value | Status | Interpretation |
| --- | --- | --- | --- |
| `derived_event_ticker_mismatches` | 0 | PASS | Input/key sanity check. |
| `removed_tp` | 0 | PASS | Candidate-B structural invariant / regression guard, not semantic proof. |
| `tp_leg_visible_after` | 22/22 | PASS | Candidate-B structural invariant / regression guard, not semantic proof. |
| `missed_labeled_hedge_fp` | 0 | PASS | Discriminating, window-sensitive grouping gate. |
| `39bd1f35 <-> 623164c5 grouped` | True | PASS | GERCIV anchor pair grouped at the chosen window. |

Diagnostic: `parent_label_conflicts=7` is the substantive review signal. Mixed-label groups exist, so any display that collapses a group to one parent label would hide TP under FP/noise context; review must remain per-leg.

Sensitivity:

| window_s | result | note |
| ---: | --- | --- |
| 300 | FAIL | Fails the known GERCIV `39bd1f35 <-> 623164c5` hard check; this confirms the smaller window is insufficient. |
| 900 | PASS | Chosen operating window. |
| 1800 | PASS | Regenerated under the corrected v1.2 reval cohort; label row is `fp=64, noise=24, tp=22`. |

## Leg Visibility

At `window_s=900`, `parent_label_conflicts=7` on the enlarged cohort. Three of those conflicts are BTCD groups containing TP legs:

- `ecb9bfbc` TP and `8481251b` noise on `KXBTCD-26JUN1817-T63749.99`
- `2f74584e` TP and `6cd522fc` noise on `KXBTCD-26JUN1817-T63249.99`
- `be9ce230`, `954bad61`, and `a6fb7bd0` TP with `504e373a` FP on `KXBTCD-26JUN1817-T63749.99`

These are not Candidate-B failures because the grouped representation retains every leg with its own label/category. They are evidence against any future parent-label-only display or suppress-to-one-leg design.

## Known Limits Surfaced By This Re-Validation

### Strike-Ladder Hedge Gap

`LABELING_RULE v1.2` R3 requires at least two distinct market legs with the same `event_ticker` within 15 minutes. It does not model a Kalshi strike ladder as a correlated hedge/survivorship family, even when adjacent OTM bands share one underlying, such as KXBTCD `T63249.99` and `T63749.99`. Outcome-test TP on these ladders should therefore be treated as provisional and reviewed leg-by-leg.

### Settlement-TP Temporal Gap - Fixed In LABELING_RULE v1.2

`fetch_outcomes.py` computes settlement TP with the v1.2 temporal guard: `0 <= close_time - fired_at <= SETTLE_D`. A fire after `close_time` no longer qualifies through settlement. The concrete fixed instances in this cohort are `9934a6e1` and `9a357683`; both are re-labeled noise in the current reval JSON.

## DB Fingerprint

Primary DB counts stayed unchanged before and after the live read-only check:

```text
alerts=318
alert_reviews=301
raw_events=661380
```
