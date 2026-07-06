# Co-Fire Validation - 2026-07-06

Verdict: **PASS** for offline Candidate-B co-fire grouping.

Live emission remains out of scope: this branch adds a standalone primitive plus this harness/report only; it does not wire `cofire` into runner, engine, rules, scoring, SQL, or CLI paths.

## Inputs

| Field | Value |
| --- | --- |
| cohort | C:\Users\benny\OneDrive\Desktop\PM-intel\reports\alert-quality\outcomes-2026-07-02.json |
| window_s | 900 |
| alerts | 44 |
| labels | fp=13, noise=15, tp=16 |
| categories | cross_market_hedge=12, low_price_lottery=1, none=31 |

## Hard Gates

| Gate | Value | Status |
| --- | --- | --- |
| derived_event_ticker_mismatches | 0 | PASS |
| removed_tp | 0 | PASS |
| tp_leg_visible_after | 16/16 | PASS |
| missed_labeled_hedge_fp | 0 | PASS |
| 39bd1f35 <-> 623164c5 grouped | True | PASS |

## Reduction Metrics

| Metric | Before | After | Reduction |
| --- | --- | --- | --- |
| queue items | 44 | 22 | 22 |
| fp operator items | 13 | 5 | 8 |
| cross_market_hedge fp groups | 12 | 4 | 8 |

## Leg Visibility Diagnostics

Candidate B drops no legs. The conflicts below show where any single parent label or majority-label suppression would be unsafe.

| Metric | Value |
| --- | --- |
| parent_label_conflicts | 4 |
| cross_market_hedge fp in multi-leg groups | 12 |
| cross_market_hedge fp singleton legs | none |

## Parent Label Conflicts

| Group | Event | Legs | Labels | Members |
| --- | --- | --- | --- | --- |
| 8 | KXBTC15M-26JUN201630 | 2 | noise, tp | 8d802094:noise:none:KXBTC15M-26JUN201630-30; 52648fe1:tp:none:KXBTC15M-26JUN201630-30 |
| 9 | KXWC1HTOTAL-26JUN20GERCIV | 4 | noise, tp | 2a2c4cfd:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; 034a26f6:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; ee4b177c:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; 34b0e9ce:noise:none:KXWC1HTOTAL-26JUN20GERCIV-1 |
| 10 | KXWCGAME-26JUN20GERCIV | 4 | noise, tp | 08097c1f:noise:none:KXWCGAME-26JUN20GERCIV-GER; 884b1f78:noise:none:KXWCGAME-26JUN20GERCIV-GER; 92c182df:tp:none:KXWCGAME-26JUN20GERCIV-GER; d1e48c62:tp:none:KXWCGAME-26JUN20GERCIV-GER |
| 21 | KXWNBAGAME-26JUN21NYLA | 3 | noise, tp | dafd230b:tp:none:KXWNBAGAME-26JUN21NYLA-LA; 5015d4fc:noise:none:KXWNBAGAME-26JUN21NYLA-LA; 35e1fe3c:tp:none:KXWNBAGAME-26JUN21NYLA-LA |

## Labeled Hedge Pair Coverage

| Pair | Event | Delta s | Grouped |
| --- | --- | --- | --- |
| 21faf380 <-> cf2ccb3b | KXWCGAME-26JUN21NZLEGY | 169.691 | True |
| 3069ce93 <-> fa202af3 | KXWCGAME-26JUN18MEXKOR | 240.704 | True |
| 319a9711 <-> 39bd1f35 | KXWCGAME-26JUN20GERCIV | 656.06 | True |
| 319a9711 <-> 645e3274 | KXWCGAME-26JUN20GERCIV | 131.207 | True |
| 319a9711 <-> a86758a3 | KXWCGAME-26JUN20GERCIV | 131.103 | True |
| 319a9711 <-> fb885a3c | KXWCGAME-26JUN20GERCIV | 89.544 | True |
| 39bd1f35 <-> 623164c5 | KXWCGAME-26JUN20GERCIV | 725.852 | True |
| 39bd1f35 <-> 645e3274 | KXWCGAME-26JUN20GERCIV | 524.852 | True |
| 39bd1f35 <-> 97ee5ccb | KXWCGAME-26JUN20GERCIV | 676.29 | True |
| 39bd1f35 <-> a86758a3 | KXWCGAME-26JUN20GERCIV | 524.957 | True |
| 39bd1f35 <-> bfebfa61 | KXWCGAME-26JUN20GERCIV | 726.976 | True |
| 39bd1f35 <-> fb885a3c | KXWCGAME-26JUN20GERCIV | 566.516 | True |
| 585d54fe <-> 825f4477 | KXWCGAME-26JUN18MEXKOR | 73.914 | True |
| 623164c5 <-> 645e3274 | KXWCGAME-26JUN20GERCIV | 201.0 | True |
| 623164c5 <-> a86758a3 | KXWCGAME-26JUN20GERCIV | 200.895 | True |
| 623164c5 <-> fb885a3c | KXWCGAME-26JUN20GERCIV | 159.336 | True |
| 645e3274 <-> 97ee5ccb | KXWCGAME-26JUN20GERCIV | 151.437 | True |
| 645e3274 <-> bfebfa61 | KXWCGAME-26JUN20GERCIV | 202.124 | True |
| 859af590 <-> cf2ccb3b | KXWCGAME-26JUN21NZLEGY | 130.79 | True |
| 97ee5ccb <-> a86758a3 | KXWCGAME-26JUN20GERCIV | 151.333 | True |
| 97ee5ccb <-> fb885a3c | KXWCGAME-26JUN20GERCIV | 109.774 | True |
| a86758a3 <-> bfebfa61 | KXWCGAME-26JUN20GERCIV | 202.02 | True |
| bfebfa61 <-> fb885a3c | KXWCGAME-26JUN20GERCIV | 160.46 | True |
| eb5374fc <-> fa202af3 | KXWCGAME-26JUN18MEXKOR | 120.529 | True |

## Missed Labeled Hedge Pairs

None.

## Multi-Leg Groups

| Group | Event | Legs | Labels | Categories | Members |
| --- | --- | --- | --- | --- | --- |
| 4 | KXWCGAME-26JUN18MEXKOR | 3 | fp=1, noise=2 | cross_market_hedge=1, none=2 | fa202af3:fp:cross_market_hedge:KXWCGAME-26JUN18MEXKOR-KOR; eb5374fc:noise:none:KXWCGAME-26JUN18MEXKOR-MEX; 3069ce93:noise:none:KXWCGAME-26JUN18MEXKOR-MEX |
| 7 | KXWCGAME-26JUN18MEXKOR | 2 | fp=2 | cross_market_hedge=2 | 585d54fe:fp:cross_market_hedge:KXWCGAME-26JUN18MEXKOR-TIE; 825f4477:fp:cross_market_hedge:KXWCGAME-26JUN18MEXKOR-MEX |
| 8 | KXBTC15M-26JUN201630 | 2 | noise=1, tp=1 | none=2 | 8d802094:noise:none:KXBTC15M-26JUN201630-30; 52648fe1:tp:none:KXBTC15M-26JUN201630-30 |
| 9 | KXWC1HTOTAL-26JUN20GERCIV | 4 | noise=1, tp=3 | none=4 | 2a2c4cfd:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; 034a26f6:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; ee4b177c:tp:none:KXWC1HTOTAL-26JUN20GERCIV-1; 34b0e9ce:noise:none:KXWC1HTOTAL-26JUN20GERCIV-1 |
| 10 | KXWCGAME-26JUN20GERCIV | 4 | noise=2, tp=2 | none=4 | 08097c1f:noise:none:KXWCGAME-26JUN20GERCIV-GER; 884b1f78:noise:none:KXWCGAME-26JUN20GERCIV-GER; 92c182df:tp:none:KXWCGAME-26JUN20GERCIV-GER; d1e48c62:tp:none:KXWCGAME-26JUN20GERCIV-GER |
| 12 | KXWCGAME-26JUN20GERCIV | 8 | fp=6, noise=2 | cross_market_hedge=6, none=2 | bfebfa61:noise:none:KXWCGAME-26JUN20GERCIV-CIV; 623164c5:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-CIV; 97ee5ccb:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-CIV; 319a9711:noise:none:KXWCGAME-26JUN20GERCIV-CIV; fb885a3c:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-TIE; a86758a3:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-TIE; 645e3274:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-TIE; 39bd1f35:fp:cross_market_hedge:KXWCGAME-26JUN20GERCIV-GER |
| 13 | KXBTC15M-26JUN201930 | 2 | noise=2 | none=2 | 94ad2703:noise:none:KXBTC15M-26JUN201930-30; bc8b7ab2:noise:none:KXBTC15M-26JUN201930-30 |
| 19 | KXWCGAME-26JUN21NZLEGY | 3 | fp=3 | cross_market_hedge=3 | 21faf380:fp:cross_market_hedge:KXWCGAME-26JUN21NZLEGY-NZL; 859af590:fp:cross_market_hedge:KXWCGAME-26JUN21NZLEGY-NZL; cf2ccb3b:fp:cross_market_hedge:KXWCGAME-26JUN21NZLEGY-TIE |
| 21 | KXWNBAGAME-26JUN21NYLA | 3 | noise=1, tp=2 | none=3 | dafd230b:tp:none:KXWNBAGAME-26JUN21NYLA-LA; 5015d4fc:noise:none:KXWNBAGAME-26JUN21NYLA-LA; 35e1fe3c:tp:none:KXWNBAGAME-26JUN21NYLA-LA |

## Notes

- The source cohort is read-only; this harness writes only this report.
- No DB, live API, seeding, or artifact generation is used.
- Parent label conflicts are not Candidate-B failures because every leg remains visible with its own label and category.
