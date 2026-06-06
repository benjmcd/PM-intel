from __future__ import annotations
from pathlib import Path
from decimal import Decimal
import yaml
from pmfi.domain import NormalizedTrade, AlertDecision
from pmfi.scoring import LargeTradeRule, score_large_trade

ROOT = Path(__file__).resolve().parents[3]

class AlertEngine:
    def __init__(self, rules_path: Path | None = None):
        if rules_path is None:
            rules_path = ROOT / "config" / "alert_rules.yaml"
        self._rules_path = rules_path
        self._rules = self._load_rules()

    def _load_rules(self) -> dict:
        if self._rules_path.exists():
            return yaml.safe_load(self._rules_path.read_text(encoding="utf-8")) or {}
        return {}

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
        return results
