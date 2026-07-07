"""Outcome-based label proposal for the M-TRUTH re-baseline cohort (2026-07-02).

Reproducible, read-only vs the repo/DB: reads the canonical review packet, fetches
post-alert market outcomes from public venue APIs (Kalshi trade-api v2, Polymarket
gamma), and proposes tp/fp/noise labels under LABELING_RULE v1.3 (below). Writes two
artifacts next to this script; records NOTHING to the database.

Live-fetch note: this is an operator-authorized, read-only, opt-in live check
(AGENTS.md local-secret/live-check clause). Not part of tests or verify.py.

LABELING_RULE v1.3 (operator-ratified before any label is recorded):
  Side S = evidence dominant_side / directional_side / outcome_key (first present).
  Static overrides (checked in order, short-circuit):
    R0 fp/directional_outcome_mismatch: stored outcome_key contradicts evidence side.
    R1 fp/low_price_lottery: large_trade_absolute_v1 with capital_at_risk <= $2,500
       (10% of the $25k floor) and trade price <= $0.02.
    R2 noise (immature baseline): market_relative_large_trade_v1 whose baseline
       status/state contains missing/pending/sparse.
    R3 fp/cross_market_hedge (v1.3): hedge_group is a caveat on an otherwise
       outcome-evaluated leg, not a pre-outcome short-circuit. Assert
       cross_market_hedge only for non-OT-TP legs with stronger hedge evidence:
       >=2 opposite-side distinct market legs of the SAME Kalshi event_ticker
       within 15 minutes, OR an opposite-side distinct market leg with size
       symmetry. Same-market-only co-fires and one-sided directional co-fires are
       NOT hedges; they fall through to the outcome test with caveat notes.
  Outcome test (Kalshi, side-adjusted yes-price in dollars; favorable = up for S=yes,
  down for S=no; window = fire_ts .. min(fire_ts+72h, market close)):
    OT-TP  -> tp: market settled result == S (close within 7d of fire), OR max
              favorable move >= $0.10 within the window AND end-of-window price keeps
              >= $0.05 of it (did not fully revert).
              Settlement-TP requires 0 <= close_time - fired_at <= SETTLE_D; a
              fire after close is NOT settlement-tp (conservative noise).
    OT-N   -> noise: everything else (flat, adverse, reverted, settled against S), and
              the conservative default when the market/history is unfetchable or the
              market is unresolved with no usable history (guide section 4 step 3).
  volume_spike_v1 with thin-baseline signature (triage low_notional/thin_baseline or
  baseline_median_usd < $50) gets note low_notional_thin_baseline when noise.
  Caveat flag (label unchanged): tp earned ONLY via settlement (no kept-move
  corroboration) on a market that closed < 6h after fire is annotated
  short_horizon_settlement_only_tp (in-play/public-news reaction can masquerade as
  informed flow there; base-rate ~coin-flip on ultra-short binaries).
Known limits (v1.3): hedge detection is event_ticker-scoped, so a hedge spanning
related events (e.g. KXWCGAME vs KXWC1HTOTAL on the same match) is not grouped;
the committed review-packet fields have no same-actor/account identifier, so
size symmetry is only a magnitude heuristic; strike-ladder/survivorship families
are not deterministically separated; in-play public_news_reaction cannot be
separated from informed flow deterministically.
Constants (ratification knobs): HORIZON_H=72, MOVE=0.10, KEEP=0.05, SETTLE_D=7,
GROUP_MIN=15, SHORT_H=6, SIZE_SYM_TOL=0.20.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKET = HERE.parent / "review-packets" / "m-truth-rebaseline-unreviewed-2026-06-25.json"
OUT_JSON = HERE / "outcomes-2026-07-02.json"
OUT_MD = HERE / "m-truth-autolabel-proposal-2026-07-02.md"

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"

HORIZON_H = 72
MOVE = 0.10
KEEP = 0.05
SETTLE_D = 7
GROUP_MIN = 15
SHORT_H = 6
SIZE_SYM_TOL = 0.20
SIZE_FIELDS = (
    "net_capital_usd",
    "capital_at_risk_usd",
    "this_trade_usd",
    "payout_notional_usd",
    "contracts",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Propose outcome-based labels for a review packet."
    )
    parser.add_argument("--packet", type=Path, default=PACKET)
    parser.add_argument("--out", type=Path, default=OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=OUT_MD)
    parser.add_argument("--generated", default="2026-07-02")
    parser.add_argument("--expected-real-count", type=int, default=None)
    return parser


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "pmfi-local-outcome-check"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def is_synthetic(a: dict) -> bool:
    vid = a.get("venue_market_id", "")
    if vid.startswith("pm-") or "EXAMPLE" in vid.upper():
        return True
    if a.get("venue_code") == "polymarket" and not re.fullmatch(r"0x[0-9a-fA-F]{64}", vid):
        return True
    return False


def side_of(a: dict) -> str | None:
    ev = a.get("evidence", {})
    for k in ("dominant_side", "directional_side"):
        s = ev.get(k)
        if s in ("yes", "no"):
            return s
    s = a.get("outcome_key")
    return s if s in ("yes", "no") else None


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


_cache: dict[str, dict] = {}


def kalshi_market(ticker: str) -> dict | None:
    key = f"m:{ticker}"
    if key not in _cache:
        try:
            _cache[key] = http_json(f"{KALSHI}/markets/{ticker}")["market"]
        except Exception as e:  # noqa: BLE001 - recorded as unfetchable, conservative label path
            _cache[key] = {"_error": str(e)}
        time.sleep(0.15)
    m = _cache[key]
    return None if "_error" in m else m


def kalshi_series(event_ticker: str) -> str | None:
    key = f"e:{event_ticker}"
    if key not in _cache:
        try:
            _cache[key] = {"series": http_json(f"{KALSHI}/events/{event_ticker}")["event"]["series_ticker"]}
        except Exception as e:  # noqa: BLE001
            _cache[key] = {"_error": str(e)}
        time.sleep(0.15)
    v = _cache[key]
    return v.get("series")


def kalshi_candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    key = f"c:{ticker}:{start}:{end}"
    if key not in _cache:
        try:
            u = f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks?start_ts={start}&end_ts={end}&period_interval=60"
            _cache[key] = {"candles": http_json(u).get("candlesticks", [])}
        except Exception as e:  # noqa: BLE001
            _cache[key] = {"_error": str(e), "candles": []}
        time.sleep(0.15)
    return _cache[key]["candles"]


def analyze_kalshi(a: dict) -> dict:
    t = a["venue_market_id"]
    fired = parse_ts(a["fired_at"])
    m = kalshi_market(t)
    out: dict = {"venue": "kalshi", "ticker": t}
    if m is None:
        out["fetch"] = "market_unfetchable"
        return out
    out["status"] = m.get("status")
    out["result"] = m.get("result") or None
    out["close_time"] = m.get("close_time")
    close = parse_ts(m["close_time"]) if m.get("close_time") else None
    out["event_ticker"] = m.get("event_ticker")
    td = close - fired if close else None
    out["settled_within_7d"] = bool(
        out["result"] and td is not None and timedelta(0) <= td <= timedelta(days=SETTLE_D)
    )
    series = kalshi_series(m["event_ticker"]) if m.get("event_ticker") else None
    end_dt = fired + timedelta(hours=HORIZON_H)
    if close and close < end_dt:
        end_dt = close
    candles = []
    if series:
        candles = kalshi_candles(series, t, int((fired - timedelta(hours=1)).timestamp()), int(end_dt.timestamp()))
    out["candles"] = len(candles)
    if candles:
        def px(c, k):
            return float(c["price"][k]) if c.get("price", {}).get(k) is not None else None

        pre = [c for c in candles if c["end_period_ts"] <= fired.timestamp()]
        post = [c for c in candles if c["end_period_ts"] > fired.timestamp()]
        p0 = px(pre[-1], "close_dollars") if pre else (px(post[0], "open_dollars") if post else None)
        out["price_at_fire"] = p0
        if post and p0 is not None:
            highs = [px(c, "high_dollars") for c in post if px(c, "high_dollars") is not None]
            lows = [px(c, "low_dollars") for c in post if px(c, "low_dollars") is not None]
            pend = px(post[-1], "close_dollars")
            out["post_high"] = max(highs) if highs else None
            out["post_low"] = min(lows) if lows else None
            out["price_end_window"] = pend
    return out


def analyze_poly(a: dict) -> dict:
    cid = a["venue_market_id"]
    out: dict = {"venue": "polymarket", "condition_id": cid}
    try:
        ms = http_json(f"{GAMMA}/markets?condition_ids={cid}")
        if ms:
            m = ms[0]
            out["question"] = m.get("question")
            out["closed"] = m.get("closed")
            out["outcome_prices"] = m.get("outcomePrices")
    except Exception as e:  # noqa: BLE001
        out["fetch"] = f"gamma_unfetchable: {e}"
    return out


def favorable(out: dict, side: str) -> dict:
    """Side-adjusted move facts from candle window facts."""
    p0 = out.get("price_at_fire")
    if p0 is None:
        return {}
    hi, lo, pend = out.get("post_high"), out.get("post_low"), out.get("price_end_window")
    if hi is None or lo is None or pend is None:
        return {}
    if side == "yes":
        fav_max, fav_end = hi - p0, pend - p0
    else:
        fav_max, fav_end = p0 - lo, p0 - pend
    return {"fav_max_move": round(fav_max, 4), "fav_end_move": round(fav_end, 4)}


def _size_value(row: dict) -> tuple[str, float] | None:
    ev = row.get("evidence", {})
    for key in SIZE_FIELDS:
        raw = ev.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return key, value
    return None


def _size_symmetric_peers(row: dict, peers: list[dict]) -> list[str]:
    base = _size_value(row)
    if base is None:
        return []
    base_key, base_value = base
    matched = []
    for peer in peers:
        if peer.get("side") == row.get("side"):
            continue
        peer_size = _size_value(peer)
        if peer_size is None:
            continue
        peer_key, peer_value = peer_size
        if peer_key != base_key:
            continue
        spread = abs(peer_value - base_value) / max(peer_value, base_value)
        if spread <= SIZE_SYM_TOL:
            matched.append(peer["short_id"])
    return sorted(matched)


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    packet_path = args.packet
    out_json = args.out
    out_md = args.out_md
    expected_real_count = args.expected_real_count
    if expected_real_count is None and packet_path == PACKET:
        expected_real_count = 44

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    real = [a for a in packet["alerts"] if not is_synthetic(a)]
    if expected_real_count is not None:
        assert len(real) == expected_real_count, (
            f"expected {expected_real_count} real alerts, got {len(real)}"
        )

    rows = []
    for a in real:
        side = side_of(a)
        facts = analyze_kalshi(a) if a["venue_code"] == "kalshi" else analyze_poly(a)
        facts.update(favorable(facts, side) if side and a["venue_code"] == "kalshi" else {})
        rows.append({
            "short_id": a["short_id"],
            "rule": a["rule_key"],
            "venue": a["venue_code"],
            "market": a["venue_market_id"],
            "title": a.get("title"),
            "fired_at": a["fired_at"],
            "side": side,
            "outcome_key": a.get("outcome_key"),
            "evidence": a.get("evidence", {}),
            "triage_flags": a.get("triage_flags", []),
            "facts": facts,
        })

    # v1.3 grouping: candidate co-fire = >=2 DISTINCT market legs of one event
    # within GROUP_MIN minutes. A cross_market_hedge proposal requires stronger
    # opposite-side or size-symmetry evidence and is applied only after OT-TP fails.
    by_event = defaultdict(list)
    for r in rows:
        ev = r["facts"].get("event_ticker")
        if ev:
            by_event[ev].append(r)
    for ev, members in by_event.items():
        if len(members) < 2:
            continue
        for r in members:
            near = [
                o for o in members
                if o is not r
                and abs((parse_ts(o["fired_at"]) - parse_ts(r["fired_at"])).total_seconds()) <= GROUP_MIN * 60
            ]
            other_legs = [o for o in near if o["market"] != r["market"]]
            if other_legs:
                opposite_legs = [o for o in other_legs if o["side"] != r["side"]]
                size_symmetric_with = _size_symmetric_peers(r, other_legs)
                strong = len(opposite_legs) >= 2 or bool(size_symmetric_with)
                r["hedge_group"] = {
                    "event": ev,
                    "with": sorted({o["short_id"] for o in near}),
                    "opposite_side_with": sorted({o["short_id"] for o in opposite_legs}),
                    "size_symmetric_with": size_symmetric_with,
                    "strong": strong,
                }
            elif any(o["side"] != r["side"] for o in near):
                r["two_sided_cofire"] = sorted({o["short_id"] for o in near if o["side"] != r["side"]})

    # Propose labels.
    for r in rows:
        ev, f = r["evidence"], r["facts"]
        label, cat, why = None, None, []
        dom = ev.get("dominant_side") or ev.get("directional_side")
        if dom in ("yes", "no") and r["outcome_key"] in ("yes", "no") and dom != r["outcome_key"]:
            label, cat = "fp", "directional_outcome_mismatch"
            why.append(f"stored outcome_key={r['outcome_key']} contradicts evidence side={dom} (R0)")
        elif r["rule"] == "large_trade_absolute_v1" and float(ev.get("capital_at_risk_usd") or 9e9) <= 2500 \
                and float(ev.get("price") or ev.get("trade_price") or 1) <= 0.02:
            label, cat = "fp", "low_price_lottery"
            why.append("capital_at_risk <= $2,500 at price <= $0.02 vs $25k floor (R1)")
        elif r["rule"] == "market_relative_large_trade_v1" and any(
                s in str(ev.get("baseline_status", "")) + str(ev.get("baseline_state", ""))
                for s in ("missing", "pending", "sparse")):
            label = "noise"
            why.append(f"immature baseline ({ev.get('baseline_status') or ev.get('baseline_state')}) (R2)")
        else:
            settled_in_side = bool(f.get("result")) and f.get("result") == r["side"] and f.get("settled_within_7d")
            fav_max, fav_end = f.get("fav_max_move"), f.get("fav_end_move")
            moved = fav_max is not None and fav_max >= MOVE and (fav_end is not None and fav_end >= KEEP)
            if settled_in_side or moved:
                label = "tp"
                if settled_in_side:
                    why.append(f"settled {f.get('result')} == side within {SETTLE_D}d (OT-TP)")
                if moved:
                    why.append(f"favorable move {fav_max:+.2f} kept {fav_end:+.2f} within {HORIZON_H}h (OT-TP)")
                if settled_in_side and not moved and f.get("close_time"):
                    span_h = (parse_ts(f["close_time"]) - parse_ts(r["fired_at"])).total_seconds() / 3600
                    if span_h < SHORT_H:
                        why.append("short_horizon_settlement_only_tp caveat")
                if r.get("hedge_group"):
                    why.append(
                        "cross_market_hedge_caveat: "
                        f"co-fired legs of {r['hedge_group']['event']} with "
                        f"{r['hedge_group']['with']}; outcome test controls label (R3)"
                    )
            elif r.get("hedge_group") and r["hedge_group"].get("strong"):
                label, cat = "fp", "cross_market_hedge"
                group = r["hedge_group"]
                evidence = []
                if len(group.get("opposite_side_with", [])) >= 2:
                    evidence.append(f"opposite_side_with={group['opposite_side_with']}")
                if group.get("size_symmetric_with"):
                    evidence.append(f"size_symmetric_with={group['size_symmetric_with']}")
                why.append(
                    f"co-fired legs of {group['event']} with {group['with']} "
                    f"({', '.join(evidence)}) (R3)"
                )
            else:
                label = "noise"
                if f.get("result") and f.get("result") != r["side"]:
                    why.append(f"settled {f['result']} != side {r['side']}, no kept favorable move (OT-N)")
                elif f.get("fetch") or not f.get("candles"):
                    why.append("market/history unfetchable or no candles -> conservative noise (OT-N)")
                else:
                    why.append(f"no favorable move >= {MOVE:.2f} kept (max {fav_max}, end {fav_end}) (OT-N)")
                if r["rule"] == "volume_spike_v1":
                    thin = any("thin" in str(x) or "low_notional" in str(x) for x in r["triage_flags"]) \
                        or float(ev.get("baseline_median_usd") or 9e9) < 50
                    if thin:
                        why.append("low_notional_thin_baseline signature")
            if r.get("two_sided_cofire"):
                why.append(f"two_sided_cofire with {r['two_sided_cofire']}")
            if r.get("hedge_group") and not r["hedge_group"].get("strong"):
                why.append(
                    "cofire_caveat: "
                    f"co-fired legs of {r['hedge_group']['event']} with "
                    f"{r['hedge_group']['with']} lack opposite-side basket or size symmetry (R3)"
                )
        r["proposed_label"] = label
        r["proposed_category"] = cat
        r["why"] = why

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"rule": "LABELING_RULE v1.3", "generated": args.generated,
                                    "constants": {"HORIZON_H": HORIZON_H, "MOVE": MOVE, "KEEP": KEEP,
                                                  "SETTLE_D": SETTLE_D, "GROUP_MIN": GROUP_MIN,
                                                  "SIZE_SYM_TOL": SIZE_SYM_TOL},
                                    "alerts": rows}, indent=1, default=str), encoding="utf-8")

    from collections import Counter
    dist = Counter((r["rule"], r["proposed_label"]) for r in rows)
    lines = [f"# M-TRUTH auto-label proposal — LABELING_RULE v1.3 — {args.generated}", "",
             "NOT RECORDED. Proposals only; operator ratification gates any DB write.", "",
             "| rule | tp | fp | noise |", "|---|---|---|---|"]
    for rule in sorted({r["rule"] for r in rows}):
        lines.append(f"| {rule} | {dist.get((rule, 'tp'), 0)} | {dist.get((rule, 'fp'), 0)} | {dist.get((rule, 'noise'), 0)} |")
    tot = Counter(r["proposed_label"] for r in rows)
    lines += ["", f"**Totals:** tp={tot.get('tp', 0)} fp={tot.get('fp', 0)} noise={tot.get('noise', 0)} of {len(rows)}", "",
              "| short_id | rule | market | side | outcome | proposal | why |", "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["rule"], r["short_id"])):
        f = r["facts"]
        oc = f.get("result") or ("closed" if f.get("closed") else f.get("status", "?"))
        mv = f.get("fav_max_move")
        oc = f"{oc}, fav_max {mv:+.2f}" if mv is not None else str(oc)
        cat = f" ({r['proposed_category']})" if r["proposed_category"] else ""
        lines.append(f"| {r['short_id']} | {r['rule']} | {r['market']} | {r['side']} | {oc} | **{r['proposed_label']}**{cat} | {'; '.join(r['why'])} |")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_json.name} and {out_md.name}; totals: {dict(tot)}")


if __name__ == "__main__":
    main()
