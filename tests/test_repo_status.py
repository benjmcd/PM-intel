from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from scripts import repo_status


ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "docs" / "implementation" / "02_task_graph.yaml"
CALIBRATION_PATH = ROOT / "docs" / "product" / "03_calibration.md"


def load_graph() -> dict:
    return yaml.safe_load(GRAPH_PATH.read_text(encoding="utf-8"))


def test_packet_backed_calibration_decision_is_recorded():
    text = CALIBRATION_PATH.read_text(encoding="utf-8")

    assert "Packet-backed calibration decision - 2026-06-18" in text
    assert "reviewed alerts: 24" in text
    assert "volume_spike_v1: 23 noise" in text
    assert "low_notional_thin_baseline" in text
    assert "market_relative_large_trade_v1: 1 true positive" in text
    assert "volume_spike_v1.min_trade_usd: 500" in text
    assert "zero volume_spike_v1 alerts below the configured 500 USD floor" in text
    assert "Decision: do not change alert thresholds in this slice" in text
    assert "market_relative_large_trade_v1 remains unchanged" in text
    assert "Next proof target: fresh post-calibration live or soak proof" in text
    assert "Post-calibration sample review - 2026-06-18" in text
    assert "raw_events=4075" in text
    assert "Reviewed labels: 3 true positives, 1 false positive, 1 noise" in text
    assert "directional_outcome_mismatch" in text
    assert "low_notional_thin_near_threshold" in text
    assert "do not change alert thresholds in this slice" in text
    assert "detected `dominant_side`" in text
    assert "Post-fix non-directional sample review - 2026-06-18" in text
    assert "raw_events=4717" in text
    assert "Reviewed labels: 3 true positives, 0 false positives, 0 noise" in text
    assert "pmfi alerts outcome-audit" in text
    assert "Refreshed-Kalshi strict live sample review - 2026-06-18" in text
    assert "raw_events=6047" in text
    assert "Reviewed labels: 1 true positive, 0 false positives, 9 noise" in text
    assert "fresh_kalshi_directional_cluster" in text
    assert "live_low_notional_thin_baseline" in text
    assert "do not change alert thresholds in this slice" in text


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
    assert focus["id"] == "post_strict_live_calibration_accumulation"
    assert "implementation-proven by deterministic" in focus["summary"]
    assert "natural strict live evidence" in focus["summary"]
    assert "reviewed packet accumulation" in focus["summary"]
    assert "threshold changes" in focus["summary"]
    assert "packet_backed_calibration_decision" not in focus["summary"]
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
    assert "Packet-backed calibration decision recorded on 2026-06-18" in proof
    assert "does not justify another threshold change" in proof
    assert "market_relative_large_trade_v1 remains unchanged" in proof
    assert "next proof target is fresh post-calibration runtime evidence" in proof
    assert "Fresh post-calibration exact bounded live/soak proof passed on 2026-06-18" in proof
    assert "raw_events=3445" in proof
    assert "normalized_trades=343" in proof
    assert "raw_evidence_duration_minutes=9.982" in proof
    assert "kalshi raw_events=305" in proof
    assert "polymarket raw_events=3140" in proof
    assert "this_trade_usd=1695.75" in proof
    assert "spike_multiplier=60.78" in proof
    assert "baseline_trades=20" in proof
    assert "Fresh post-calibration alert review closeout recorded f5f72655" in proof
    assert "category post_calibration_volume_spike" in proof
    assert "dry-run resolved the intended alert without writing" in proof
    assert "review_label=tp" in proof
    assert "raw_event_id=34755" in proof
    assert "Reviewed=1, FP=0, TP=1, Noise=0" in proof
    assert "review_queue.total=0" in proof
    assert "Second post-calibration exact bounded live/soak sample passed on 2026-06-18" in proof
    assert "raw_events=4075" in proof
    assert "normalized_trades=206" in proof
    assert "alerts=5" in proof
    assert "raw_evidence_duration_minutes=9.985" in proof
    assert "kalshi raw_events=178" in proof
    assert "polymarket raw_events=3897" in proof
    assert "Reviewed=5, FP=1, TP=3, Noise=1" in proof
    assert "false_positive_categories directional_outcome_mismatch=1" in proof
    assert "one volume_spike_v1 noise row at exactly 5.0x" in proof
    assert "Directional alert persistence now prefers detected dominant_side" in proof
    assert "tests/test_runner_suppression.py" in proof
    assert "27 passed" in proof
    assert "Deterministic DB-gated replay proof passed on 2026-06-18" in proof
    assert "test_persisted_directional_alert_outcome_matches_dominant_side_audit" in proof
    assert "alerts.outcome_key=no" in proof
    assert "dominant_side=no" in proof
    assert "status=match" in proof
    assert "Executable local review-pass gate is implemented on 2026-06-18" in proof
    assert "python scripts\\task.py review-pass" in proof
    assert "read-only, offline, and file-backed" in proof
    assert "default verification staying offline" in proof
    assert "DB replay window validation now fails closed on 2026-06-18" in proof
    assert "rejects malformed, naive, future, zero relative, partial relative, and inverted windows" in proof
    assert "before loading config or opening Postgres" in proof
    assert "tests/test_replay_cli_offline.py: 23 passed" in proof
    assert "Replay report artifact generation is now executable on 2026-06-18" in proof
    assert "reports/replay after successful replay" in proof
    assert "invalid DB windows still fail before report writing" in proof
    assert "Wrapper-backed local DB operator smoke passed on 2026-06-18" in proof
    assert "python scripts\\task.py report --since 7d --format json returned total=35" in proof
    assert "review_queue.total=0" in proof
    assert "reviewed_total=35" in proof
    assert "python scripts\\task.py db-replay over 2026-06-18T17:37:00+00:00" in proof
    assert "replayed 3 DB raw events" in proof
    assert "emitted 2 Kalshi alerts" in proof
    assert "reports\\replay\\20260618-191136-db-report.txt" in proof
    assert "Fresh post-fix exact bounded live/soak sample passed on 2026-06-18" in proof
    assert "raw_events=3499" in proof
    assert "normalized_trades=64" in proof
    assert "alerts=0" in proof
    assert "kalshi raw_events=56" in proof
    assert "polymarket raw_events=3443" in proof
    assert "partial post-fix runtime evidence" in proof
    assert "Local directional outcome audit is implemented through pmfi alerts outcome-audit" in proof
    assert "checked=3, matched=2, mismatches=1, ok=false" in proof
    assert "Fresh 15-minute post-fix exact bounded live/soak sample passed on 2026-06-18" in proof
    assert "raw_events=4717" in proof
    assert "normalized_trades=90" in proof
    assert "alerts=3" in proof
    assert "Exact outcome-audit for the run returned checked=0" in proof
    assert "Reviewed=3, FP=0, TP=3, Noise=0" in proof
    assert "post_fix_volume_spike" in proof
    assert "payout_notional_low_capital" in proof
    assert "Fresh 30-minute post-fix exact bounded live/soak sample passed on 2026-06-18" in proof
    assert "raw_events=10328" in proof
    assert "normalized_trades=144" in proof
    assert "alerts=2" in proof
    assert "strict mode returned ok=false with exit_code=1" in proof
    assert "Reviewed=2, FP=0, TP=2, Noise=0" in proof
    assert "post_fix_market_relative_large_trade" in proof
    assert "Strict refreshed-Kalshi exact live/soak proof passed on 2026-06-18" in proof
    assert "raw_events=6047" in proof
    assert "normalized_trades=1698" in proof
    assert "alerts=10" in proof
    assert "kalshi raw_events=1644" in proof
    assert "polymarket raw_events=4403" in proof
    assert "checked=1, matched=1, mismatches=0" in proof
    assert "stored_outcome_key=yes and dominant_side=yes" in proof
    assert "The 10-alert refreshed-Kalshi sample was fully reviewed on 2026-06-18" in proof
    assert "Reviewed=10, FP=0, TP=1, Noise=9" in proof
    assert "fresh_kalshi_directional_cluster" in proof
    assert "live_low_notional_thin_baseline" in proof
    assert "Kalshi watchlist refresh is now a repeatable operator command" in proof
    assert "pmfi markets refresh-watchlist" in proof
    assert "python scripts\\task.py refresh-watchlist" in proof
    assert "dry-runs by default" in proof
    assert "requires --sync for local Postgres writes" in proof
    gaps = "\n".join(posture["residual_proof_gaps"])
    assert "currently sampled live alert queue is labeled" in gaps
    assert "23 volume_spike_v1 noise rows" in gaps
    assert "1 directional outcome" in gaps
    assert "2 more non-directional true positives from a 30-minute run" in gaps
    assert "1 directional_cluster_v1 true positive" in gaps
    assert "9 volume_spike_v1 low_notional+thin_baseline noise rows" in gaps
    assert "1 near-threshold volume_spike_v1 noise row" in gaps
    assert "local review truth, not final threshold truth" in gaps
    assert "Publication is complete for current local commits" in gaps
    assert "Review-packet export is implemented and DB-smoked" in gaps
    assert "packet inspection and fresh post-calibration" in gaps
    assert "future threshold changes still need more reviewed post-calibration packet evidence" in gaps
    assert "directional dominant-side persistence fix is covered by focused unit tests" in gaps
    assert "clean post-fix runtime samples" in gaps
    assert "deterministic DB-gated replay proof" in gaps
    assert "Fresh strict live traffic has now produced a" in gaps
    assert "stored outcome matched evidence" in gaps
    assert "exact-window outcome-audit command" in gaps
    assert "future live sample to prove new persisted" not in gaps
    assert "Natural post-fix live traffic still has not produced" not in gaps
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
    assert "post_strict_live_calibration_accumulation" in text
    assert "implementation-proven by deterministic" in text
    assert "natural strict live evidence" in text
    assert "reviewed packet accumulation" in text
    assert "packet_backed_calibration_decision" not in text
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
    assert "Packet-backed calibration decision recorded on 2026-06-18" in text
    assert "does not justify another threshold change" in text
    assert "market_relative_large_trade_v1 remains unchanged" in text
    assert "Fresh post-calibration exact bounded live/soak proof passed on 2026-06-18" in text
    assert "raw_events=3445" in text
    assert "normalized_trades=343" in text
    assert "raw_evidence_duration_minutes=9.982" in text
    assert "kalshi raw_events=305" in text
    assert "polymarket raw_events=3140" in text
    assert "this_trade_usd=1695.75" in text
    assert "Fresh post-calibration alert review closeout recorded f5f72655" in text
    assert "post_calibration_volume_spike" in text
    assert "review_label=tp" in text
    assert "Reviewed=1, FP=0, TP=1, Noise=0" in text
    assert "review_queue.total=0" in text
    assert "Second post-calibration exact bounded live/soak sample passed on 2026-06-18" in text
    assert "raw_events=4075" in text
    assert "normalized_trades=206" in text
    assert "raw_evidence_duration_minutes=9.985" in text
    assert "Reviewed=5, FP=1, TP=3, Noise=1" in text
    assert "directional_outcome_mismatch" in text
    assert "Directional alert persistence now prefers detected dominant_side" in text
    assert "tests/test_runner_suppression.py" in text
    assert "Deterministic DB-gated replay proof passed on 2026-06-18" in text
    assert "Executable local review-pass gate is implemented on 2026-06-18" in text
    assert "DB replay window validation now fails closed on 2026-06-18" in text
    assert "implementation-proven by deterministic" in text
    assert "Fresh post-fix exact bounded live/soak sample passed on 2026-06-18" in text
    assert "raw_events=3499" in text
    assert "normalized_trades=64" in text
    assert "partial post-fix runtime evidence" in text
    assert "Local directional outcome audit is implemented through pmfi alerts outcome-audit" in text
    assert "checked=3, matched=2, mismatches=1, ok=false" in text
    assert "Fresh 15-minute post-fix exact bounded live/soak sample passed on 2026-06-18" in text
    assert "raw_events=4717" in text
    assert "normalized_trades=90" in text
    assert "Reviewed=3, FP=0, TP=3, Noise=0" in text
    assert "Strict refreshed-Kalshi exact live/soak proof passed on 2026-06-18" in text
    assert "raw_events=6047" in text
    assert "normalized_trades=1698" in text
    assert "kalshi raw_events=1644" in text
    assert "polymarket raw_events=4403" in text
    assert "checked=1, matched=1, mismatches=0" in text
    assert "The 10-alert refreshed-Kalshi sample was fully reviewed on 2026-06-18" in text
    assert "Reviewed=10, FP=0, TP=1, Noise=9" in text
    assert "Kalshi watchlist refresh is now a repeatable operator command" in text
    assert "pmfi markets refresh-watchlist" in text
    assert "python scripts\\task.py refresh-watchlist" in text
    assert "strict 60+ minute Kalshi-required soak" not in text
    assert "yielded no normalized trades" not in text
    assert "currently sampled live alert queue is labeled" in text
    assert "1 fresh post-calibration volume_spike_v1 true-positive row" in text
    assert "1 near-threshold volume_spike_v1 noise row" in text
    assert "Publication is complete for current local commits" in text
    assert "Review-packet export is implemented and DB-smoked" in text
    assert "packet inspection and fresh post-calibration" in text
    assert "clean post-fix runtime samples" in text
    assert "deterministic DB-gated replay proof" in text
    assert "exact-window outcome-audit command" in text
    assert "Fresh strict live traffic has now produced a" in text
    assert "there is not yet a compact local review-packet export" not in text
    assert "Publication has not been performed" not in text
    assert "Publish or remote-branch readiness is not implied" not in text
    assert "Tuned volume_spike_v1 min_trade_usd threshold still needs" not in text
    assert "One live market_relative_large_trade_v1 alert remains unreviewed" not in text
    assert "Alert quality still needs operator review of the unreviewed live Polymarket" not in text
    assert "python scripts\\task.py publish-ready --fetch" in text
    assert "python scripts\\task.py review-pass" in text
    assert "python scripts\\task.py db-replay --from <started_at> --to <ended_at> --limit 0 --report" in text
    assert "python scripts\\task.py soak --window 2h" in text
    assert "python scripts\\task.py soak --since <started_at> --until <ended_at>" in text
    assert "python -m pytest tests\\test_replay_backtest_db.py -q" in text
    assert "python scripts\\task.py health" in text
    assert "python scripts\\task.py report --since 7d" in text
    assert "python scripts\\task.py dead-letters --limit 20 --format json" in text
    assert "python scripts\\task.py review-packet --since 24h" in text
    assert "python scripts\\task.py outcome-audit --since <started_at> --until <ended_at> --strict" in text
    assert "python scripts\\task.py refresh-watchlist --since-minutes 30 --limit 50 --top 5 --sync --watch" in text
    assert "M1: local postgres proof [core_proven]" in text
    assert "M10: local hardening and operator UX [continuous_hardening]" in text
    assert "M1: local postgres proof [high_priority]" not in text
    assert "ready_after_or_parallel_with_M1" not in text
    assert "ready_with_fixtures" not in text
    assert "Multi-hour supervised ingest soak still needs" not in text
