from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from pmfi.domain import RawEvent, NormalizedTrade, AlertDecision
from pmfi.fixtures import load_raw_event
from pmfi.normalization import normalize_polymarket_fixture, normalize_kalshi_fixture, NormalizationError
from pmfi.pipeline.engine import AlertEngine

@dataclass
class ReplayResult:
    fixture_path: str
    trade: NormalizedTrade
    alerts: list[AlertDecision]

def replay_fixtures(
    fixture_dir: Path,
    *,
    rules_path: Path | None = None,
    verbose: bool = False,
) -> list[ReplayResult]:
    engine = AlertEngine(rules_path=rules_path)
    results: list[ReplayResult] = []
    for path in sorted(fixture_dir.glob("*.json")):
        try:
            raw = load_raw_event(path)
        except Exception as exc:
            if verbose:
                print(f"  skip {path.name}: {exc}")
            continue
        try:
            if raw.venue_code == "polymarket":
                trade = normalize_polymarket_fixture(raw)
            elif raw.venue_code == "kalshi":
                trade = normalize_kalshi_fixture(raw)
            else:
                continue
        except NormalizationError as exc:
            if verbose:
                print(f"  norm error {path.name}: {exc}")
            continue
        decisions = engine.evaluate(trade)
        results.append(ReplayResult(fixture_path=str(path), trade=trade, alerts=decisions))
        if verbose:
            for d in decisions:
                print(f"  ALERT {d.rule_id} {d.severity} score={d.score}")
    return results
