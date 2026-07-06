# Co-fire semantics measurement (M-ALERT-COFIRE-0) — 2026-07-06

> **Read-only.** Measures event-key granularity + co-fire window against the 44 labeled alerts (`outcomes-2026-07-02.json`), to settle the two open questions agent2 raised before any emission/harness code. No engine change, no DB write, no live API. Settles inputs for `plans/2026-07-05-cofire-suppression-spec.md`.
>
> **Two independent passes agree (cross-verified).** Pass A (`scratchpad/r0_cofire.py`) + Pass B (`scratchpad/measure_cofire_keys.py`, independent). Both: window 900 s, Polymarket = 1 no-op, singleton-tp `36cdc737` preserved. Pass B added the decisive cross-check that **corrected an early lean toward match_key** — see Recommendation.

## Q1 — Key granularity (event_ticker vs match_key)

Kalshi ticker grammar in this cohort is uniform: **43/43 Kalshi tickers are exactly 3 dash-segments** `[TYPE, EVENT, OUTCOME]`. So:
- `event_ticker_key = venue_market_id.rsplit("-",1)[0]` (drops OUTCOME).
- `match_key = segments[1]` (the EVENT token).

| ticker | event_ticker_key | match_key |
|---|---|---|
| `KXWCGAME-26JUN20GERCIV-GER` | `KXWCGAME-26JUN20GERCIV` | `26JUN20GERCIV` |
| `KXWC1HTOTAL-26JUN20GERCIV-1` | `KXWC1HTOTAL-26JUN20GERCIV` | `26JUN20GERCIV` |
| `KXWCCORNERS-26JUN20GERCIV-10` | `KXWCCORNERS-26JUN20GERCIV` | `26JUN20GERCIV` |
| `KXBTC15M-26JUN201630-30` | `KXBTC15M-26JUN201630` | `26JUN201630` |
| `KXMVESPORTS…-S202696F8015AEB8-7EFA…` | `KXMVESPORTS…-S202696F8015AEB8` | `S202696F8015AEB8` |

- **`event_ticker_key` == Kalshi API `facts.event_ticker`, verbatim, 43/43** (Pass B cross-check, zero mismatches). It is not a parsing heuristic — it is the venue's own event id minus the outcome segment.
- **`event_ticker` unifies all 3 labeled hedge clusters with ZERO under-grouping** (`KXWCGAME-26JUN18MEXKOR` n=5, `KXWCGAME-26JUN20GERCIV` n=8, `KXWCGAME-26JUN21NZLEGY` n=3 — each a single unfragmented event_ticker group). It does **not** fragment any real hedge basket that occurs here.
- **`match_key = segments[1]` OVER-groups**: it merges 17 short_ids across **3 distinct `facts.event_ticker`** (`KXWCGAME`/`KXWC1HTOTAL`/`KXWCCORNERS`-`26JUN20GERCIV`) into one key. agent2 read this as "the match basket," **but the labeling rule deliberately excludes cross-market-type hedges** (`fetch_outcomes.py` docstring: "hedge detection is event_ticker-scoped… a hedge spanning related events is not grouped"). So match_key groups *beyond what the labels define as a hedge* — it provides **no** additional unification on the actual labeled clusters, only over-grouping. Its `segments[1]` derivation is also fragile (timestamp-shaped BTC middle tokens, opaque esports hashes) — safe here only by lucky absence of collisions.
- **1 Polymarket alert** (`db27a822`, `low_price_lottery` fp) → co-fire is Kalshi-scoped; Polymarket is a correct no-op (n=1 is too thin to infer any Polymarket-side analog is ever needed).

## Q2 — Window (300 s vs 900 s) × key

| key | window | multi-groups | mixed-label groups | **labeled hedge fps MISSED** | tp legs pooled |
|---|---|---|---|---|---|
| event_ticker | 300 s | 9 | 4 | **1** (`39bd1f35`) | 8 |
| event_ticker | **900 s** | 9 | 4 | **0** | 8 |
| match_key | 300 s | 8 | 3 | **1** (`39bd1f35`) | 8 |
| match_key | **900 s** | 8 | 3 | **0** | 8 |

Column notes: "mixed-label groups" here = groups containing a tp + any non-tp (Pass A); **tp+fp specifically = 0** under all combos (Pass B) — see Q3. "MISSED" = labeled hedge *alerts* left orphaned (Pass A: 1 = `39bd1f35`); by labeled hedge *pairs*, Pass B counts **7 of 33 missed at 300 s, 0 at 900 s**. Both metrics point the same way.

- **Window = 900 s is decisive.** The `39bd1f35`↔`623164c5` pair (both `fp`/`cross_market_hedge`) fired **726 s apart** → missed at 300 s, captured at 900 s. The existing **300 s single-leg suppression window is too short for co-fire**; the co-fire tier needs its own ~900 s window (aligns with `GROUP_MIN=15`) in a **separate cache/key namespace** (agent2 #2), not the 300 s dict.
- **match_key's fewer groups (8 vs 9) is the over-grouping DEFECT, not a virtue** — it folds `KXWC1HTOTAL`/`KXWCCORNERS`-`GERCIV` into the `KXWCGAME`-`GERCIV` cluster across 3 distinct `facts.event_ticker`, which the labeling rule excludes by design. Reject match_key (see Recommendation).

## Q3 — Hidden-tp risk (leg-aware visibility mandatory)

Precise counts (Pass B): **tp+fp groups = 0** under all 4 combos — no group puts a tp and an fp together in this cohort. Groups *do* mix tp+noise (e.g. `KXWC1HTOTAL-26JUN20GERCIV`: `034a26f6`/`2a2c4cfd`/`ee4b177c` tp + `34b0e9ce` noise). So my first pass's "3–4 mixed" was tp+*noise*, not tp+fp — corrected.

**But the hidden-tp risk is real, not eliminated.** One event ticker, `KXWCGAME-26JUN20GERCIV`, contains **both** tp legs (`92c182df`, `d1e48c62`, fired ~20:17) **and** fp hedge legs (`39bd1f35`, `623164c5`, `645e3274`, `97ee5ccb`, `a86758a3`, `fb885a3c`, fired 20:35–20:48). They avoid landing in one group here **only because they are >900 s apart in time** — a timing coincidence of this sample, not a safety property. A radius-based grouper (see below), or a denser future event, could pull them together.

⇒ **Any suppression/grouping must NOT collapse an event-scoped group under a parent/majority verdict** — `92c182df`/`d1e48c62` are real tp that must stay independently visible. Zero-tp-loss must assert `tp_leg_visible_after` + `parent_label_conflicts==0`, not merely non-deletion (confirms agent2 #2/#5). Singleton-tp control `36cdc737` stays ungrouped under all combos ✓.

### Clustering-method finding (both passes missed initially; Pass B surfaced)
The ground-truth hedge grouping (`fetch_outcomes.py:244-249`) uses **pairwise radius** — any two same-event alerts within 15 min are grouped. Both our Q2 sweeps used **consecutive-gap chaining** (new group when the gap to the *previous* alert exceeds the window), which under-counts vs radius (it's why `39bd1f35`, 656–726 s after its neighbors, broke off at 300 s). **R3 must use radius-based grouping to match the labeled ground truth**, and the leg-visibility guarantee matters more under radius (it pools more).

## Recommendation (settles the spec's open inputs)

1. **Window = 900 s** (not 300 s) — both passes; 300 s misses 7 of 33 labeled hedge pairs. Use a **separate co-fire namespace**; do not touch the existing 300 s single-leg suppression.
2. **Key = `event_ticker` — the CLEAR choice** (revised; corrects an early lean toward match_key). It == Kalshi `facts.event_ticker` 43/43, unifies every labeled hedge cluster with zero under- and zero over-grouping. **`match_key` = REJECT** for the current labeled definition: it over-groups cross-market-type markets the labeling rule deliberately excludes, provides no unification benefit on the real clusters, and has a fragile `segments[1]` derivation.
3. **The cross-market-type "match basket" (agent2's original concern) is an UNVALIDATED HYPOTHESIS, not a key choice.** The labels are event_ticker-scoped by design; whether a hedger really baskets `KXWCGAME`+`KXWC1HTOTAL`+`KXWCCORNERS` cannot be answered from these labels. To pursue it, **extend the labeling rule + generate new labels first** — do not swap the key on the existing cohort.
4. **Clustering = pairwise-radius** (any two same-event alerts within the window), matching the ground truth (`fetch_outcomes.py:244-249`) — **NOT** consecutive-gap chaining. This is a required R3 design constant, and it makes leg-aware visibility more important (radius pools more).
5. **Leg-aware visibility mandatory** (Q3): governance/export must count per-leg labels under any grouping; no parent/majority suppression.
6. **Derivation:** `event_ticker = venue_market_id.rsplit("-",1)[0]` (safe; == `facts.event_ticker`). Lock it with a table test; no `match_key` `segments[1]` in production.

**Limitation:** n=44 alerts / 3 hedge clusters / 33 labeled pairs. The `match_key` over-grouping defect and the 300 s under-capture are each driven by the single `KXWCGAME-26JUN20GERCIV` event → re-validate the constants on a larger post-2026-07-02 cohort before finalizing.

## Net for the roadmap
M-COFIRE-0 is **complete + cross-verified** (this report). Settled: window **900 s**; key **event_ticker** (match_key rejected); clustering **pairwise-radius**; **leg-aware visibility mandatory**; cross-market-type basket = separate label-rule-extension question. R1 (harness generalization) can build around these knowns. R3 remains GATED on operator greenlight + the hardened acceptance gate.
