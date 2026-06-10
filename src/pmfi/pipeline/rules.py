"""Rule registry for AlertEngine.

Each rule is a self-contained object with a ``rule_id`` attribute and an
``evaluate(trade, engine)`` method.  The method returns an ``AlertDecision``
when the rule fires, or ``None`` when it does not.

AlertEngine builds ``self._rule_registry`` as an ordered list of these
objects.  ``AlertEngine.evaluate()`` iterates the list and collects results::

    results = []
    for rule in self._rule_registry:
        d = rule.evaluate(trade, self)
        if d is not None:
            results.append(d)
    return results

Extension point
---------------
To add a new rule without editing ``evaluate()``:

1. Implement ``AlertRule`` — a class with ``rule_id: str`` and
   ``evaluate(self, trade, engine) -> AlertDecision | None``.
   Structural conformance is sufficient; explicit inheritance is optional.
2. Instantiate it and append to the registry list built in
   ``AlertEngine.__init__`` — OR pass it via a constructor parameter if you
   need programmatic registration.

No changes to ``AlertEngine.evaluate()`` are ever needed for new rules.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import statistics
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

from pmfi.domain import AlertDecision, NormalizedTrade
from pmfi.scoring import (
    LargeTradeRule,
    score_large_trade,
    assess_data_quality,
    _cap_confidence,
)
from pmfi.pipeline.accumulator import DirectionalAccumulator


@runtime_checkable
class AlertRule(Protocol):
    """Formal contract for alert rules registered with AlertEngine.

    Every rule registered in ``AlertEngine._rule_registry`` must satisfy this
    protocol.  Structural conformance (duck-typing) is sufficient — explicit
    inheritance is optional.  ``AlertEngine.__init__`` uses
    ``isinstance(rule, AlertRule)`` at construction time to catch malformed
    rules early.
    """

    rule_id: str

    def evaluate(
        self, trade: NormalizedTrade, engine: object
    ) -> AlertDecision | None: ...


class LargeTradeAbsoluteRule:
    """large_trade_absolute_v1 — absolute capital / payout threshold."""

    rule_id = "large_trade_absolute_v1"

    def __init__(
        self,
        *,
        min_capital_at_risk_usd: Decimal,
        min_payout_notional_usd: Decimal,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._rule = LargeTradeRule(
            min_capital_at_risk_usd=min_capital_at_risk_usd,
            min_payout_notional_usd=min_payout_notional_usd,
        )

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:
        if not self._enabled:
            return None
        decision = score_large_trade(trade, self._rule)
        return decision if decision.emit_alert else None


class MarketRelativeLargeTradeRule:
    """market_relative_large_trade_v1 — percentile-based relative-size gate."""

    rule_id = "market_relative_large_trade_v1"

    def __init__(
        self,
        *,
        min_capital_at_risk_usd: Decimal,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._min_cap = min_capital_at_risk_usd
        self._severity = severity

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:  # type: ignore[override]
        if not self._enabled:
            return None
        min_cap = self._min_cap
        if trade.capital_at_risk_usd < min_cap:
            return None

        bkey = f"{trade.venue_code}:{trade.venue_market_id}"
        baseline = engine._baselines.get(bkey)  # type: ignore[attr-defined]
        threshold_percentile = "minimum"
        _emit = True
        _severity = self._severity

        if baseline and baseline.get("p99_trade_usd") is not None:
            p99 = Decimal(str(baseline["p99_trade_usd"]))
            p995 = Decimal(str(baseline.get("p995_trade_usd") or baseline["p99_trade_usd"]))
            sample_size = baseline.get("sample_size", 0)
            if trade.capital_at_risk_usd >= p995:
                confidence = "high" if sample_size >= 10 else "medium"
                score = Decimal("0.85")
                reason_codes = ("exceeds_p995_baseline",)
                threshold_percentile = "p995"
            elif trade.capital_at_risk_usd >= p99:
                confidence = "medium" if sample_size >= 5 else "low"
                score = Decimal("0.7")
                reason_codes = ("exceeds_p99_baseline",)
                threshold_percentile = "p99"
            else:
                # Capital is below p99: percentile guard not met — do not emit.
                _emit = False
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
            # No baseline — floor alert only; severity forced to low/info.
            confidence = "low"
            score = Decimal("0.5")
            reason_codes = ("capital_above_minimum_threshold",)
            data_quality = "baseline_pending"
            _severity = "low"
            evidence_extra = {"baseline_status": "baseline_missing", "baseline_state": "baseline_missing"}

        if not _emit:
            return None

        _dq, _dq_reasons = assess_data_quality(trade)
        if _dq == "degraded":
            confidence = _cap_confidence(confidence, "medium")
            data_quality = "degraded"

        return AlertDecision(
            emit_alert=True,
            rule_id="market_relative_large_trade_v1",
            rule_version="alert_rules.v1",
            severity=_severity,
            confidence=confidence,
            score=score,
            reason_codes=reason_codes,
            evidence={
                "venue_code": trade.venue_code,
                "venue_market_id": trade.venue_market_id,
                "outcome_key": trade.outcome_key,
                "capital_at_risk_usd": str(trade.capital_at_risk_usd),
                "min_capital_threshold_usd": str(min_cap),
                "threshold_percentile": threshold_percentile,
                "degraded_reasons": _dq_reasons,
                **evidence_extra,
            },
            data_quality=data_quality,
        )


class OpenInterestShockRule:
    """open_interest_shock_v1 — trade as fraction of open interest."""

    rule_id = "open_interest_shock_v1"

    def __init__(
        self,
        *,
        min_open_interest_fraction: Decimal,
        min_capital_at_risk_usd: Decimal,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._min_oi_frac = min_open_interest_fraction
        self._min_oi_cap = min_capital_at_risk_usd
        self._severity = severity

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:
        if not self._enabled:
            return None
        if trade.open_interest_contracts is None or trade.open_interest_contracts <= 0:
            return None
        oi_fraction = trade.contracts / trade.open_interest_contracts
        if oi_fraction < self._min_oi_frac or trade.capital_at_risk_usd < self._min_oi_cap:
            return None

        _dq, _dq_reasons = assess_data_quality(trade)
        _confidence = _cap_confidence("high", "medium") if _dq == "degraded" else "high"
        _data_quality = "degraded" if _dq == "degraded" else "oi_present"

        return AlertDecision(
            emit_alert=True,
            rule_id="open_interest_shock_v1",
            rule_version="alert_rules.v1",
            severity=self._severity,
            confidence=_confidence,
            score=Decimal("0.75"),
            reason_codes=("trade_fraction_of_open_interest",),
            evidence={
                "venue_code": trade.venue_code,
                "venue_market_id": trade.venue_market_id,
                "outcome_key": trade.outcome_key,
                "trade_contracts": str(trade.contracts),
                "open_interest_contracts": str(trade.open_interest_contracts),
                "oi_fraction": f"{oi_fraction:.4f}",
                "capital_at_risk_usd": str(trade.capital_at_risk_usd),
                "min_oi_fraction": str(self._min_oi_frac),
                "min_capital_threshold_usd": str(self._min_oi_cap),
                "degraded_reasons": _dq_reasons,
            },
            data_quality=_data_quality,
        )


class DirectionalClusterRule:
    """directional_cluster_v1 — rolling-window directional cluster detection."""

    rule_id = "directional_cluster_v1"

    def __init__(
        self,
        *,
        window_seconds: int,
        min_trade_count: int,
        min_net_capital_at_risk_usd: Decimal,
        min_price_impact_cents: Decimal,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._window_sec = window_seconds
        self._min_trade_count = min_trade_count
        self._min_net_capital = min_net_capital_at_risk_usd
        self._min_price_impact = min_price_impact_cents
        self._severity = severity

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:  # type: ignore[override]
        if not self._enabled:
            return None

        # Dynamic window resize if config changed between calls (matches original logic)
        if engine._accumulator._window_seconds != self._window_sec:  # type: ignore[attr-defined]
            engine._accumulator = DirectionalAccumulator(window_seconds=self._window_sec)  # type: ignore[attr-defined]

        event_ts = trade.exchange_ts or trade.received_at
        engine._accumulator.add(  # type: ignore[attr-defined]
            trade.venue_code,
            trade.venue_market_id,
            trade.directional_side,
            trade.capital_at_risk_usd,
            trade.price,
            event_ts=event_ts,
        )
        cluster = engine._accumulator.check_cluster(  # type: ignore[attr-defined]
            trade.venue_code,
            trade.venue_market_id,
            min_trade_count=self._min_trade_count,
            min_net_capital_usd=self._min_net_capital,
            min_price_impact_cents=self._min_price_impact,
            now=event_ts,
        )
        if cluster is None:
            return None

        _dq, _dq_reasons = assess_data_quality(trade)
        _is_directionally_degraded = any(
            r in _dq_reasons for r in ("direction_unknown", "outcome_unknown")
        )
        if _dq == "degraded":
            _confidence = "low" if _is_directionally_degraded else "medium"
        else:
            _confidence = "medium"
        _data_quality = "degraded" if _dq == "degraded" else "in_window"

        return AlertDecision(
            emit_alert=True,
            rule_id="directional_cluster_v1",
            rule_version="alert_rules.v1",
            severity=self._severity,
            confidence=_confidence,
            score=Decimal("0.75"),
            reason_codes=("directional_cluster_detected",),
            evidence={
                "venue_code": trade.venue_code,
                "venue_market_id": trade.venue_market_id,
                "outcome_key": trade.outcome_key,
                "directional_side": trade.directional_side,
                "side_confidence": trade.side_confidence,
                "dominant_side": cluster.dominant_side,
                "cluster_trade_count": str(cluster.trade_count),
                "net_capital_usd": str(cluster.net_capital_usd),
                "price_impact_cents": str(cluster.price_impact_cents),
                "window_seconds": str(cluster.window_seconds),
                "min_trade_count": self._min_trade_count,
                "min_net_capital_usd": float(self._min_net_capital),
                "min_price_impact_cents": float(self._min_price_impact),
                "degraded_reasons": _dq_reasons,
            },
            data_quality=_data_quality,
        )


class MomentumRule:
    """momentum_v1 — sustained directional flow over a longer window."""

    rule_id = "momentum_v1"

    def __init__(
        self,
        *,
        min_trades: int,
        min_net_capital_usd: float,
        min_price_spread: float,
        window_seconds: int,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._min_trades = min_trades
        self._min_capital = min_net_capital_usd
        self._min_spread = min_price_spread
        self._window = window_seconds
        self._severity = severity

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:  # type: ignore[override]
        if not self._enabled:
            return None

        _event_ts_m = trade.exchange_ts or trade.received_at
        engine._momentum_acc.add(  # type: ignore[attr-defined]
            trade.venue_code,
            trade.venue_market_id,
            trade.directional_side or "",
            trade.capital_at_risk_usd,
            trade.price,
            event_ts=_event_ts_m,
        )
        _mcluster = engine._momentum_acc.check_cluster(  # type: ignore[attr-defined]
            trade.venue_code,
            trade.venue_market_id,
            min_trade_count=self._min_trades,
            min_net_capital_usd=Decimal(str(self._min_capital)),
            min_price_impact_cents=Decimal(str(round(self._min_spread * 100, 6))),
            now=_event_ts_m,
        )
        if _mcluster is None:
            return None

        _dq, _dq_reasons = assess_data_quality(trade)
        _is_directionally_degraded = any(
            r in _dq_reasons for r in ("direction_unknown", "outcome_unknown")
        )
        if _dq == "degraded":
            _confidence = "low" if _is_directionally_degraded else "medium"
        else:
            _confidence = "high"
        _data_quality = "degraded" if _dq == "degraded" else "live"

        return AlertDecision(
            emit_alert=True,
            rule_id="momentum_v1",
            rule_version="alert_rules.v1",
            severity=self._severity,
            confidence=_confidence,
            score=Decimal("0.85"),
            reason_codes=("sustained_directional_flow",),
            evidence={
                "rule": "momentum_v1",
                "outcome_key": trade.outcome_key,
                "directional_side": trade.directional_side,
                "side_confidence": trade.side_confidence,
                "dominant_side": _mcluster.dominant_side,
                "net_capital_usd": round(float(_mcluster.net_capital_usd), 2),
                "trade_count": _mcluster.trade_count,
                "price_spread": round(float(_mcluster.price_impact_cents) / 100, 4),
                "window_seconds": self._window,
                "baseline_status": "not_applicable",
                "min_net_capital_usd": self._min_capital,
                "min_trades": self._min_trades,
                "min_price_spread": self._min_spread,
                "degraded_reasons": _dq_reasons,
            },
            data_quality=_data_quality,
        )


class VolumeSpikeRule:
    """volume_spike_v1 — single-trade outlier vs recent baseline median."""

    rule_id = "volume_spike_v1"

    def __init__(
        self,
        *,
        min_spike_multiplier: Decimal,
        min_baseline_trades: int,
        history_max: int,
        severity: str,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._multiplier = min_spike_multiplier
        self._min_trades = min_baseline_trades
        self._history_max = history_max
        self._severity = severity

    def evaluate(self, trade: NormalizedTrade, engine: object) -> AlertDecision | None:  # type: ignore[override]
        if not self._enabled:
            return None

        _vskey = f"{trade.venue_code}:{trade.venue_market_id}"
        _history = engine._vs_history.setdefault(_vskey, [])  # type: ignore[attr-defined]
        _this_cap: Decimal = trade.capital_at_risk_usd
        result: AlertDecision | None = None

        if len(_history) < self._min_trades:
            # Thin-market skip: not enough history to form a baseline yet.
            # Logged at DEBUG (not INFO) because this fires per-trade on new markets.
            logger.debug(
                "volume_spike_v1: thin-market skip market=%s history_len=%d min_baseline_trades=%d",
                _vskey, len(_history), self._min_trades,
            )

        if len(_history) >= self._min_trades:
            _window = sorted(_history[-self._min_trades:])
            _median = statistics.median(_window)
            if _median > 0 and _this_cap >= _median * self._multiplier:
                _dq, _dq_reasons = assess_data_quality(trade)
                _vs_confidence = _cap_confidence("medium", "medium" if _dq == "degraded" else "high")
                _vs_data_quality = "degraded" if _dq == "degraded" else "live"
                result = AlertDecision(
                    emit_alert=True,
                    rule_id="volume_spike_v1",
                    rule_version="alert_rules.v1",
                    severity=self._severity,
                    confidence=_vs_confidence,
                    score=Decimal("0.75"),
                    reason_codes=("volume_spike_detected",),
                    data_quality=_vs_data_quality,
                    evidence={
                        "rule": "volume_spike_v1",
                        "outcome_key": trade.outcome_key,
                        "this_trade_usd": round(float(_this_cap), 2),
                        "baseline_median_usd": round(float(_median), 2),
                        "spike_multiplier": round(float(_this_cap / _median), 2),
                        "min_spike_multiplier": float(self._multiplier),
                        "baseline_trades": self._min_trades,
                        "degraded_reasons": _dq_reasons,
                    },
                )

        # Append AFTER check so spike trade doesn't inflate its own baseline
        _history.append(_this_cap)
        if len(_history) > self._history_max:
            engine._vs_history[_vskey] = _history[-self._history_max:]  # type: ignore[attr-defined]

        return result
