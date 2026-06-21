from __future__ import annotations
from pathlib import Path
from decimal import Decimal
import yaml
from pmfi.domain import NormalizedTrade, AlertDecision
from pmfi.pipeline.accumulator import DirectionalAccumulator
from pmfi.pipeline.rules import (
    AlertRule,
    LargeTradeAbsoluteRule,
    MarketRelativeLargeTradeRule,
    OpenInterestShockRule,
    DirectionalClusterRule,
    MomentumRule,
    VolumeSpikeRule,
)

ROOT = Path(__file__).resolve().parents[3]

_VALID_SEVERITIES = frozenset({"low", "medium", "high"})


def _validate_rules_dict(rules: dict) -> tuple[bool, str]:
    if not isinstance(rules, dict):
        return False, "rules config must be a dict"
    rule_map = rules.get("rules")
    if not isinstance(rule_map, dict) or not rule_map:
        return False, "'rules' must be a non-empty dict"
    for rule_id, rule_cfg in rule_map.items():
        if not isinstance(rule_cfg, dict):
            return False, f"rule '{rule_id}' must be a dict"
        severity = rule_cfg.get("severity")
        if severity is not None and severity not in _VALID_SEVERITIES:
            return False, f"rule '{rule_id}' has invalid severity {severity!r}"
    return True, ""


class AlertEngine:
    def __init__(
        self,
        rules_path: Path | None = None,
        baselines: dict | None = None,
        rules_config: dict | None = None,
        directional_accumulator_max_markets: int = 5000,
        directional_accumulator_ttl_seconds: float = 3600.0,
    ):
        if rules_path is None:
            rules_path = ROOT / "config" / "alert_rules.yaml"
        self._rules_path = rules_path
        self._rules = rules_config if rules_config is not None else self._load_rules()
        # keyed by "venue_code:venue_market_id"
        self._baselines: dict = baselines or {}
        self._directional_accumulator_max_markets = directional_accumulator_max_markets
        self._directional_accumulator_ttl_seconds = directional_accumulator_ttl_seconds
        self._accumulator = DirectionalAccumulator(
            window_seconds=300,
            max_markets=directional_accumulator_max_markets,
            market_ttl_seconds=directional_accumulator_ttl_seconds,
        )

        # Separate accumulator for momentum_v1 (longer window). _momentum_acc and
        # _momentum_window persist (seed_from_db reads the window); the remaining
        # momentum thresholds are only used to build the rule, so they stay local.
        _mom_rule = self._rules.get("rules", {}).get("momentum_v1", {})
        _mom_window = int(_mom_rule.get("window_seconds", 900))
        self._momentum_acc = DirectionalAccumulator(
            window_seconds=_mom_window,
            max_markets=directional_accumulator_max_markets,
            market_ttl_seconds=directional_accumulator_ttl_seconds,
        )
        self._momentum_window = _mom_window
        _mom_min_trades = int(_mom_rule.get("min_trades", 5))
        _mom_min_capital = float(_mom_rule.get("min_net_capital_usd", 75000))
        _mom_min_spread = float(_mom_rule.get("min_price_spread", 0.03))
        _mom_severity = str(_mom_rule.get("severity", "high"))
        _mom_enabled = bool(_mom_rule.get("enabled", True))
        self._momentum_min_trades = _mom_min_trades
        self._momentum_min_capital = _mom_min_capital
        self._momentum_min_spread = _mom_min_spread
        self._momentum_severity = _mom_severity
        self._momentum_enabled = _mom_enabled

        # Per-market recent trade history for volume spike detection. _vs_history and
        # _vs_min_trades/_vs_history_max persist (seed_from_db reads them).
        _vs_rule = self._rules.get("rules", {}).get("volume_spike_v1", {})
        _vs_enabled = bool(_vs_rule.get("enabled", True))
        _vs_multiplier = Decimal(str(_vs_rule.get("min_spike_multiplier", 5.0)))
        _vs_min_trade_usd = Decimal(str(_vs_rule.get("min_trade_usd", 0)))
        _vs_low_notional_threshold = Decimal(str(_vs_rule.get("low_notional_threshold_usd", 5000)))
        _vs_low_notional_min_trades_raw = _vs_rule.get("low_notional_min_baseline_trades")
        _vs_low_notional_min_trades = (
            int(_vs_low_notional_min_trades_raw)
            if _vs_low_notional_min_trades_raw is not None
            else None
        )
        _vs_low_notional_min_median_raw = _vs_rule.get("low_notional_min_baseline_median_usd")
        _vs_low_notional_min_median = (
            Decimal(str(_vs_low_notional_min_median_raw))
            if _vs_low_notional_min_median_raw is not None
            else None
        )
        _vs_low_notional_max_multiplier_raw = _vs_rule.get(
            "low_notional_max_spike_multiplier"
        )
        _vs_low_notional_max_multiplier = (
            Decimal(str(_vs_low_notional_max_multiplier_raw))
            if _vs_low_notional_max_multiplier_raw is not None
            else None
        )
        self._vs_min_trades = int(_vs_rule.get("min_baseline_trades", 20))
        _vs_severity = str(_vs_rule.get("severity", "medium"))
        self._vs_enabled = _vs_enabled
        self._vs_multiplier = _vs_multiplier
        self._vs_min_trade_usd = _vs_min_trade_usd
        self._vs_low_notional_threshold = _vs_low_notional_threshold
        self._vs_low_notional_min_trades = _vs_low_notional_min_trades
        self._vs_low_notional_min_median = _vs_low_notional_min_median
        self._vs_low_notional_max_multiplier = _vs_low_notional_max_multiplier
        self._vs_severity = _vs_severity
        self._vs_history: dict[str, list[Decimal]] = {}  # market_key → list of capital_at_risk_usd
        # history_max: max trades kept per market for the rolling baseline.
        # Configurable via volume_spike_v1.history_max in alert_rules.yaml (default 200).
        self._vs_history_max = int(_vs_rule.get("history_max", 200))

        # ── Rule registry (ordered; matches original evaluation order) ──────
        _rules_cfg = self._rules.get("rules", {})
        _lt_cfg = _rules_cfg.get("large_trade_absolute_v1", {})
        _mr_cfg = _rules_cfg.get("market_relative_large_trade_v1", {})
        _oi_cfg = _rules_cfg.get("open_interest_shock_v1", {})
        _dc_cfg = _rules_cfg.get("directional_cluster_v1", {})
        self._rule_registry = [
            LargeTradeAbsoluteRule(
                min_capital_at_risk_usd=Decimal(str(_lt_cfg.get("min_capital_at_risk_usd", 25000))),
                min_payout_notional_usd=Decimal(str(_lt_cfg.get("min_payout_notional_usd", 100000))),
                enabled=bool(_lt_cfg.get("enabled", True)),
            ),
            MarketRelativeLargeTradeRule(
                min_capital_at_risk_usd=Decimal(str(_mr_cfg.get("min_capital_at_risk_usd", 5000))),
                severity=str(_mr_cfg.get("severity", "medium")),
                enabled=bool(_mr_cfg.get("enabled", True)),
            ),
            OpenInterestShockRule(
                min_open_interest_fraction=Decimal(str(_oi_cfg.get("min_open_interest_fraction", "0.03"))),
                min_capital_at_risk_usd=Decimal(str(_oi_cfg.get("min_capital_at_risk_usd", 5000))),
                severity=str(_oi_cfg.get("severity", "high")),
                enabled=bool(_oi_cfg.get("enabled", True)),
            ),
            DirectionalClusterRule(
                window_seconds=int(_dc_cfg.get("window_seconds", 300)),
                min_trade_count=int(_dc_cfg.get("min_trade_count", 3)),
                min_net_capital_at_risk_usd=Decimal(str(_dc_cfg.get("min_net_capital_at_risk_usd", 15000))),
                min_price_impact_cents=Decimal(str(_dc_cfg.get("min_price_impact_cents", 2))),
                severity=str(_dc_cfg.get("severity", "high")),
                enabled=bool(_dc_cfg.get("enabled", True)),
            ),
            MomentumRule(
                min_trades=_mom_min_trades,
                min_net_capital_usd=_mom_min_capital,
                min_price_spread=_mom_min_spread,
                window_seconds=self._momentum_window,
                severity=_mom_severity,
                enabled=_mom_enabled,
            ),
            VolumeSpikeRule(
                min_spike_multiplier=_vs_multiplier,
                min_baseline_trades=self._vs_min_trades,
                min_trade_usd=_vs_min_trade_usd,
                low_notional_threshold_usd=_vs_low_notional_threshold,
                low_notional_min_baseline_trades=_vs_low_notional_min_trades,
                low_notional_min_baseline_median_usd=_vs_low_notional_min_median,
                low_notional_max_spike_multiplier=_vs_low_notional_max_multiplier,
                history_max=self._vs_history_max,
                severity=_vs_severity,
                enabled=_vs_enabled,
            ),
        ]
        # Validate each registered rule conforms to AlertRule at construction time.
        for _r in self._rule_registry:
            assert isinstance(_r, AlertRule), (
                f"Rule {_r!r} does not conform to AlertRule protocol "
                "(must have rule_id: str and evaluate(trade, engine) method)"
            )
            assert isinstance(_r.rule_id, str), (
                f"Rule {_r!r}.rule_id must be str, got {type(_r.rule_id)}"
            )

    async def seed_from_db(self, pool: object, before_ts: object) -> None:
        """Pre-populate accumulators and _vs_history from normalized_trades before before_ts.

        Queries trades within each rule's lookback window ending at before_ts so
        cluster/momentum/volume_spike rules see warm state at replay start instead
        of cold-starting at zero.
        """
        import asyncpg  # type: ignore[import]
        from decimal import Decimal as _D
        from datetime import timedelta

        dc_cfg = self._rules.get("rules", {}).get("directional_cluster_v1", {})
        dc_window = int(dc_cfg.get("window_seconds", 300))
        mom_window = self._momentum_window
        vs_min = self._vs_min_trades
        # Lookback: max of all windows + a small buffer for volume_spike (uses last N trades,
        # not a time window, so we use 24h as a generous seed horizon for it)
        seed_horizon_seconds = max(dc_window, mom_window, 86400)

        from datetime import timezone
        # Convert before_ts to a datetime with tz if it isn't already
        if hasattr(before_ts, "tzinfo") and before_ts.tzinfo is None:  # type: ignore[union-attr]
            before_ts = before_ts.replace(tzinfo=timezone.utc)  # type: ignore[union-attr]

        cutoff_ts = before_ts - timedelta(seconds=seed_horizon_seconds)  # type: ignore[operator]

        query = (
            "SELECT nt.venue_code, m.venue_market_id, nt.directional_side, "
            "       nt.capital_at_risk_usd, nt.price, "
            "       COALESCE(nt.exchange_ts, nt.received_at) AS event_ts "
            "FROM normalized_trades nt "
            "JOIN markets m ON nt.market_id = m.market_id "
            "WHERE COALESCE(nt.exchange_ts, nt.received_at) >= $1 "
            "  AND COALESCE(nt.exchange_ts, nt.received_at) < $2 "
            "ORDER BY event_ts, nt.trade_id"
        )

        async with pool.acquire() as conn:  # type: ignore[attr-defined]
            rows = await conn.fetch(query, cutoff_ts, before_ts)

        for row in rows:
            vc = row["venue_code"]
            vmid = row["venue_market_id"]
            side = row["directional_side"] or ""
            capital = _D(str(row["capital_at_risk_usd"]))
            price = _D(str(row["price"]))
            event_ts = row["event_ts"]
            if hasattr(event_ts, "tzinfo") and event_ts.tzinfo is None:
                from datetime import timezone as _tz
                event_ts = event_ts.replace(tzinfo=_tz.utc)

            # Feed directional_cluster accumulator (prunes by its own window)
            self._accumulator.add(vc, vmid, side, capital, price, event_ts=event_ts)
            # Feed momentum accumulator
            self._momentum_acc.add(vc, vmid, side, capital, price, event_ts=event_ts)
            # Feed volume_spike history
            vskey = f"{vc}:{vmid}"
            hist = self._vs_history.setdefault(vskey, [])
            hist.append(capital)
            if len(hist) > self._vs_history_max:
                self._vs_history[vskey] = hist[-self._vs_history_max:]

    def _load_rules(self) -> dict:
        if self._rules_path.exists():
            return yaml.safe_load(self._rules_path.read_text(encoding="utf-8")) or {}
        return {}

    def _rebuild_rule_registry(self) -> None:
        _rules_cfg = self._rules.get("rules", {})
        _mom_rule = _rules_cfg.get("momentum_v1", {})
        _mom_window = int(_mom_rule.get("window_seconds", 900))
        if _mom_window != self._momentum_window:
            self._momentum_acc = DirectionalAccumulator(
                window_seconds=_mom_window,
                max_markets=self._directional_accumulator_max_markets,
                market_ttl_seconds=self._directional_accumulator_ttl_seconds,
            )
        self._momentum_window = _mom_window
        self._momentum_min_trades = int(_mom_rule.get("min_trades", 5))
        self._momentum_min_capital = float(_mom_rule.get("min_net_capital_usd", 75000))
        self._momentum_min_spread = float(_mom_rule.get("min_price_spread", 0.03))
        self._momentum_severity = str(_mom_rule.get("severity", "high"))
        self._momentum_enabled = bool(_mom_rule.get("enabled", True))

        _vs_rule = _rules_cfg.get("volume_spike_v1", {})
        self._vs_enabled = bool(_vs_rule.get("enabled", True))
        self._vs_multiplier = Decimal(str(_vs_rule.get("min_spike_multiplier", 5.0)))
        self._vs_min_trade_usd = Decimal(str(_vs_rule.get("min_trade_usd", 0)))
        self._vs_low_notional_threshold = Decimal(str(_vs_rule.get("low_notional_threshold_usd", 5000)))
        _vs_low_notional_min_trades_raw = _vs_rule.get("low_notional_min_baseline_trades")
        self._vs_low_notional_min_trades = (
            int(_vs_low_notional_min_trades_raw)
            if _vs_low_notional_min_trades_raw is not None
            else None
        )
        _vs_low_notional_min_median_raw = _vs_rule.get("low_notional_min_baseline_median_usd")
        self._vs_low_notional_min_median = (
            Decimal(str(_vs_low_notional_min_median_raw))
            if _vs_low_notional_min_median_raw is not None
            else None
        )
        _vs_low_notional_max_multiplier_raw = _vs_rule.get("low_notional_max_spike_multiplier")
        self._vs_low_notional_max_multiplier = (
            Decimal(str(_vs_low_notional_max_multiplier_raw))
            if _vs_low_notional_max_multiplier_raw is not None
            else None
        )
        self._vs_min_trades = int(_vs_rule.get("min_baseline_trades", 20))
        self._vs_severity = str(_vs_rule.get("severity", "medium"))
        self._vs_history_max = int(_vs_rule.get("history_max", 200))

        _lt_cfg = _rules_cfg.get("large_trade_absolute_v1", {})
        _mr_cfg = _rules_cfg.get("market_relative_large_trade_v1", {})
        _oi_cfg = _rules_cfg.get("open_interest_shock_v1", {})
        _dc_cfg = _rules_cfg.get("directional_cluster_v1", {})
        self._rule_registry = [
            LargeTradeAbsoluteRule(
                min_capital_at_risk_usd=Decimal(str(_lt_cfg.get("min_capital_at_risk_usd", 25000))),
                min_payout_notional_usd=Decimal(str(_lt_cfg.get("min_payout_notional_usd", 100000))),
                enabled=bool(_lt_cfg.get("enabled", True)),
            ),
            MarketRelativeLargeTradeRule(
                min_capital_at_risk_usd=Decimal(str(_mr_cfg.get("min_capital_at_risk_usd", 5000))),
                severity=str(_mr_cfg.get("severity", "medium")),
                enabled=bool(_mr_cfg.get("enabled", True)),
            ),
            OpenInterestShockRule(
                min_open_interest_fraction=Decimal(str(_oi_cfg.get("min_open_interest_fraction", "0.03"))),
                min_capital_at_risk_usd=Decimal(str(_oi_cfg.get("min_capital_at_risk_usd", 5000))),
                severity=str(_oi_cfg.get("severity", "high")),
                enabled=bool(_oi_cfg.get("enabled", True)),
            ),
            DirectionalClusterRule(
                window_seconds=int(_dc_cfg.get("window_seconds", 300)),
                min_trade_count=int(_dc_cfg.get("min_trade_count", 3)),
                min_net_capital_at_risk_usd=Decimal(str(_dc_cfg.get("min_net_capital_at_risk_usd", 15000))),
                min_price_impact_cents=Decimal(str(_dc_cfg.get("min_price_impact_cents", 2))),
                severity=str(_dc_cfg.get("severity", "high")),
                enabled=bool(_dc_cfg.get("enabled", True)),
            ),
            MomentumRule(
                min_trades=self._momentum_min_trades,
                min_net_capital_usd=self._momentum_min_capital,
                min_price_spread=self._momentum_min_spread,
                window_seconds=self._momentum_window,
                severity=self._momentum_severity,
                enabled=self._momentum_enabled,
            ),
            VolumeSpikeRule(
                min_spike_multiplier=self._vs_multiplier,
                min_baseline_trades=self._vs_min_trades,
                min_trade_usd=self._vs_min_trade_usd,
                low_notional_threshold_usd=self._vs_low_notional_threshold,
                low_notional_min_baseline_trades=self._vs_low_notional_min_trades,
                low_notional_min_baseline_median_usd=self._vs_low_notional_min_median,
                low_notional_max_spike_multiplier=self._vs_low_notional_max_multiplier,
                history_max=self._vs_history_max,
                severity=self._vs_severity,
                enabled=self._vs_enabled,
            ),
        ]
        for _r in self._rule_registry:
            assert isinstance(_r, AlertRule)
            assert isinstance(_r.rule_id, str)

    def reload_rules(self, new_rules: dict) -> bool:
        ok, _error = _validate_rules_dict(new_rules)
        if not ok:
            return False
        self._rules = new_rules
        self._rebuild_rule_registry()
        return True

    def update_baselines(self, baselines: dict) -> None:
        self._baselines = baselines

    def evaluate(self, trade: NormalizedTrade) -> list[AlertDecision]:
        results: list[AlertDecision] = []
        for rule in self._rule_registry:
            d = rule.evaluate(trade, self)
            if d is not None:
                results.append(d)
        return results
