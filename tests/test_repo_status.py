from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from scripts import repo_status


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "docs" / "implementation" / "02_task_graph.yaml"


def load_graph() -> dict:
    return yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8"))


def test_task_graph_distinguishes_proven_core_from_remaining_work():
    graph = load_graph()
    statuses = {milestone["id"]: milestone["status"] for milestone in graph["milestones"]}

    assert statuses["M1"] == "core_proven"
    assert statuses["M2"] == "core_proven"
    assert statuses["M3"] == "core_proven"
    assert statuses["M10"] == "continuous_hardening"
    assert "high_priority" not in statuses.values()
    assert "ready_after_or_parallel_with_M1" not in statuses.values()
    assert "ready_with_fixtures" not in statuses.values()

    posture = graph["current_posture"]
    assert "implemented local core" in posture["summary"]
    assert "not final long-term completion" in posture["summary"]
    focus = posture["next_recommended_focus"]
    assert focus["id"] == "packet_backed_calibration_decision"
    assert "exported local review packet" in focus["summary"]
    assert "calibration decision" in focus["summary"]
    assert "replay proof" in focus["summary"]
    assert "Publish the current exact-soak" not in focus["summary"]
    assert len(posture["residual_proof_gaps"]) >= 3
    proof = "\n".join(posture["verified_proof"])
    assert "Strict Polymarket live soak passed on 2026-06-18" in proof
    assert "raw_events=11643" in proof
    assert "normalized_trades=781" in proof
    assert "alerts=10" in proof
    assert "raw_evidence_duration_minutes=68.9" in proof
    assert "Strict Kalshi-required live soak passed on 2026-06-18" in proof
    assert "kalshi raw_events=1144" in proof
    assert "kalshi normalized_trades=1144" in proof
    assert "kalshi raw_evidence_duration_minutes=60.862" in proof
    assert "pmfi markets sync-one" in proof
    assert "Tier-1 alert-quality review recorded 23" in proof
    assert "market_relative_large_trade_v1 alert recorded" in proof
    assert "one true-positive review row" in proof
    assert "volume_spike_v1 now has a configurable min_trade_usd=500 floor" in proof
    assert "Read-only 24h DB replay with current post-tuning rules" in proof
    assert "zero volume_spike_v1 alerts below the configured 500 USD floor" in proof
    assert "Publish-readiness validation passed after fetching origin on 2026-06-18" in proof
    assert "branch main was clean, ahead 52 and behind 0 against origin/main" in proof
    assert "no attribution/generated-footer hits" in proof
    assert "Publication completed on 2026-06-18" in proof
    assert "git push origin main succeeded" in proof
    assert "local HEAD and origin/main matched" in proof
    assert "Fresh post-publication bounded live ingest passed on 2026-06-18" in proof
    assert "raw_events=2021" in proof
    assert "normalized_trades=290" in proof
    assert "raw_evidence_duration_minutes=9.965" in proof
    assert "kalshi raw_events=266" in proof
    assert "polymarket raw_events=1755" in proof
    assert "Dashboard alert hardening now surfaces deterministic triage flags" in proof
    assert "read-only /api/alerts payload and browser alerts table" in proof
    assert "shared pmfi.alert_triage helper" in proof
    assert "Exact-run soak validation now supports explicit --since and --until bounds" in proof
    assert "Fresh exact bounded current-traffic soak passed on 2026-06-18" in proof
    assert "raw_events=15711" in proof
    assert "normalized_trades=723" in proof
    assert "raw_evidence_duration_minutes=69.984" in proof
    assert "kalshi raw_events=167" in proof
    assert "polymarket raw_events=15544" in proof
    assert "Dashboard alert filtering is now read-only and operator-facing" in proof
    assert "review_state, latest review_label, and repeated or comma-separated" in proof
    assert "Dashboard alert review writes are implemented as a localhost-only append-only POST" in proof
    assert "POST /api/alerts/{alert_id}/review" in proof
    assert "shared alert repository helper" in proof
    assert "Local dashboard review endpoint smoke passed against local Postgres" in proof
    assert "malformed JSON returned HTTP 400" in proof
    assert "GET /api/alerts with reviewed/tp filters returned the inserted latest-review state" in proof
    assert "Headless and headed Chrome smoke passed on 2026-06-18" in proof
    assert "zero table-cell overlaps at a 1440x950 viewport" in proof
    assert "Dashboard review-write hardening now rejects non-application/json POSTs" in proof
    assert "rejects foreign Origin/Referer headers with HTTP 403" in proof
    assert "prevent attribute injection" in proof
    assert "Dashboard-origin review cross-surface smoke passed on 2026-06-18" in proof
    assert "pmfi alerts fp-rate reported Reviewed=1 and FP=1" in proof
    assert "Local review-packet export is implemented" in proof
    assert "pmfi alerts review-packet" in proof
    assert "latest-review authority" in proof
    assert "reports/review-packets" in proof
    assert "without overwriting existing files" in proof
    assert "Review-packet DB smoke passed on 2026-06-18" in proof
    assert "reviewed alerts in the cohort" in proof
    assert "raw_events=30529" in proof
    assert "normalized_trades=2948" in proof
    gaps = "\n".join(posture["residual_proof_gaps"])
    assert "Live alert review queue is fully labeled" in gaps
    assert "23 volume_spike_v1 noise rows and 1 market_relative_large_trade_v1" in gaps
    assert "Publication is complete for current local commits" in gaps
    assert "Review-packet export is implemented and DB-smoked" in gaps
    assert "future calibration claims still need packet inspection" in gaps
    assert "there is not yet a compact local review-packet export" not in gaps
    assert "Publication has not been performed" not in gaps
    assert "validated as push-ready" not in gaps
    assert "Publish or remote-branch readiness is not implied" not in gaps
    assert "Tuned volume_spike_v1 min_trade_usd threshold still needs" not in gaps
    assert "One live market_relative_large_trade_v1 alert remains unreviewed" not in gaps
    assert "Alert quality still needs operator review of the unreviewed live Polymarket" not in gaps
    assert "strict 60+ minute Kalshi-required soak" not in gaps
    assert "Strict Polymarket live soak passed on 2026-06-18" not in gaps
    assert "yielded no normalized trades" not in gaps
    assert "Multi-hour supervised ingest soak still needs" not in gaps


def test_repo_status_renders_handoff_ready_sections():
    output = io.StringIO()
    with redirect_stdout(output):
        rc = repo_status.main()

    text = output.getvalue()
    assert rc == 0
    assert "Current posture:" in text
    assert "Next recommended focus:" in text
    assert "packet_backed_calibration_decision" in text
    assert "exported local review packet" in text
    assert "calibration decision" in text
    assert "Publish the current exact-soak" not in text
    assert "Verified proof:" in text
    assert "Residual proof gaps:" in text
    assert "High-priority commands:" in text
    assert "Strict Polymarket live soak passed on 2026-06-18" in text
    assert "raw_events=11643" in text
    assert "normalized_trades=781" in text
    assert "alerts=10" in text
    assert "unresolved_dead_letters=0" in text
    assert "open_data_quality_incidents=0" in text
    assert "raw_evidence_duration_minutes=68.9" in text
    assert "Strict Kalshi-required live soak passed on 2026-06-18" in text
    assert "kalshi raw_events=1144" in text
    assert "kalshi normalized_trades=1144" in text
    assert "kalshi raw_evidence_duration_minutes=60.862" in text
    assert "Tier-1 alert-quality review recorded 23" in text
    assert "one true-positive review row" in text
    assert "min_trade_usd=500" in text
    assert "Read-only 24h DB replay with current post-tuning rules" in text
    assert "zero volume_spike_v1 alerts below the configured 500 USD floor" in text
    assert "Publish-readiness validation passed after fetching origin on 2026-06-18" in text
    assert "branch main was clean, ahead 52 and behind 0 against origin/main" in text
    assert "Publication completed on 2026-06-18" in text
    assert "git push origin main succeeded" in text
    assert "Fresh post-publication bounded live ingest passed on 2026-06-18" in text
    assert "raw_events=2021" in text
    assert "kalshi raw_events=266" in text
    assert "Dashboard alert hardening now surfaces deterministic triage flags" in text
    assert "Exact-run soak validation now supports explicit --since and --until bounds" in text
    assert "Fresh exact bounded current-traffic soak passed on 2026-06-18" in text
    assert "raw_events=15711" in text
    assert "kalshi raw_events=167" in text
    assert "Dashboard alert filtering is now read-only and operator-facing" in text
    assert "Dashboard alert review writes are implemented as a localhost-only append-only POST" in text
    assert "POST /api/alerts/{alert_id}/review" in text
    assert "Local dashboard review endpoint smoke passed against local Postgres" in text
    assert "malformed JSON returned HTTP 400" in text
    assert "Headless and headed Chrome smoke passed on 2026-06-18" in text
    assert "Dashboard review-write hardening now rejects non-application/json POSTs" in text
    assert "rejects foreign Origin/Referer headers with HTTP 403" in text
    assert "Dashboard-origin review cross-surface smoke passed on 2026-06-18" in text
    assert "Local review-packet export is implemented" in text
    assert "pmfi alerts review-packet" in text
    assert "without overwriting existing files" in text
    assert "Review-packet DB smoke passed on 2026-06-18" in text
    assert "strict 60+ minute Kalshi-required soak" not in text
    assert "yielded no normalized trades" not in text
    assert "Live alert review queue is fully labeled" in text
    assert "Publication is complete for current local commits" in text
    assert "Review-packet export is implemented and DB-smoked" in text
    assert "there is not yet a compact local review-packet export" not in text
    assert "Publication has not been performed" not in text
    assert "Publish or remote-branch readiness is not implied" not in text
    assert "Tuned volume_spike_v1 min_trade_usd threshold still needs" not in text
    assert "One live market_relative_large_trade_v1 alert remains unreviewed" not in text
    assert "Alert quality still needs operator review of the unreviewed live Polymarket" not in text
    assert "python scripts\\task.py publish-ready --fetch" in text
    assert "python scripts\\task.py soak --window 2h" in text
    assert "python scripts\\task.py soak --since <started_at> --until <ended_at>" in text
    assert "python -m pmfi.cli alerts review-packet --since 24h" in text
    assert "M1: local postgres proof [core_proven]" in text
    assert "M10: local hardening and operator UX [continuous_hardening]" in text
    assert "M1: local postgres proof [high_priority]" not in text
    assert "ready_after_or_parallel_with_M1" not in text
    assert "ready_with_fixtures" not in text
    assert "Multi-hour supervised ingest soak still needs" not in text
