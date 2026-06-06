from __future__ import annotations
from pathlib import Path
from pmfi.replay import replay_fixtures, ReplayResult

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "raw"

def test_replay_returns_results():
    results = replay_fixtures(FIXTURE_DIR)
    assert isinstance(results, list)
    assert len(results) >= 1
    for r in results:
        assert isinstance(r, ReplayResult)
        assert r.trade is not None
        assert isinstance(r.alerts, list)

def test_replay_verbose_does_not_raise():
    results = replay_fixtures(FIXTURE_DIR, verbose=True)
    assert len(results) >= 1

def test_replay_empty_dir(tmp_path):
    results = replay_fixtures(tmp_path)
    assert results == []
