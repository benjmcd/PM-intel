from __future__ import annotations
from pathlib import Path
from decimal import Decimal
import yaml
from pmfi.domain import NormalizedTrade, AlertDecision
from pmfi.scoring import LargeTradeRule, score_large_trade
from pmfi.pipeline.accumulator import DirectionalAccumulator

ROOT = Path(__file__).resolve().parents[3]

class AlertEngine:
    def __init__(self, rules_path: Path | None = None, baselines: dict | None = None):
        if rules_path is None:
            rules_path = ROOT / "config" / "alert_rules.yaml"
        self._rules_path = rules_path
        self._rules = self._load_rules()
        # keyed by "venue_code:venue_market_id"
        self._baselines: dict = baselines or {}
        self._accumulator = DirectionalAccumulator(window_seconds=300)

        # Separate accumulator for momentum_v1 (longer window)
        _mom_rule = self._rules.get("rules", {}).get("momentum_v1", {})
        _mom_window = int(_mom_rule.get("window_seconds", 900))
        self._momentum_acc = DirectionalAccumulator(window_seconds=_mom_window)
        self._momentum_window = _mom_window
        self._momentum_min_trades = int(_mom_rule.get("min_trades", 5))
        self._momentum_min_capital = float(_mom_rule.get("min_net_capital_usd", 75000))
        self._momentum_min_spread = float(_mom_rule.get("min_price_spread", 0.03))
        self._momentum_severity = str(_mom_rule.get("severity", "high"))
        self._momentum_enabled = bool(_mom_rule.get("enabled", True))

    def _load_rules(self) -> dict:
        if self._rules_path.exists():
            return yaml.safe_load(self._rules_path.read_text(encoding="utf-8")) or {}
        return {}

    def update_baselines(self, baselines: dict) -> None:
        self._baselines = baselines

    def evaluate(self, trade: NormalizedTrade) -> list[AlertDecision]:
        results: list[AlertDecision] = []
        rules = self._rules.get("rules", {})

        lt_cfg = rules.get("large_trade_absolute_v1", {})
        if lt_cfg.get("enabled", True):
            rule = LargeTradeRule(
                min_capital_at_risk_usd=Decimal(str(lt_cfg.get("min_capital_at_risk_usd", 25000))),
                min_payout_notional_usd=Decimal(str(lt_cfg.get("min_payout_notional_usd", 100000))),
            )
            decision = score_large_trade(trade, rule)
            if decision.emit_alert:
                results.append(decision)

        mr_cfg = rules.get("market_relative_large_trade_v1", {})
        if mr_cfg.get("enabled", True):
            min_cap = Decimal(str(mr_cfg.get("min_capital_at_risk_usd", 5000)))
            if trade.capital_at_risk_usd >= min_cap:
                bkey = f"{trade.venue_code}:{trade.venue_market_id}"
                baseline = self._baselines.get(bkey)
                if baseline and baseline.get("p99_trade_usd") is not None:
                    p99 = Decimal(str(baseline["p99_trade_usd"]))
                    p995 = Decimal(str(baseline.get("p995_trade_usd") or baseline["p99_trade_usd"]))
                    sample_size = baseline.get("sample_size", 0)
                    if trade.capital_at_risk_usd >= p995:
                        confidence = "high" if sample_size >= 10 else "medium"
                        score = Decimal("0.85")
                        reason_codes = ("exceeds_p995_baseline",)
                    elif trade.capital_at_risk_usd >= p99:
                        confidence = "medium" if sample_size >= 5 else "low"
                        score = Decimal("0.7")
                        reason_codes = ("exceeds_p99_baseline",)
                    else:
                        confidence = "low"
                        score = Decimal("0.4")
                        reason_codes = ("capital_above_minimum_threshold",)
                    data_quality = "baseline_available"
                    _bstate = "baseline_sufficient" if sample_size >= 10 else "baseline_sparse"
                    evidence_extra = {
                        "p99_trade_usd": str(p99),
                        "p995_trade_usd": str(p995),
                        "baseline_sample_size": str(sample_size),
                        "baseline_status": "available",
                        "baseline_state": _bstate,
                    }
                else:
                    confidence = "low"
                    score = Decimal("0.5")
                    reason_codes = ("capital_above_minimum_threshold",)
                    data_quality = "baseline_pending"
                    evidence_extra = {"baseline_status": "baseline_missing", "baseline_state": "baseline_missing"}

                results.append(AlertDecision(
                    emit_alert=True,
                    rule_id="market_relative_large_trade_v1",
                    rule_version="alert_rules.v1",
                    severity=str(mr_cfg.get("severity", "medium")),
                    confidence=confidence,
                    score=score,
                    reason_codes=reason_codes,
                    evidence={
                        "venue_code": trade.venue_code,
                        "venue_market_id": trade.venue_market_id,
                        "capital_at_risk_usd": str(trade.capital_at_risk_usd),
                        "min_capital_threshold_usd": str(min_cap),
                        **evidence_extra,
                    },
                    data_quality=data_quality,
                ))

        oi_cfg = rules.get("open_interest_shock_v1", {})
        if oi_cfg.get("enabled", True) and trade.open_interest_contracts is not None and trade.open_interest_contracts > 0:
            min_oi_frac = Decimal(str(oi_cfg.get("min_open_interest_fraction", "0.03")))
            min_oi_cap = Decimal(str(oi_cfg.get("min_capital_at_risk_usd", 5000)))
            oi_fraction = trade.contracts / trade.open_interest_contracts
            if oi_fraction >= min_oi_frac and trade.capital_at_risk_usd >= min_oi_cap:
                results.append(AlertDecision(
                    emit_alert=True,
                    rule_id="open_interest_shock_v1",
                    rule_version="alert_rules.v1",
                    severity=str(oi_cfg.get("severity", "high")),
                    confidence="medium",
                    score=Decimal("0.75"),
                    reason_codes=("trade_fraction_of_open_interest",),
                    evidence={
                        "venue_code": trade.venue_code,
                        "venue_market_id": trade.venue_market_id,
                        "trade_contracts": str(trade.contracts),
                        "open_interest_contracts": str(trade.open_interest_contracts),
                        "oi_fraction": f"{oi_fraction:.4f}",
                        "capital_at_risk_usd": str(trade.capital_at_risk_usd),
                        "min_oi_fraction": str(min_oi_frac),
                    },
                    data_quality="oi_present",
                ))

        dc_cfg = rules.get("directional_cluster_v1", {})
        if dc_cfg.get("enabled", True):
            window_sec = int(dc_cfg.get("window_seconds", 300))
            if self._accumulator._window_seconds != window_sec:
                self._accumulator = DirectionalAccumulator(window_seconds=window_sec)
            event_ts = trade.exchange_ts or trade.received_at
            self._accumulator.add(
                trade.venue_code,
                trade.venue_market_id,
                trade.directional_side,
                trade.capital_at_risk_usd,
                trade.price,
                event_ts=event_ts,
            )
            cluster = self._accumulator.check_cluster(
                trade.venue_code,
                trade.venue_market_id,
                min_trade_count=int(dc_cfg.get("min_trade_count", 3)),
                min_net_capital_usd=Decimal(str(dc_cfg.get("min_net_capital_at_risk_usd", 15000))),
                min_price_impact_cents=Decimal(str(dc_cfg.get("min_price_impact_cents", 2))),
                now=event_ts,
            )
            if cluster is not None:
                results.append(AlertDecision(
                    emit_alert=True,
                    rule_id="directional_cluster_v1",
                    rule_version="alert_rules.v1",
                    severity=str(dc_cfg.get("severity", "high")),
                    confidence="medium",
                    score=Decimal("0.75"),
                    reason_codes=("directional_cluster_detected",),
                    evidence={
                        "venue_code": trade.venue_code,
                        "venue_market_id": trade.venue_market_id,
                        "dominant_side": cluster.dominant_side,
                        "cluster_trade_count": str(cluster.trade_count),
                        "net_capital_usd": str(cluster.net_capital_usd),
                        "price_impact_cents": str(cluster.price_impact_cents),
                        "window_seconds": str(cluster.window_seconds),
                    },
                    data_quality="in_window",
                ))

        # ── momentum_v1 ──────────────────────────────────────────────────
        if self._momentum_enabled:
            _event_ts_m = trade.exchange_ts or trade.received_at
            self._momentum_acc.add(
                trade.venue_code,
                trade.venue_market_id,
                trade.directional_side or "",
                trade.capital_at_risk_usd,
                trade.price,
                event_ts=_event_ts_m,
            )
            _mcluster = self._momentum_acc.check_cluster(
                trade.venue_code,
                trade.venue_market_id,
                min_trade_count=self._momentum_min_trades,
                min_net_capital_usd=Decimal(str(self._momentum_min_capital)),
                min_price_impact_cents=Decimal(str(round(self._momentum_min_spread * 100, 6))),
                now=_event_ts_m,
            )
            if _mcluster is not None:
                results.append(AlertDecision(
                    emit_alert=True,
                    rule_id="momentum_v1",
                    rule_version="alert_rules.v1",
                    severity=self._momentum_severity,
                    confidence="high",
                    score=Decimal("0.85"),
                    reason_codes=("sustained_directional_flow",),
                    evidence={
                        "rule": "momentum_v1",
                        "dominant_side": _mcluster.dominant_side,
                        "net_capital_usd": round(float(_mcluster.net_capital_usd), 2),
                        "trade_count": _mcluster.trade_count,
                        "price_spread": round(float(_mcluster.price_impact_cents) / 100, 4),
                        "window_seconds": self._momentum_window,
                        "baseline_status": "not_applicable",
                    },
                    data_quality="live",
                ))

        return results
