# Co-Fire Larger-Cohort Revalidation - 2026-07-06

## Verdict

PASS. The enlarged `M-COFIRE-REVAL` cohort preserves the zero-TP-loss guarantee at the larger MEXKOR/NZLEGY/KXBTCD scale:

- `removed_tp=0`
- `tp_visible=24/24`
- `missed_labeled_hedge_fp=0`
- hardcoded GERCIV pair `39bd1f35 <-> 623164c5` remains grouped at `window_s=900`
- no MEXKOR-class group hides a TP leg

Production emission paths and `src/pmfi/pipeline/cofire.py` were not modified.

## Method

Operator authorization covered public read-only settlement fetches only. No credentials were used and no DB writes were performed.

1. Reconfirmed Kalshi pairwise co-fire event families from the live local DB with the exact-three-segment event-key rule.
2. Extracted the three new target families into `cofire-reval-packet-2026-07-06.json`: MEXKOR, KXBTCD-26JUN1817, and NZLEGY.
3. Ran `fetch_outcomes.py` with additive `--packet`, `--out`, and `--out-md` options. `LABELING_RULE v1.1` was unchanged.
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

Enlarged label distribution: `fp=64`, `tp=24`, `noise=22`.

## Target Event Results

| event_ticker | alerts | legs | labels | TP finding |
| --- | ---: | ---: | --- | --- |
| `KXWCGAME-26JUN18MEXKOR` | 47 | 3 | `fp=44`, `noise=3` | No TP legs. |
| `KXBTCD-26JUN1817` | 17 | 2 | `tp=9`, `noise=2`, `fp=6` | 9 TP legs, all leg-visible after grouping. |
| `KXWCGAME-26JUN21NZLEGY` | 11 | 2 | `fp=7`, `noise=4` | No TP legs. |

BTCD has same-market two-sided co-fires and mixed TP/noise/FP groups, which reinforces the leg-visibility requirement. Candidate-B grouping remains safe because every leg is retained and visible.

## Per-Market Live Fetch Results

| market | event | alerts | labels | status | result | candles | fetch |
| --- | --- | ---: | --- | --- | --- | ---: | --- |
| `KXBTCD-26JUN1817-T63249.99` | `KXBTCD-26JUN1817` | 9 | `tp=5`, `noise=1`, `fp=3` | finalized | no | 0 | ok |
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
PASS: removed_tp=0 tp_visible=24/24 missed_labeled_hedge_fp=0 fp_group_reduction=56 queue_reduction=81
```

Hard gates from `co-fire-validation-reval-2026-07-06.md`:

| Gate | Value | Status |
| --- | --- | --- |
| `derived_event_ticker_mismatches` | 0 | PASS |
| `removed_tp` | 0 | PASS |
| `tp_leg_visible_after` | 24/24 | PASS |
| `missed_labeled_hedge_fp` | 0 | PASS |
| `39bd1f35 <-> 623164c5 grouped` | True | PASS |

Sensitivity:

| window_s | result | note |
| ---: | --- | --- |
| 300 | FAIL | Fails the known GERCIV `39bd1f35 <-> 623164c5` hard check; this confirms the smaller window is insufficient. |
| 900 | PASS | Chosen operating window. |
| 1800 | PASS | `removed_tp=0`, `tp_visible=24/24`, `missed_labeled_hedge_fp=0`, `queue_reduction=85`. |

## Leg Visibility

At `window_s=900`, `parent_label_conflicts=7` on the enlarged cohort. Three of those conflicts are BTCD groups containing TP legs:

- `ecb9bfbc` TP and `8481251b` noise on `KXBTCD-26JUN1817-T63749.99`
- `2f74584e` TP and `6cd522fc` noise on `KXBTCD-26JUN1817-T63249.99`
- `be9ce230`, `954bad61`, and `a6fb7bd0` TP with `504e373a` FP on `KXBTCD-26JUN1817-T63749.99`

These are not Candidate-B failures because the grouped representation retains every leg with its own label/category. They are evidence against any future parent-label-only display or suppress-to-one-leg design.

## DB Fingerprint

Primary DB counts stayed unchanged before and after the live read-only check:

```text
alerts=318
alert_reviews=301
raw_events=661380
```
