from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src" / "pmfi" / "dashboard" / "static" / "index.html"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn=None):
        self.conn = conn or object()

    def acquire(self):
        return _Acquire(self.conn)


def test_dashboard_workspace_nav_keeps_core_sections_addressable():
    html = HTML_PATH.read_text(encoding="utf-8")

    targets = [
        "runtime-health",
        "recent-volume",
        "alerts-workspace",
        "calibration-workspace",
        "calibration-packets",
        "calibration-cluster-reviews",
        "calibration-decisions",
    ]
    assert 'class="workspace-nav"' in html
    assert 'aria-label="Dashboard sections"' in html
    assert 'aria-current="location"' in html
    for target in targets:
        assert f'data-section-target="{target}"' in html
        assert f'id="{target}"' in html
    assert 'const workspaceSectionIds = [' in html
    assert "function workspaceNavButtons()" in html
    assert "function updateWorkspaceNav()" in html
    assert "function initWorkspaceNav()" in html
    assert 'button.setAttribute("aria-current", "location");' in html
    assert 'target.scrollIntoView({ block: "start" });' in html
    assert 'window.addEventListener("scroll", updateWorkspaceNav, { passive: true });' in html
    assert ".workspace-nav { flex-wrap: nowrap; overflow-x: auto; }" in html
    assert html.index('id="alerts-tbl"') < html.index('id="volume-spike-calibration"')


def test_dashboard_calibration_dense_outputs_are_collapsible_without_renaming_ids():
    html = HTML_PATH.read_text(encoding="utf-8")

    expected_regions = {
        "packet-output": "Packet output",
        "packet-comparison-output": "Packet comparison",
        "packet-review-output": "Packet review",
        "packet-queue-output": "Packet queue",
        "cluster-review-list-output": "Cluster list",
        "cluster-review-output": "Cluster detail",
        "cluster-review-coverage-output": "Cluster coverage",
        "decision-list-output": "Decision list",
        "decision-output": "Decision detail",
    }

    assert ".density-details" in html
    assert ".density-detail-body" in html
    assert ".density-details summary" in html
    assert 'class="density-grid"' in html

    calibration_area = html.split('id="calibration-packets"', 1)[1].split(
        "</section>", 1
    )[0]
    for target_id, summary in expected_regions.items():
        assert f'<span class="density-summary-label">{summary}</span>' in calibration_area
        assert f'data-density-summary-for="{target_id}"' in calibration_area
        assert f'id="{target_id}"' in calibration_area
    assert calibration_area.count('class="density-details density-default-open" open') >= 3
    assert calibration_area.count('class="density-details"') >= 5


def test_dashboard_calibration_posture_routes_latest_decision_workflow():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="calibration-posture"' in html
    assert 'aria-label="Latest calibration posture"' in html
    assert 'id="calibration-posture-status"' in html
    assert 'id="calibration-posture-load-decision"' in html
    assert 'id="calibration-posture-run-coverage"' in html
    assert ".calibration-posture" in html
    assert ".posture-card" in html
    assert ".posture-action" in html
    assert "let latestCalibrationDecision = null;" in html
    assert "function decisionNextActionText(decision)" in html
    assert "function renderCalibrationPosture(decisions)" in html
    assert "latestCalibrationDecision = latest;" in html
    assert 'metricCell("latest decision", latest.decision || "-")' in html
    assert 'metricCell("readiness", readiness)' in html
    assert 'metricCell("cluster coverage", clusterText)' in html
    assert 'metricCell("next action", decisionNextActionText(latest))' in html
    assert "renderCalibrationPosture(d.decisions || []);" in html
    assert "async function loadLatestCalibrationDecision()" in html
    assert "async function runLatestDecisionCoverage()" in html
    assert 'document.getElementById("calibration-posture-load-decision").addEventListener("click", loadLatestCalibrationDecision);' in html
    assert 'document.getElementById("calibration-posture-run-coverage").addEventListener("click", runLatestDecisionCoverage);' in html
    assert "await runClusterReviewCoverage(packetNames);" in html
    assert 'document.getElementById("packet-market-cluster").value = "";' in html


def test_dashboard_preflight_detects_stale_cluster_review_api_surface():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="dashboard-preflight"' in html
    assert 'role="alert" hidden' in html
    assert 'id="dashboard-preflight-title"' in html
    assert 'id="dashboard-preflight-detail"' in html
    assert ".preflight-banner" in html
    assert ".preflight-banner[hidden]" in html
    assert "let dashboardClusterReviewRoutesAvailable = true;" in html
    assert "function setClusterReviewControlsEnabled(enabled)" in html
    assert '"cluster-review-refresh"' in html
    assert '"cluster-review-coverage-selected"' in html
    assert "function showDashboardPreflight(title, detail)" in html
    assert "function hideDashboardPreflight()" in html
    assert "async function preflightDashboardApi()" in html
    assert 'fetch("/api/dashboard-capabilities")' in html
    assert "routes.calibration_cluster_reviews === true" in html
    assert "routes.calibration_cluster_review_coverage === true" in html
    assert 'showDashboardPreflight(\n      "Dashboard process is stale",' in html
    assert 'setDensitySummary("cluster-review-coverage-output", "stale process", "error");' in html
    assert "preflightDashboardApi().then(clusterRoutesOk => {" in html
    assert "if (clusterRoutesOk) refreshClusterReviews();" in html


def test_dashboard_density_summary_hints_update_representative_paths():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert ".density-summary-hint" in html
    assert ".density-summary-error" in html
    assert "function setDensitySummary(outputId, text, kind = \"muted\")" in html
    assert 'document.querySelector(`[data-density-summary-for="${outputId}"]`)' in html
    assert "hint.textContent = text || \"idle\";" in html
    assert 'hint.className = `density-summary-hint density-summary-${kind || "muted"}`;' in html

    expected_updates = [
        'setDensitySummary("packet-output", `${packets.length} packet(s)`, "ok");',
        'setDensitySummary("packet-output", "0 packets", "muted");',
        'setDensitySummary("packet-comparison-output", `compared ${data.packet_count || 0} packet(s)`, "ok");',
        'setDensitySummary("packet-review-output", `${readiness} - removed TP ${removedTp}`, removedTp ? "warn" : "ok");',
        'setDensitySummary("packet-queue-output", `filtered ${totals.filtered_rows || 0} - returned ${totals.returned_rows || 0}`, "ok");',
        'setDensitySummary("cluster-review-list-output", `${reviews.length} review(s)`, "ok");',
        'setDensitySummary("cluster-review-output", `${assessmentLabel} - ${readiness}`, readiness === "-" ? "muted" : "ok");',
        'setDensitySummary("cluster-review-coverage-output", `${totals.covered_market_cluster_count || 0} covered - ${totals.uncovered_market_cluster_count || 0} uncovered`, "ok");',
        'setDensitySummary("decision-list-output", `${decisions.length} decision(s)`, "ok");',
        'setDensitySummary("decision-output", decision, decision === "-" ? "muted" : "ok");',
    ]
    for update in expected_updates:
        assert update in html

    for target_id in [
        "packet-output",
        "packet-comparison-output",
        "packet-review-output",
        "packet-queue-output",
        "cluster-review-list-output",
        "cluster-review-output",
        "cluster-review-coverage-output",
        "decision-list-output",
        "decision-output",
    ]:
        assert f'setDensitySummary("{target_id}", "failed", "error");' in html


def test_dashboard_calibration_state_helper_escapes_operator_detail():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert ".calibration-state {" in html
    assert ".calibration-state-title" in html
    assert ".calibration-state-detail" in html
    assert ".calibration-state-action" in html
    assert ".calibration-state-error" in html
    assert 'function renderCalibrationState(kind, title, detail = "", actions = [])' in html
    assert (
        'const safeDetail = detail ? `<p class="calibration-state-detail">${esc(detail)}</p>` : "";'
        in html
    )
    assert (
        'actions.map(action => `<span class="calibration-state-action">${esc(action)}</span>`)'
        in html
    )


def test_dashboard_calibration_empty_paths_use_state_helper():
    html = HTML_PATH.read_text(encoding="utf-8")

    expected_empty_paths = [
        'renderCalibrationState("empty", "Refresh packets", "Load ignored local packet artifacts.")',
        'renderCalibrationState("empty", "Packets loaded", "Select a packet, then load detail or compare selected packets.")',
        'renderCalibrationState("empty", "No packets found", "Run a calibration replay export first.")',
        'renderCalibrationState("empty", "Select a packet", "Choose a packet before loading details.")',
        'renderCalibrationState("empty", "No packet rows", "No packet comparison rows matched this request.")',
        'renderCalibrationState("empty", "No review rows", "No review-risk sample rows matched this request.")',
        'renderCalibrationState("empty", "No queue rows", "No packet review queue rows matched these filters.")',
        'renderCalibrationState("empty", "Refresh reviews", "Load ignored local cluster review artifacts.")',
        'renderCalibrationState("empty", "No cluster reviews", "Run a cluster review export first.")',
        'renderCalibrationState("empty", "Select a cluster review", "Choose a review artifact before loading detail.")',
        'renderCalibrationState("empty", "No coverage rows", "No queue clusters matched these filters.")',
        'renderCalibrationState("empty", "Refresh decisions", "Load ignored local decision records.")',
        'renderCalibrationState("empty", "No decisions", "Run a decision export first.")',
        'renderCalibrationState("empty", "Select a decision", "Choose a decision record before loading detail.")',
    ]
    for snippet in expected_empty_paths:
        assert snippet in html

    raw_empty_strings = [
        '<span class="muted">refresh local calibration packets</span>',
        '<span class="muted">no local calibration packets found</span>',
        '<p class="calibration-sample-empty">no packet rows to compare</p>',
        '<p class="calibration-sample-empty">no review-risk sample rows</p>',
        '<p class="calibration-sample-empty">no packet review queue rows</p>',
        '<span class="muted">refresh local cluster reviews</span>',
        '<span class="muted">no local cluster review artifacts found</span>',
        '<p class="calibration-sample-empty">no queue clusters matched coverage filters</p>',
        '<span class="muted">refresh local calibration decisions</span>',
        '<span class="muted">no local calibration decision records found</span>',
    ]
    for raw in raw_empty_strings:
        assert raw not in html


def test_dashboard_calibration_error_paths_use_state_helper_with_escaped_details():
    html = HTML_PATH.read_text(encoding="utf-8")

    expected_error_paths = [
        'renderCalibrationState("error", "Packet list failed", e.message || "packet list failed")',
        'renderCalibrationState("error", "Packet load failed", e.message || "packet load failed")',
        'renderCalibrationState("error", "Packet comparison failed", e.message || "packet comparison failed")',
        'renderCalibrationState("error", "Packet review failed", e.message || "packet review summary failed")',
        'renderCalibrationState("error", "Packet queue failed", e.message || "packet review queue failed")',
        'renderCalibrationState("error", "Cluster coverage failed", e.message || "cluster-review coverage failed")',
        'renderCalibrationState("error", "Cluster list failed", e.message || "cluster review list failed")',
        'renderCalibrationState("error", "Cluster load failed", e.message || "cluster review load failed")',
        'renderCalibrationState("error", "Decision list failed", e.message || "decision list failed")',
        'renderCalibrationState("error", "Decision load failed", e.message || "decision load failed")',
        'renderCalibrationState("error", "Calibration failed", e.message || "calibration failed")',
    ]
    for snippet in expected_error_paths:
        assert snippet in html

    assert '<span class="err">${esc(e.message ||' not in html


def test_alerts_table_keeps_triage_flags_column_contract():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "PMFI operator cockpit" in html
    assert 'id="alert-summary"' in html
    assert 'data-quick-filter="unreviewed"' in html
    assert 'data-quick-filter="low_notional"' in html
    assert 'data-quick-filter="high"' not in html
    assert "<th>Flags</th>" in html
    assert 'id="alerts-review-state"' in html
    assert 'id="alerts-review-label"' in html
    assert 'id="alerts-rule-key"' in html
    assert 'aria-label="Global review state filter"' in html
    assert 'aria-label="Global review label filter"' in html
    assert 'aria-label="Global rule filter"' in html
    assert "value=\"unreviewed\"" in html
    assert "value=\"reviewed\"" in html
    assert 'value="volume_spike_v1"' in html
    assert 'id="alerts-triage-flags"' in html
    assert 'id="raw-lookup-panel"' in html
    assert 'aria-live="polite" hidden' in html
    assert 'name="triage_flag" value="low_notional"' in html
    assert 'name="triage_flag" value="thin_baseline"' in html
    assert 'name="triage_flag" value="near_threshold"' in html
    assert 'name="triage_flag" value="degraded_data_quality"' in html
    assert 'name="triage_flag" value="missing_lineage"' in html
    assert "function buildAlertsUrl()" in html
    assert 'params.append("triage_flag", cb.value);' in html
    assert 'params.set("rule_key", ruleKey)' in html
    assert 'params.set("review_state", reviewState)' in html
    assert 'params.set("review_label", reviewLabel)' in html
    assert 'replace(/"/g,"&quot;")' in html
    assert "replace(/'/g,\"&#39;\")" in html
    assert "function flagsCell(a)" in html
    assert 'td("Flags", flagsCell(a)' in html
    assert "function evidenceCell(a)" in html
    assert "a.evidence_facts || []" in html
    assert 'class="evidence-facts"' in html
    assert 'class="evidence-chip"' in html
    assert 'td("Evidence", evidenceCell(a)' in html
    assert 'esc(f.label)' in html
    assert 'esc(f.value)' in html
    assert "function td(label, content, attrs = \"\")" in html
    assert "data-label=" in html
    assert "function compactId(s)" in html
    assert "function copyButton(value, label, title, extraClass = \"\")" in html
    assert 'data-copy="${esc(value)}"' in html
    assert "function lineageChip(label, value)" in html
    assert "function rawLookupButton(value)" in html
    assert "function idCell(a)" in html
    assert 'aria-label="Alert lineage"' in html
    assert 'lineageChip("raw_event_id", a.raw_event_id)' in html
    assert "rawLookupButton(a.raw_event_id)" in html
    assert 'class="id-copy raw-lookup-button"' in html
    assert 'data-raw-event-id="${esc(value)}"' in html
    assert 'lineageChip("trade_id", a.trade_id)' in html
    assert 'copyButton(alertId, shortId(alertId), "Copy full alert ID")' in html
    assert 'td("ID", idCell(a)' in html
    assert "lineage-missing" in html
    assert "@media (max-width: 900px)" in html
    assert "function updateAlertSummary(rows)" in html
    assert 'card.addEventListener("click", () => setQuickFilter(card.dataset.quickFilter));' in html
    assert 'class="review-box"' in html
    assert "Record review" in html
    assert "<summary>${esc(reviewActionSummary(a))}</summary>" in html
    assert "Append review" in html
    assert 'class="review-actions"' in html
    assert 'data-review-alert-id' in html
    assert 'data-review-default-label="${esc(defaultLabel)}"' in html
    assert 'const alertActionLabel = shortId(a.alert_id || "") || "this alert";' in html
    assert 'aria-label="Review label for ${esc(alertActionLabel)}"' in html
    assert 'aria-label="Human reviewer for ${esc(alertActionLabel)}"' in html
    assert 'placeholder="operator" autocomplete="off"' in html
    assert '.review-actions input[name="notes"] { grid-column: 1 / -1; }' in html
    assert 'class="review-meta"' in html
    assert 'class="review-meta-label">at ${esc(a.reviewed_at.replace("T"," ").slice(0,19))}</span>' in html
    assert 'class="review-meta-label">by ${esc(a.reviewed_by)}</span>' in html
    assert 'class="review-meta-label review-notes">notes: ${esc(a.review_notes)}</span>' in html
    assert "function defaultReviewLabel(a)" in html
    assert "function reviewOption(a, label)" in html
    assert "function reviewActionSummary(a)" in html
    assert 'if (a.is_reviewed) return current;' not in html
    assert "function submitAlertReview(" in html
    assert "function rawLookupMetric(label, value)" in html
    assert "function renderAlertRawLookup(result)" in html
    assert "function setRawLookupMessage(className, message)" in html
    assert "async function loadAlertRawEvent(button)" in html
    assert 'fetch(`/api/raw-events/${encodeURIComponent(rawEventId)}`)' in html
    assert 'setRawLookupMessage("muted", "loading raw event...")' in html
    assert 'setRawLookupMessage("err", e.message || "raw lookup failed")' in html
    assert 'event.target.closest(".raw-lookup-button")' in html
    assert 'await loadAlertRawEvent(rawLookup);' in html
    assert 'event.target.closest(".raw-lookup-close")' in html
    assert 'rawPayloadBlock(row)' in html
    assert 'fetch(`/api/alerts/${encodeURIComponent(alertId)}/review`' in html
    assert "const reviewDrafts = new Map();" in html
    assert "function currentReviewDraft(form)" in html
    assert "function captureReviewDrafts()" in html
    assert "function restoreReviewDrafts()" in html
    assert "function hasDirtyReviewDraft()" in html
    assert "defaultLabel: form.dataset.reviewDefaultLabel || \"tp\"" in html
    assert "draft.label !== draft.defaultLabel" in html
    assert "reviewDrafts.delete(alertId);" in html
    assert "captureReviewDrafts();" in html
    assert "restoreReviewDrafts();" in html
    assert 'pollAlerts({ skipWhenReviewDraftDirty: true })' in html
    assert "review draft active - auto refresh paused" in html
    assert 'colspan="11" class="muted">waiting for alerts' in html
    assert 'colspan="11" class="muted">no recent alerts' in html
    assert 'id="alert-comparison"' in html
    assert 'aria-label="Visible alert comparison"' in html
    assert "function updateAlertComparison(rows)" in html
    assert "renderComparisonGroup(" in html
    assert 'updateAlertComparison(rows);' in html
    assert 'no alerts in current view' in html
    assert 'id="volume-spike-calibration"' in html
    assert 'id="calibration-from"' in html
    assert 'id="calibration-to"' in html
    assert 'id="calibration-venue"' in html
    assert 'id="calibration-market"' in html
    assert 'id="calibration-low-notional-min-baseline-trades"' in html
    assert 'id="calibration-low-notional-min-baseline-median-usd"' in html
    assert 'id="calibration-low-notional-max-spike-multiplier"' in html
    assert 'id="calibration-low-notional-threshold-usd"' in html
    assert 'id="calibration-min-trade-usd"' in html
    assert 'id="calibration-details-limit"' in html
    assert 'id="calibration-cold-start"' in html
    assert 'id="calibration-run"' in html
    assert 'id="calibration-output"' in html
    assert "function buildVolumeSpikeCalibrationUrl()" in html
    assert '["low_notional_min_baseline_median_usd", "calibration-low-notional-min-baseline-median-usd"]' in html
    assert '["low_notional_max_spike_multiplier", "calibration-low-notional-max-spike-multiplier"]' in html
    assert '["low_notional_threshold_usd", "calibration-low-notional-threshold-usd"]' in html
    assert '["details_limit", "calibration-details-limit"]' in html
    assert '"/api/volume-spike-calibration?"' in html
    assert "function renderCalibrationDeltaSamples(comparison)" in html
    assert "comparison.removed_volume_spike_samples || []" in html
    assert "comparison.added_volume_spike_samples || []" in html
    assert "function renderCalibrationSampleRows(rows, state)" in html
    assert 'aria-label="${esc(state)} volume spike sample rows"' in html
    assert "calibrationReviewText(row.review)" in html
    assert "renderCalibrationDeltaSamples(comparison)," in html
    assert "function runVolumeSpikeCalibration()" in html
    assert 'addEventListener("click", runVolumeSpikeCalibration)' in html
    assert "pollHealth(); pollVolume(); pollAlerts();" in html
    assert "runVolumeSpikeCalibration();" not in html
    assert 'id="calibration-packets"' in html
    assert 'aria-label="Local calibration packet browser"' in html
    assert 'id="packet-select"' in html
    assert '<select id="packet-select" multiple size="6">' in html
    assert 'id="packet-refresh"' in html
    assert 'id="packet-load"' in html
    assert 'id="packet-compare"' in html
    assert 'id="packet-compare-selected"' in html
    assert 'id="packet-review"' in html
    assert 'id="packet-review-selected"' in html
    assert 'id="packet-queue"' in html
    assert 'id="packet-queue-selected"' in html
    assert 'id="packet-output"' in html
    assert 'id="packet-comparison-output"' in html
    assert 'id="packet-review-output"' in html
    assert 'id="packet-queue-output"' in html
    assert 'id="calibration-decisions"' in html
    assert 'aria-label="Local calibration decision history"' in html
    assert 'id="decision-select"' in html
    assert 'id="decision-refresh"' in html
    assert 'id="decision-load"' in html
    assert 'id="decision-list-output"' in html
    assert 'id="decision-output"' in html
    assert 'id="calibration-cluster-reviews"' in html
    assert 'aria-label="Local calibration cluster reviews"' in html
    assert 'id="cluster-review-select"' in html
    assert 'id="cluster-review-refresh"' in html
    assert 'id="cluster-review-load"' in html
    assert 'id="cluster-review-coverage"' in html
    assert 'id="cluster-review-coverage-selected"' in html
    assert 'id="cluster-review-list-output"' in html
    assert 'id="cluster-review-output"' in html
    assert 'id="cluster-review-coverage-output"' in html
    assert "function refreshCalibrationPackets()" in html
    assert "function loadCalibrationPacket()" in html
    assert "function compareCalibrationPackets()" in html
    assert "function compareSelectedCalibrationPackets()" in html
    assert "function reviewCalibrationPackets()" in html
    assert "function reviewSelectedCalibrationPackets()" in html
    assert "function reviewQueueCalibrationPackets()" in html
    assert "function reviewQueueSelectedCalibrationPackets()" in html
    assert "function renderPacketReviewQueue(data)" in html
    assert "function renderPacketReviewQueueClusters(clusters)" in html
    assert "function renderPacketReviewQueueRows(rows)" in html
    assert 'rawLookupButton(row.raw_event_id)' in html
    assert 'const rawLookup = event.target.closest(".raw-lookup-button");' in html
    assert "await loadAlertRawEvent(rawLookup);" in html
    assert 'aria-label="Calibration packet market cluster rows"' in html
    assert "function selectedPacketNames()" in html
    assert "Array.from(select.selectedOptions || [])" in html
    assert "function buildCalibrationPacketSelectionUrl(path, names = selectedPacketNames(), extraParams = {})" in html
    assert "function buildCalibrationReviewQueueUrl(names = selectedPacketNames())" in html
    assert 'state: "removed", review_group: "unmatched_replay_only"' in html
    assert "if (!names.length && !extraEntries.length) return path;" in html
    assert 'params.append("name", name);' in html
    assert "return `${path}?${params.toString()}`;" in html
    assert "function renderPacketReviewSummary(data)" in html
    assert "function renderPacketReviewRows(rows)" in html
    assert "function refreshCalibrationDecisions()" in html
    assert "function loadCalibrationDecision()" in html
    assert "function renderDecisionRecord(record)" in html
    assert "function renderDecisionRows(decisions)" in html
    assert "function refreshClusterReviews()" in html
    assert 'function loadClusterReview(nameOverride = "")' in html
    assert "function renderClusterReviewRecord(record)" in html
    assert "function renderClusterReviewRows(reviews)" in html
    assert "function renderClusterReviewRawRows(rows)" in html
    assert "function buildClusterReviewCoverageUrl(names = selectedPacketNames())" in html
    assert "function renderClusterReviewCoverage(data)" in html
    assert "function renderClusterReviewCoverageRows(clusters)" in html
    assert "function runClusterReviewCoverage(names = [])" in html
    assert "function runClusterReviewCoverageAll()" in html
    assert "function runClusterReviewCoverageSelected()" in html
    assert "function rawLookupText(row)" in html
    assert "function rawLookupProfileText(row)" in html
    assert "function rawProfileChip(label, value)" in html
    assert "function signalText(values)" in html
    assert "function readinessChip(label, value, extraClass = \"\")" in html
    assert "function readinessBlock(row)" in html
    assert "function rawLookupProfileBlock(row)" in html
    assert "function clusterKeyCell(value)" in html
    assert "function counterText(counts)" in html
    assert ".cluster-key" in html
    assert ".raw-profile" in html
    assert ".raw-profile-line" in html
    assert ".raw-profile-chip" in html
    assert ".raw-profile-value" in html
    assert ".review-readiness" in html
    assert ".review-readiness-chip" in html
    assert "function clusterReviewSafeguardText(review)" in html
    assert "function clusterReviewText(row)" in html
    assert "function decisionClusterReviewCountsText(row)" in html
    assert "row.decision_readiness" in html
    assert "row.review_recommendation" in html
    assert "row.cluster_review_covered_clusters" in html
    assert "row.cluster_review_next_action_counts" in html
    assert 'metricCell("decision readiness"' in html
    assert 'metricCell("review recommendation"' in html
    assert 'metricCell("cluster coverage"' in html
    assert 'metricCell("cluster assessments"' in html
    assert 'metricCell("cluster readiness"' in html
    assert 'metricCell("cluster next actions"' in html
    assert 'metricCell("cluster payload status"' in html
    assert '["removed_reviewed_tp"]' in html
    assert 'metricCell("added records"' in html
    assert 'metricCell("added TP risk"' in html
    assert 'metricCell("unmatched added"' in html
    assert "function renderCalibrationPacket(packet)" in html
    assert "function renderPacketComparison(data)" in html
    assert "function renderPacketComparisonRows(rows)" in html
    assert 'fetch("/api/calibration-packets")' in html
    assert 'fetch(`/api/calibration-packets/${encodeURIComponent(name)}`)' in html
    assert 'fetch("/api/calibration-packets/compare")' in html
    assert 'fetch(buildCalibrationPacketSelectionUrl("/api/calibration-packets/compare", selected))' in html
    assert 'fetch("/api/calibration-packets/review-summary")' in html
    assert 'fetch(buildCalibrationPacketSelectionUrl("/api/calibration-packets/review-summary", selected))' in html
    assert "fetch(buildCalibrationReviewQueueUrl([]))" in html
    assert "fetch(buildCalibrationReviewQueueUrl(selected))" in html
    assert 'id="packet-market-cluster"' in html
    assert "params.market_cluster = marketCluster" in html
    assert "renderPacketReviewQueueClusters(data.market_clusters || [])" in html
    assert "const cluster = row.market_cluster || row.market || row.venue_market_id || \"-\";" in html
    assert 'cluster: ${esc(cluster)}' in html
    assert "<h3>Market clusters</h3>" in html
    assert "<h3>Review queue rows</h3>" in html
    assert 'fetch("/api/calibration-decisions")' in html
    assert 'fetch(`/api/calibration-decisions/${encodeURIComponent(name)}`)' in html
    assert 'fetch("/api/calibration-cluster-reviews")' in html
    assert 'fetch(`/api/calibration-cluster-reviews/${encodeURIComponent(name)}`)' in html
    assert '"/api/calibration-cluster-reviews/coverage"' in html
    assert 'aria-label="Calibration cluster review coverage rows"' in html
    assert 'metricCell("queue clusters"' in html
    assert 'metricCell("covered"' in html
    assert 'metricCell("uncovered"' in html
    assert 'metricCell("assessment counts"' in html
    assert 'metricCell("readiness counts"' in html
    assert 'metricCell("next actions"' in html
    assert 'metricCell("signal counts"' in html
    assert 'metricCell("payload status"' in html
    assert 'function clusterReviewLoadButton(name)' in html
    assert 'class="packet-load cluster-review-load-artifact"' in html
    assert 'data-cluster-review-name="${attr(name)}"' in html
    assert 'const latestReviewCell = latest.name' in html
    assert 'td("Latest review", latestReviewCell)' in html
    assert 'td("Assessment", esc(latest.assessment || "-"))' in html
    assert 'td("Readiness", readinessBlock(latest), \'class="cluster-review-profile-cell"\')' in html
    assert 'td("Raw lookup", esc(rawLookupText(latest)))' in html
    assert 'td("Raw lookup profile", rawLookupProfileBlock(latest), \'class="cluster-review-profile-cell"\')' in html
    assert 'td("Missing raw events", esc(row.missing_raw_event_id_count == null ? "-" : row.missing_raw_event_id_count))' in html
    assert 'rawLookupProfileBlock(summary)' in html
    assert 'const readiness = summary.calibration_candidate_readiness || "-";' in html
    assert 'metricCell("readiness", readiness)' in html
    assert 'metricCell("next action", summary.calibration_candidate_next_action || "-")' in html
    assert 'metricCell("blockers", signalText(summary.calibration_candidate_blockers || []))' in html
    assert 'metricCell("side counts", counterText(summary.raw_event_lookup_directional_side_counts || {}))' in html
    assert 'td("Cluster", clusterKeyCell(row.market_cluster || "-"), \'class="cluster-review-key-cell"\')' in html
    assert 'td("Cluster", clusterKeyCell(row.market_key || "-"), \'class="cluster-review-key-cell"\')' in html
    assert "raw_event_lookup_capital_at_risk_usd_min" in html
    assert "raw_event_lookup_payload_status" in html
    assert "calibration_candidate_next_action" in html
    assert "comparison.removed_volume_spike_records || []" in html
    assert "comparison.added_volume_spike_records || []" in html
    assert 'addEventListener("click", refreshCalibrationPackets)' in html
    assert 'addEventListener("click", loadCalibrationPacket)' in html
    assert 'addEventListener("click", compareCalibrationPackets)' in html
    assert 'addEventListener("click", compareSelectedCalibrationPackets)' in html
    assert 'addEventListener("click", reviewCalibrationPackets)' in html
    assert 'addEventListener("click", reviewSelectedCalibrationPackets)' in html
    assert 'addEventListener("click", reviewQueueCalibrationPackets)' in html
    assert 'addEventListener("click", reviewQueueSelectedCalibrationPackets)' in html
    assert 'addEventListener("click", refreshCalibrationDecisions)' in html
    assert 'addEventListener("click", loadCalibrationDecision)' in html
    assert 'addEventListener("click", refreshClusterReviews)' in html
    assert 'addEventListener("click", () => loadClusterReview())' in html
    assert 'addEventListener("click", runClusterReviewCoverageAll)' in html
    assert 'addEventListener("click", runClusterReviewCoverageSelected)' in html
    assert 'event.target.closest(".cluster-review-load-artifact")' in html
    assert 'await loadClusterReview(btn.dataset.clusterReviewName || "");' in html
    assert "refreshCalibrationPackets();" in html
    assert "refreshCalibrationDecisions();" in html
    assert "refreshClusterReviews();" in html


def test_dashboard_calibration_review_handlers_do_not_capture_event_objects():
    html = HTML_PATH.read_text(encoding="utf-8")
    packet_review_body = html.split("function renderPacketReviewSummary(data)", 1)[1].split(
        "function packetQueueGroupText", 1
    )[0]
    cluster_load_body = html.split('async function loadClusterReview(nameOverride = "")', 1)[
        1
    ].split("function renderDecisionRows(decisions)", 1)[0]

    assert 'metricCell("readiness", readiness)' in packet_review_body
    assert "summary.calibration_candidate_readiness" not in packet_review_body
    assert 'const override = typeof nameOverride === "string" ? nameOverride : "";' in cluster_load_body
    assert "const name = override || select.value;" in cluster_load_body
    assert "if (override) select.value = name;" in cluster_load_body


def test_cluster_review_raw_rows_render_payload_preview_and_full_payload_safely():
    html = HTML_PATH.read_text(encoding="utf-8")
    raw_rows_body = html.split("function renderClusterReviewRawRows(rows)", 1)[1].split(
        "function renderClusterReviewRecord(record)", 1
    )[0]

    assert "function rawPayloadText(value)" in raw_rows_body
    assert "function rawPayloadBlock(row)" in raw_rows_body
    assert ".raw-payload-cell" in html
    assert ".raw-payload-block" in html
    assert ".raw-payload-preview" in html
    assert ".raw-payload-details" in html
    assert ".raw-payload-pre" in html
    assert "white-space: pre-wrap;" in html
    assert "overflow-wrap: anywhere;" in html
    assert "word-break: break-word;" in html
    assert "max-height: 240px;" in html
    assert "<th>Payload</th>" in raw_rows_body
    assert 'td("Payload", rawPayloadBlock(row), \'class="raw-payload-cell"\')' in raw_rows_body
    assert "row.payload_preview" in raw_rows_body
    assert "row.payload" in raw_rows_body
    assert "JSON.stringify(value, null, 2)" in raw_rows_body
    assert '<pre class="raw-payload-preview">${esc(visiblePreview)}</pre>' in raw_rows_body
    assert (
        '<details class="raw-payload-details"><summary>Full payload</summary>'
        '<pre class="raw-payload-pre">${esc(full)}</pre></details>'
    ) in raw_rows_body

    assert 'td("Raw event", esc(row.raw_event_id || "-"))' in raw_rows_body
    assert 'td("Market", `${esc(row.venue_market_id || "-")}' in raw_rows_body
    assert 'td("Source", `${esc(row.source_event_id || "-")}' in raw_rows_body
    assert 'td("Exchange", esc(row.exchange_ts || row.received_at || "-"))' in raw_rows_body
    assert 'td("Trade", `${esc(trade.outcome_key || "-")}' in raw_rows_body


def test_packet_cluster_rows_can_filter_queue_from_safe_data_attribute():
    html = HTML_PATH.read_text(encoding="utf-8")
    attr_body = html.split("function attr(s)", 1)[1].split("function shortId", 1)[0]

    assert "function attr(s)" in html
    assert 'if (s == null || s === "") return "";' in attr_body
    assert 'replace(/&/g,"&amp;")' in attr_body
    assert 'replace(/"/g,"&quot;")' in attr_body
    assert "replace(/'/g,\"&#39;\")" in attr_body
    assert 'replace(/</g,"&lt;")' in attr_body
    assert 'replace(/>/g,"&gt;")' in attr_body
    assert '<th>Use</th>' in html
    assert 'class="packet-load packet-cluster-filter"' in html
    assert 'data-packet-market-cluster="${attr(cluster.market_key || "")}"' in html
    assert 'event.target.closest(".packet-cluster-filter")' in html
    assert 'document.getElementById("packet-market-cluster").value = btn.dataset.packetMarketCluster || "";' in html
    assert "await reviewQueueCalibrationPackets();" in html


def test_packet_review_queue_focus_strip_summarizes_filters_and_reuses_cluster_filter_buttons():
    html = HTML_PATH.read_text(encoding="utf-8")
    focus_body = html.split("function renderPacketReviewQueueFocusStrip(data)", 1)[1].split(
        "function renderPacketReviewQueueClusters(clusters)", 1
    )[0]
    queue_body = html.split("function renderPacketReviewQueue(data)", 1)[1].split(
        "async function refreshCalibrationPackets()", 1
    )[0]

    assert ".packet-focus-strip" in html
    assert ".packet-focus-summary" in html
    assert ".packet-focus-clusters" in html
    assert ".packet-focus-button" in html
    assert ".packet-focus-clear" in html
    assert "function packetFocusChip(label, value)" in html
    assert "function packetQueuePostureText(totals)" in html
    assert 'aria-label="Calibration packet review queue focus"' in focus_body
    assert "const filters = data.filters || {};" in focus_body
    assert "const totals = data.totals || {};" in focus_body
    assert 'packetFocusChip("state", filters.state || "all")' in focus_body
    assert 'packetFocusChip("review_group", filters.review_group || "all")' in focus_body
    assert 'packetFocusChip("market_cluster", filters.market_cluster || "all")' in focus_body
    assert 'packetFocusChip("rows", packetQueuePostureText(totals))' in focus_body
    assert "(data.market_clusters || []).slice(0, 8)" in focus_body
    assert 'class="packet-load packet-cluster-filter packet-focus-button"' in focus_body
    assert 'data-packet-market-cluster="${attr(cluster.market_key || "")}"' in focus_body
    assert 'filters.market_cluster ? `<button type="button" class="packet-load packet-cluster-filter packet-focus-clear" data-packet-market-cluster="">Clear cluster</button>` : ""' in focus_body
    assert "renderPacketReviewQueueFocusStrip(data)" in queue_body
    assert queue_body.index("renderPacketReviewQueueFocusStrip(data)") < queue_body.index(
        "<h3>Market clusters</h3>"
    )


class _Query:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getall(self, key, default=None):
        return self.data.get(key, default if default is not None else [])


def test_api_alerts_query_parser_builds_filters_and_rejects_conflicts():
    from pmfi.dashboard.server import _parse_alerts_query

    q = _Query({
        "review_state": "reviewed",
        "review_label": "tp",
        "rule_key": "momentum_v1",
        "triage_flag": ["low_notional,thin_baseline", "near_threshold"],
        "limit": "25",
    })
    params = _parse_alerts_query(q)
    assert params["review_state"] == "reviewed"
    assert params["review_label"] == "tp"
    assert params["rule_key"] == "momentum_v1"
    assert params["limit"] == 25
    assert params["triage_flags_filter"] == [
        "low_notional",
        "thin_baseline",
        "near_threshold",
    ]

    bad = _Query({
        "review_state": "unreviewed",
        "review_label": "tp",
    })
    try:
        _parse_alerts_query(bad)
    except ValueError as exc:
        assert "unreviewed" in str(exc)
    else:
        raise AssertionError("Expected invalid query conflict to raise ValueError")

    try:
        _parse_alerts_query(_Query({"rule_key": "bad_rule"}))
    except ValueError as exc:
        assert "invalid rule_key" in str(exc)
    else:
        raise AssertionError("Expected invalid rule_key to raise ValueError")


async def test_dashboard_alerts_route_forwards_rule_filter_and_rejects_invalid_rule(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_recent_alerts(
        conn,
        *,
        limit,
        review_state,
        review_label,
        triage_flags_filter,
        rule_key,
    ):
        calls.append({
            "conn": conn,
            "limit": limit,
            "review_state": review_state,
            "review_label": review_label,
            "triage_flags_filter": list(triage_flags_filter),
            "rule_key": rule_key,
        })
        return []

    monkeypatch.setattr("pmfi.dashboard.queries.recent_alerts", fake_recent_alerts)
    client = TestClient(TestServer(_create_dashboard_app(_Pool(conn="fake-conn"))))
    await client.start_server()
    try:
        ok = await client.get(
            "/api/alerts?rule_key=momentum_v1&review_state=reviewed&triage_flag=low_notional&limit=7"
        )
        bad = await client.get("/api/alerts?rule_key=bad_rule")
        ok_body = await ok.json()
        bad_body = await bad.json()
    finally:
        await client.close()

    assert ok.status == 200
    assert ok_body["alerts"] == []
    assert calls == [{
        "conn": "fake-conn",
        "limit": 7,
        "review_state": "reviewed",
        "review_label": None,
        "triage_flags_filter": ["low_notional"],
        "rule_key": "momentum_v1",
    }]
    assert bad.status == 400
    assert bad_body["error"] == "invalid query"


async def test_dashboard_volume_route_allows_30_day_operator_window(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_volume_timeseries(conn, *, lookback_minutes):
        calls.append({"conn": conn, "lookback_minutes": lookback_minutes})
        return [{
            "venue_code": "kalshi",
            "window_start": "2026-06-19T04:10:00+00:00",
            "trades": 2053,
            "volume_usd": 75738.94,
        }]

    monkeypatch.setattr("pmfi.dashboard.queries.volume_timeseries", fake_volume_timeseries)
    client = TestClient(TestServer(_create_dashboard_app(_Pool(conn="fake-volume-conn"))))
    await client.start_server()
    try:
        ok = await client.get("/api/volume?minutes=43200")
        over_cap = await client.get("/api/volume?minutes=999999")
        invalid = await client.get("/api/volume?minutes=not-a-number")
        ok_body = await ok.json()
        over_cap_body = await over_cap.json()
        invalid_body = await invalid.json()
    finally:
        await client.close()

    assert ok.status == 200
    assert ok_body["minutes"] == 43200
    assert ok_body["buckets"][0]["trades"] == 2053
    assert over_cap_body["minutes"] == 43200
    assert invalid_body["minutes"] == 60
    assert calls == [
        {"conn": "fake-volume-conn", "lookback_minutes": 43200},
        {"conn": "fake-volume-conn", "lookback_minutes": 43200},
        {"conn": "fake-volume-conn", "lookback_minutes": 60},
    ]


async def test_dashboard_persistence_health_route_returns_operator_snapshot(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    calls = []

    async def fake_persistence_health(conn):
        calls.append(conn)
        return {
            "venues": [{
                "venue_code": "polymarket",
                "last_persisted_at": "2026-06-20T12:00:00+00:00",
                "last_persisted_age_s": 7,
                "trades_5m": 3,
                "trades_1h": 25,
            }],
            "unresolved_dead_letters_1h": 1,
        }

    monkeypatch.setattr("pmfi.dashboard.queries.persistence_health", fake_persistence_health)
    client = TestClient(TestServer(_create_dashboard_app(_Pool(conn="fake-persist-conn"))))
    await client.start_server()
    try:
        response = await client.get("/api/persistence-health")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert calls == ["fake-persist-conn"]
    assert body["persistence"]["venues"][0]["venue_code"] == "polymarket"
    assert body["persistence"]["unresolved_dead_letters_1h"] == 1
    assert "generated_at" in body


def test_api_volume_spike_calibration_query_parser_requires_explicit_candidate():
    from pmfi.dashboard.server import _parse_volume_spike_calibration_query

    parsed = _parse_volume_spike_calibration_query(_Query({
        "from": "2026-06-18T16:00:00+00:00",
        "to": "2026-06-18T17:00:00+00:00",
        "limit": "0",
        "venue": "kalshi",
        "market": "KXBTCD-26JUN1817-T63749.99",
        "low_notional_min_baseline_trades": "50",
        "low_notional_min_baseline_median_usd": "20",
        "low_notional_max_spike_multiplier": "24",
        "low_notional_threshold_usd": "1000",
        "min_trade_usd": "1000",
        "details_limit": "2",
        "cold_start": "true",
    }))

    assert parsed["since_dt"].isoformat() == "2026-06-18T16:00:00+00:00"
    assert parsed["until_dt"].isoformat() == "2026-06-18T17:00:00+00:00"
    assert parsed["limit"] == 0
    assert parsed["venue"] == "kalshi"
    assert parsed["market"] == "KXBTCD-26JUN1817-T63749.99"
    assert parsed["candidate"].min_trade_usd is not None
    assert parsed["candidate"].low_notional_min_baseline_trades == 50
    assert parsed["candidate"].low_notional_min_baseline_median_usd == Decimal("20")
    assert parsed["candidate"].low_notional_max_spike_multiplier == Decimal("24")
    assert parsed["candidate"].low_notional_threshold_usd == Decimal("1000")
    assert parsed["details_limit"] == 2
    assert parsed["cold_start"] is True

    for query, detail in [
        ({"from": "2026-06-18T16:00:00+00:00"}, "candidate"),
        ({"from": "2026-06-18T16:00:00"}, "timezone"),
        ({"from": "bad", "min_trade_usd": "1000"}, "from"),
        ({"from": "2026-06-18T17:00:00+00:00", "to": "2026-06-18T16:00:00+00:00", "min_trade_usd": "1000"}, "before"),
        ({"from": "2026-06-18T16:00:00+00:00", "limit": "-1", "min_trade_usd": "1000"}, "limit"),
        ({"from": "2026-06-18T16:00:00+00:00", "details_limit": "51", "min_trade_usd": "1000"}, "details_limit"),
        ({"from": "2026-06-18T16:00:00+00:00", "min_trade_usd": "0"}, "min_trade_usd"),
        ({"from": "2026-06-18T16:00:00+00:00", "low_notional_max_spike_multiplier": "0"}, "low_notional_max_spike_multiplier"),
    ]:
        try:
            _parse_volume_spike_calibration_query(_Query(query))
        except ValueError as exc:
            assert detail in str(exc)
        else:
            raise AssertionError(f"Expected invalid calibration query for {query!r}")


async def test_calibration_packets_route_lists_direct_json_newest_first(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    older = packet_root / "older.json"
    newer = packet_root / "newer.json"
    ignored = packet_root / "ignored.txt"
    nested_dir = packet_root / "nested"
    nested_dir.mkdir()
    nested = nested_dir / "nested.json"
    older.write_text('{"name":"older"}', encoding="utf-8")
    newer.write_text('{"name":"newer"}', encoding="utf-8")
    ignored.write_text("ignore me", encoding="utf-8")
    nested.write_text('{"name":"nested"}', encoding="utf-8")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_100, 1_700_000_100))

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-packets")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert [packet["name"] for packet in body["packets"]] == ["newer.json", "older.json"]
    assert body["packets"][0]["size_bytes"] == newer.stat().st_size
    assert body["packets"][0]["modified_at"].startswith("2023-11-14T22:15:00")
    assert "generated_at" in body


async def test_calibration_packets_route_lists_empty_when_directory_missing(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: tmp_path / "missing")
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-packets")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["packets"] == []
    assert "generated_at" in body


async def test_calibration_packet_route_loads_parsed_json(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    packet = {
        "schema_version": "volume_spike_calibration_packet.v1",
        "local_only": True,
        "sample": [{"raw_event_id": 101}],
    }
    (packet_root / "candidate.json").write_text(json.dumps(packet), encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-packets/candidate.json")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "volume_spike_calibration_packet.v1"
    assert body["local_only"] is True
    assert body["sample"] == [{"raw_event_id": 101}]
    assert "generated_at" in body


async def test_calibration_packet_route_rejects_unsafe_names(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: tmp_path)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        for name in ["%2E%2E%2Fsecret.json", "nested%2Fpacket.json", "nested%5Cpacket.json", "packet.txt"]:
            response = await client.get(f"/api/calibration-packets/{name}")
            body = await response.json()
            assert response.status == 400
            assert body["error"] == "invalid packet name"
    finally:
        await client.close()


async def test_calibration_packet_route_maps_missing_and_invalid_json(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    (packet_root / "bad.json").write_text("{bad", encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        missing = await client.get("/api/calibration-packets/missing.json")
        missing_body = await missing.json()
        invalid = await client.get("/api/calibration-packets/bad.json")
        invalid_body = await invalid.json()
    finally:
        await client.close()

    assert missing.status == 404
    assert missing_body["error"] == "not found"
    assert invalid.status == 422
    assert invalid_body["error"] == "invalid packet json"


async def test_calibration_packet_compare_route_aggregates_all_or_selected_packets(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    first = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_trades": 50},
            "filters": {"since": "2026-06-18T16:00:00+00:00", "until": "2026-06-18T16:10:00+00:00"},
        },
        "calibration_summary": {
            "current": {"volume_spike_alerts": 3},
            "candidate_replay": {"volume_spike_alerts": 1},
            "comparison": {
                "volume_spike_delta": -2,
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 101,
                        "review": {
                            "matched": True,
                            "label": "noise",
                            "category": "low_notional",
                        },
                    },
                    {"raw_event_id": 102, "review": {"matched": False}},
                ],
                "added_volume_spike_records": [],
                "removed_delta_records_truncated": False,
                "added_delta_records_truncated": False,
            },
        },
    }
    second = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_trades": 50},
            "filters": {"since": "2026-06-18T17:00:00+00:00", "until": "2026-06-18T17:10:00+00:00"},
        },
        "calibration_summary": {
            "current": {"volume_spike_alerts": 4},
            "candidate_replay": {"volume_spike_alerts": 2},
            "comparison": {
                "volume_spike_delta": -2,
                "removed_volume_spike_records": [
                    {"raw_event_id": 101, "review": {"matched": False}},
                ],
                "added_volume_spike_records": [
                    {
                        "raw_event_id": 201,
                        "review": {
                            "matched": True,
                            "label": "tp",
                            "category": "legit_spike",
                        },
                    }
                ],
                "removed_delta_records_truncated": True,
                "added_delta_records_truncated": False,
            },
        },
    }
    (packet_root / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (packet_root / "second.json").write_text(json.dumps(second), encoding="utf-8")
    os.utime(packet_root / "first.json", (1_700_000_000, 1_700_000_000))
    os.utime(packet_root / "second.json", (1_700_000_100, 1_700_000_100))

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        all_response = await client.get("/api/calibration-packets/compare")
        all_body = await all_response.json()
        selected_response = await client.get(
            "/api/calibration-packets/compare?name=first.json"
        )
        selected_body = await selected_response.json()
    finally:
        await client.close()

    assert all_response.status == 200
    assert all_body["schema_version"] == "calibration_packet_comparison.v1"
    assert all_body["local_only"] is True
    assert all_body["validate_only"] is True
    assert all_body["packet_count"] == 2
    assert all_body["candidate_groups"] == 1
    assert [packet["name"] for packet in all_body["packets"]] == [
        "second.json",
        "first.json",
    ]
    aggregate = all_body["aggregate"]
    assert aggregate["removed_records"] == 3
    assert aggregate["added_records"] == 1
    assert aggregate["removed_review_matches"] == 1
    assert aggregate["removed_review_unmatched"] == 2
    assert aggregate["removed_review_labels"] == {"noise": 1, "unmatched": 2}
    assert aggregate["added_review_labels"] == {"tp": 1}
    assert aggregate["unique_removed_raw_event_ids"] == 2
    assert aggregate["repeated_removed_raw_event_ids"] == [
        {"raw_event_id": "101", "packets": 2}
    ]
    assert all_body["packets"][0]["removed_delta_records_truncated"] is True

    assert selected_response.status == 200
    assert selected_body["packet_count"] == 1
    assert selected_body["packets"][0]["name"] == "first.json"
    assert selected_body["aggregate"]["removed_records"] == 2


async def test_calibration_packet_review_summary_route_classifies_candidate_readiness(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    first = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_trades": 50},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_trades": 50},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 101,
                        "venue": "kalshi",
                        "market": "KXBTCD",
                        "this_trade_usd": 750.0,
                        "review": {
                            "matched": True,
                            "label": "noise",
                            "category": "low_notional",
                        },
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    second = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_trades": 50},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_trades": 50},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 102,
                        "venue": "kalshi",
                        "market": "KXBTCD",
                        "this_trade_usd": 810.0,
                        "review": {"matched": False},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    (packet_root / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (packet_root / "second.json").write_text(json.dumps(second), encoding="utf-8")
    os.utime(packet_root / "first.json", (1_700_000_000, 1_700_000_000))
    os.utime(packet_root / "second.json", (1_700_000_100, 1_700_000_100))

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-packets/review-summary")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "calibration_packet_review_summary.v1"
    assert body["local_only"] is True
    assert body["validate_only"] is True
    assert body["config_mutation"] is False
    assert body["db_mutation"] is False
    assert body["live_calls"] is False
    assert body["recommendation"] == "needs-more-evidence"
    assert body["risk_counts"]["removed_reviewed_noise_or_fp"] == 1
    assert body["risk_counts"]["removed_reviewed_tp"] == 0
    assert body["risk_counts"]["removed_unmatched"] == 1
    assert body["comparison"]["packet_count"] == 2
    assert body["comparison"]["aggregate"]["removed_records"] == 2
    assert [row["risk"] for row in body["samples"]] == [
        "removed_matched_noise",
        "removed_unmatched_replay_only",
    ]
    assert "generated_at" in body


async def test_calibration_packet_compare_and_review_summary_routes_map_invalid_inputs(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    (packet_root / "bad.json").write_text("{bad", encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        unsafe = await client.get("/api/calibration-packets/compare?name=..%2Fsecret.json")
        unsafe_body = await unsafe.json()
        missing = await client.get("/api/calibration-packets/compare?name=missing.json")
        missing_body = await missing.json()
        invalid = await client.get("/api/calibration-packets/compare?name=bad.json")
        invalid_body = await invalid.json()
        summary_unsafe = await client.get(
            "/api/calibration-packets/review-summary?name=..%2Fsecret.json"
        )
        summary_unsafe_body = await summary_unsafe.json()
        summary_missing = await client.get(
            "/api/calibration-packets/review-summary?name=missing.json"
        )
        summary_missing_body = await summary_missing.json()
        summary_invalid = await client.get(
            "/api/calibration-packets/review-summary?name=bad.json"
        )
        summary_invalid_body = await summary_invalid.json()
    finally:
        await client.close()

    assert unsafe.status == 400
    assert unsafe_body["error"] == "invalid packet name"
    assert missing.status == 404
    assert missing_body["error"] == "not found"
    assert invalid.status == 422
    assert invalid_body["error"] == "invalid packet json"
    assert summary_unsafe.status == 400
    assert summary_unsafe_body["error"] == "invalid packet name"
    assert summary_missing.status == 404
    assert summary_missing_body["error"] == "not found"
    assert summary_invalid.status == 422
    assert summary_invalid_body["error"] == "invalid packet json"


async def test_calibration_packet_review_queue_route_filters_selected_packets(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    packet_root.mkdir()
    first = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 801,
                        "venue": "kalshi",
                        "venue_trade_id": "trade-801",
                        "market": "KXBTCD",
                        "this_trade_usd": 750.0,
                        "baseline_median_usd": 20.0,
                        "spike_multiplier": 37.5,
                        "triage_flags": ["low_notional"],
                        "review": {"matched": False},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    second = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 802,
                        "venue": "kalshi",
                        "market": "KXETH",
                        "review": {"matched": True, "label": "noise"},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    third = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 803,
                        "venue": "kalshi",
                        "market_title": "Title / Fallback",
                        "review": {"matched": False},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    (packet_root / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (packet_root / "second.json").write_text(json.dumps(second), encoding="utf-8")
    (packet_root / "third.json").write_text(json.dumps(third), encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get(
            "/api/calibration-packets/review-queue?"
            "name=first.json&state=removed&review_group=unmatched_replay_only&"
            "market_cluster=KXBTCD&limit=1"
        )
        body = await response.json()
        all_filtered = await client.get(
            "/api/calibration-packets/review-queue?"
            "state=removed&review_group=unmatched_replay_only&"
            "market_cluster=Title%20%2F%20Fallback&limit=0"
        )
        all_filtered_body = await all_filtered.json()
        invalid = await client.get("/api/calibration-packets/review-queue?state=bad")
        invalid_body = await invalid.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "calibration_packet_review_queue.v1"
    assert body["local_only"] is True
    assert body["validate_only"] is True
    assert body["config_mutation"] is False
    assert body["db_mutation"] is False
    assert body["live_calls"] is False
    assert body["packet_count"] == 1
    assert body["filters"] == {
        "state": "removed",
        "review_group": "unmatched_replay_only",
        "market_cluster": "KXBTCD",
        "limit": 1,
    }
    assert body["totals"]["filtered_rows"] == 1
    assert body["rows"][0]["packet_name"] == "first.json"
    assert body["rows"][0]["raw_event_id"] == 801
    assert body["rows"][0]["market_cluster"] == "KXBTCD"
    assert [cluster["market_key"] for cluster in body["market_clusters"]] == ["KXBTCD"]
    assert body["rows"][0]["persisted_alert_reviewable"] is False
    assert "manual packet/raw-event inspection" in body["rows"][0]["review_action"]
    assert "generated_at" in body
    assert all_filtered.status == 200
    assert all_filtered_body["packet_count"] == 3
    assert all_filtered_body["filters"]["market_cluster"] == "Title / Fallback"
    assert all_filtered_body["totals"]["available_rows"] == 3
    assert all_filtered_body["totals"]["filtered_rows"] == 1
    assert all_filtered_body["rows"][0]["packet_name"] == "third.json"
    assert all_filtered_body["rows"][0]["raw_event_id"] == 803
    assert all_filtered_body["rows"][0]["market_cluster"] == "Title / Fallback"
    assert [cluster["market_key"] for cluster in all_filtered_body["market_clusters"]] == [
        "Title / Fallback",
    ]
    assert invalid.status == 400
    assert invalid_body["error"] == "invalid query"


async def test_dashboard_capabilities_route_reports_current_api_surface():
    from pmfi.dashboard.server import _create_dashboard_app

    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/dashboard-capabilities")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "dashboard_capabilities.v1"
    assert body["local_only"] is True
    assert "server_started_at" in body
    assert "generated_at" in body
    assert body["routes"]["calibration_packets"] is True
    assert body["routes"]["calibration_decisions"] is True
    assert body["routes"]["calibration_cluster_reviews"] is True
    assert body["routes"]["calibration_cluster_review_coverage"] is True
    assert body["routes"]["alert_review_write"] is True
    assert body["routes"]["persistence_health"] is True


async def test_calibration_decisions_route_lists_summarized_decisions(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    decision_root = tmp_path / "decisions"
    decision_root.mkdir()
    old_record = {
        "schema_version": "calibration_decision_record.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "generated_at": "2026-06-19T05:00:00+00:00",
        "decision": "needs-more-evidence",
        "rationale": "old rationale",
        "packet_selection": {"names": ["old-packet.json"], "count": 1},
        "comparison": {
            "packet_count": 1,
            "candidate_groups": 1,
            "aggregate": {
                "removed_records": 2,
                "added_records": 0,
                "removed_review_labels": {"unmatched": 2},
                "added_review_labels": {},
                "repeated_removed_raw_event_ids": [],
                "repeated_added_raw_event_ids": [],
            },
        },
    }
    new_record = {
        **old_record,
        "generated_at": "2026-06-19T06:00:00+00:00",
        "decision": "no-change",
        "rationale": "new rationale",
        "packet_selection": {"names": ["new-a.json", "new-b.json"], "count": 2},
        "comparison": {
            "packet_count": 2,
            "candidate_groups": 1,
            "aggregate": {
                "removed_records": 4,
                "added_records": 1,
                "removed_review_labels": {"unmatched": 4},
                "added_review_labels": {"tp": 1},
                "repeated_removed_raw_event_ids": [
                    {"raw_event_id": "247241", "packets": 2}
                ],
                "repeated_added_raw_event_ids": [],
            },
        },
        "cluster_review_coverage": {
            "totals": {
                "market_cluster_count": 3,
                "covered_market_cluster_count": 3,
                "uncovered_market_cluster_count": 0,
                "assessment_counts": {"true-positive-risk": 3},
                "candidate_readiness_counts": {"blocked-true-positive-risk": 3},
                "candidate_next_action_counts": {
                    "narrow-rule-before-config-review": 3,
                },
                "raw_event_lookup_payload_status_counts": {"full-payload": 3},
            },
        },
    }
    (decision_root / "old.json").write_text(json.dumps(old_record), encoding="utf-8")
    (decision_root / "new.json").write_text(json.dumps(new_record), encoding="utf-8")
    os.utime(decision_root / "old.json", (1_700_000_000, 1_700_000_000))
    os.utime(decision_root / "new.json", (1_700_000_100, 1_700_000_100))

    monkeypatch.setattr("pmfi.calibration_decisions.calibration_decision_root", lambda: decision_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-decisions")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert [decision["name"] for decision in body["decisions"]] == ["new.json", "old.json"]
    latest = body["decisions"][0]
    assert latest["decision"] == "no-change"
    assert latest["decision_readiness"] == "blocked-by-cluster-true-positive-risk"
    assert latest["packet_count"] == 2
    assert latest["comparison_packet_count"] == 2
    assert latest["removed_records"] == 4
    assert latest["added_records"] == 1
    assert latest["removed_review_labels"] == {"unmatched": 4}
    assert latest["cluster_review_assessment_counts"] == {"true-positive-risk": 3}
    assert latest["cluster_review_readiness_counts"] == {
        "blocked-true-positive-risk": 3,
    }
    assert latest["cluster_review_next_action_counts"] == {
        "narrow-rule-before-config-review": 3,
    }
    assert latest["cluster_review_payload_status_counts"] == {"full-payload": 3}
    assert latest["repeated_removed_raw_event_ids"] == [
        {"raw_event_id": "247241", "packets": 2}
    ]
    assert "generated_at" in body


async def test_calibration_decision_route_loads_record_with_summary(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    decision_root = tmp_path / "decisions"
    decision_root.mkdir()
    record = {
        "schema_version": "calibration_decision_record.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "generated_at": "2026-06-19T06:00:00+00:00",
        "decision": "needs-more-evidence",
        "rationale": "not enough reviewed persisted noise",
        "packet_selection": {"names": ["candidate.json"], "count": 1},
        "comparison": {
            "packet_count": 1,
            "candidate_groups": 1,
            "aggregate": {
                "removed_records": 2,
                "added_records": 0,
                "removed_review_labels": {"unmatched": 2},
            },
        },
        "cluster_review_coverage": {
            "totals": {
                "market_cluster_count": 1,
                "covered_market_cluster_count": 1,
                "uncovered_market_cluster_count": 0,
                "assessment_counts": {"true-positive-risk": 1},
                "candidate_readiness_counts": {"blocked-true-positive-risk": 1},
                "candidate_next_action_counts": {
                    "narrow-rule-before-config-review": 1,
                },
                "raw_event_lookup_payload_status_counts": {"full-payload": 1},
            },
        },
    }
    (decision_root / "decision.json").write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_decisions.calibration_decision_root", lambda: decision_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-decisions/decision.json")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "calibration_decision_record.v1"
    assert body["decision"] == "needs-more-evidence"
    assert body["summary"]["name"] == "decision.json"
    assert body["summary"]["decision_readiness"] == "blocked-by-cluster-true-positive-risk"
    assert body["summary"]["removed_records"] == 2
    assert body["summary"]["packet_names"] == ["candidate.json"]
    assert body["summary"]["cluster_review_assessment_counts"] == {
        "true-positive-risk": 1,
    }
    assert body["summary"]["cluster_review_next_action_counts"] == {
        "narrow-rule-before-config-review": 1,
    }
    assert body["summary"]["cluster_review_payload_status_counts"] == {
        "full-payload": 1,
    }
    assert "generated_at" in body


async def test_calibration_decision_route_maps_invalid_inputs(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    decision_root = tmp_path / "decisions"
    decision_root.mkdir()
    (decision_root / "bad.json").write_text("{bad", encoding="utf-8")
    (decision_root / "array.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_decisions.calibration_decision_root", lambda: decision_root)
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        unsafe = await client.get("/api/calibration-decisions/..%2Fsecret.json")
        unsafe_body = await unsafe.json()
        missing = await client.get("/api/calibration-decisions/missing.json")
        missing_body = await missing.json()
        invalid = await client.get("/api/calibration-decisions/bad.json")
        invalid_body = await invalid.json()
        non_object = await client.get("/api/calibration-decisions/array.json")
        non_object_body = await non_object.json()
    finally:
        await client.close()

    assert unsafe.status == 400
    assert unsafe_body["error"] == "invalid decision name"
    assert missing.status == 404
    assert missing_body["error"] == "not found"
    assert invalid.status == 422
    assert invalid_body["error"] == "invalid decision json"
    assert non_object.status == 422
    assert non_object_body["error"] == "invalid decision json"


async def test_calibration_cluster_reviews_route_lists_summarized_reviews(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    review_root = tmp_path / "cluster-reviews"
    review_root.mkdir()
    old_record = {
        "schema_version": "calibration_cluster_review.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "persisted_alert_review": False,
        "generated_at": "2026-06-19T05:00:00+00:00",
        "market_cluster": "OLD",
        "assessment": {"label": "uncertain", "rationale": "old rationale"},
        "packet_selection": {"names": ["old-packet.json"], "count": 1},
        "cluster": {"row_count": 1},
        "raw_event_ids": [101],
    }
    new_record = {
        **old_record,
        "generated_at": "2026-06-19T06:00:00+00:00",
        "market_cluster": "KXBTCD",
        "assessment": {"label": "noise", "rationale": "new rationale"},
        "packet_selection": {"names": ["m20-no.json"], "count": 1},
        "cluster": {"row_count": 2},
        "raw_event_ids": [201, 202],
        "raw_event_lookup": {
            "schema_version": "raw_event_lookup.v1",
            "found_count": 2,
            "missing_raw_event_ids": [],
            "include_payload": True,
            "rows": [
                {
                    "exchange_ts": "2026-06-19T06:00:00+00:00",
                    "trade": {
                        "outcome_key": "yes",
                        "directional_side": "yes",
                        "capital_at_risk_usd": 750.0,
                        "price": 0.45,
                    },
                },
                {
                    "exchange_ts": "2026-06-19T06:05:00+00:00",
                    "trade": {
                        "outcome_key": "no",
                        "directional_side": "yes",
                        "capital_at_risk_usd": 1200.0,
                        "price": 0.52,
                    },
                },
            ],
        },
    }
    (review_root / "old.json").write_text(json.dumps(old_record), encoding="utf-8")
    (review_root / "new.json").write_text(json.dumps(new_record), encoding="utf-8")
    os.utime(review_root / "old.json", (1_700_000_000, 1_700_000_000))
    os.utime(review_root / "new.json", (1_700_000_100, 1_700_000_100))

    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_root",
        lambda: review_root,
    )
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-cluster-reviews")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert [review["name"] for review in body["cluster_reviews"]] == [
        "new.json",
        "old.json",
    ]
    latest = body["cluster_reviews"][0]
    assert latest["assessment"] == "noise"
    assert latest["market_cluster"] == "KXBTCD"
    assert latest["packet_names"] == ["m20-no.json"]
    assert latest["row_count"] == 2
    assert latest["raw_event_id_count"] == 2
    assert latest["raw_event_lookup_embedded"] is True
    assert latest["raw_event_lookup_found_count"] == 2
    assert latest["raw_event_lookup_missing_count"] == 0
    assert latest["raw_event_lookup_include_payload"] is True
    assert latest["raw_event_lookup_payload_status"] == "full-payload"
    assert latest["raw_event_lookup_trade_row_count"] == 2
    assert latest["raw_event_lookup_directional_side_counts"] == {"yes": 2}
    assert latest["raw_event_lookup_outcome_key_counts"] == {"no": 1, "yes": 1}
    assert latest["raw_event_lookup_capital_at_risk_usd_min"] == 750.0
    assert latest["raw_event_lookup_capital_at_risk_usd_max"] == 1200.0
    assert latest["raw_event_lookup_price_min"] == 0.45
    assert latest["raw_event_lookup_price_max"] == 0.52
    assert (
        latest["raw_event_lookup_exchange_ts_min"]
        == "2026-06-19T06:00:00+00:00"
    )
    assert (
        latest["raw_event_lookup_exchange_ts_max"]
        == "2026-06-19T06:05:00+00:00"
    )
    assert latest["calibration_candidate_readiness"] == "packet-review-only"
    assert latest["calibration_candidate_blockers"] == ["packet_review_only"]
    assert latest["calibration_candidate_signals"] == [
        "single_directional_side",
        "mixed_outcome_keys",
    ]
    assert latest["calibration_candidate_next_action"] == (
        "collect-persisted-review-evidence"
    )
    assert "generated_at" in body


async def test_calibration_cluster_review_route_loads_record_with_summary(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    review_root = tmp_path / "cluster-reviews"
    review_root.mkdir()
    record = {
        "schema_version": "calibration_cluster_review.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "persisted_alert_review": False,
        "generated_at": "2026-06-19T06:00:00+00:00",
        "market_cluster": "KXBTCD",
        "assessment": {"label": "uncertain", "rationale": "needs more evidence"},
        "packet_selection": {"names": ["m20-no.json"], "count": 1},
        "cluster": {"row_count": 1},
        "raw_event_ids": [201],
        "raw_event_lookup": {
            "schema_version": "raw_event_lookup.v1",
            "found_count": 1,
            "missing_raw_event_ids": [],
            "include_payload": True,
            "rows": [{
                "raw_event_id": 201,
                "venue_code": "kalshi",
                "venue_market_id": "KXBTCD",
                "source_event_id": "source-201",
                "exchange_ts": "2026-06-19T06:00:00+00:00",
                "payload_preview": '{"ticker":"KXBTCD","side":"yes"}',
                "payload": {
                    "ticker": "KXBTCD",
                    "side": "yes",
                    "nested": {"html": "<script>ignored</script>"},
                },
                "trade": {
                    "outcome_key": "yes",
                    "directional_side": "no",
                    "capital_at_risk_usd": 750.0,
                    "price": 0.45,
                },
            }],
        },
    }
    (review_root / "cluster.json").write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_root",
        lambda: review_root,
    )
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        response = await client.get("/api/calibration-cluster-reviews/cluster.json")
        body = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert body["schema_version"] == "calibration_cluster_review.v1"
    assert body["market_cluster"] == "KXBTCD"
    assert body["summary"]["name"] == "cluster.json"
    assert body["summary"]["assessment"] == "uncertain"
    assert body["summary"]["raw_event_lookup_embedded"] is True
    assert body["summary"]["raw_event_lookup_found_count"] == 1
    assert body["summary"]["raw_event_lookup_include_payload"] is True
    assert body["summary"]["raw_event_lookup_payload_status"] == "full-payload"
    assert body["summary"]["raw_event_lookup_directional_side_counts"] == {"no": 1}
    assert body["summary"]["raw_event_lookup_capital_at_risk_usd_min"] == 750.0
    assert body["summary"]["raw_event_lookup_capital_at_risk_usd_max"] == 750.0
    assert body["summary"]["calibration_candidate_readiness"] == "needs-more-evidence"
    assert body["summary"]["calibration_candidate_next_action"] == "classify-cluster"
    assert body["summary"]["calibration_candidate_blockers"] == [
        "assessment_uncertain",
        "packet_review_only",
    ]
    assert body["summary"]["calibration_candidate_signals"] == [
        "single_directional_side",
        "single_outcome_key",
    ]
    assert body["raw_event_lookup"]["rows"][0]["raw_event_id"] == 201
    assert (
        body["raw_event_lookup"]["rows"][0]["payload_preview"]
        == '{"ticker":"KXBTCD","side":"yes"}'
    )
    assert body["raw_event_lookup"]["rows"][0]["payload"] == {
        "ticker": "KXBTCD",
        "side": "yes",
        "nested": {"html": "<script>ignored</script>"},
    }
    assert "generated_at" in body


async def test_calibration_cluster_review_route_maps_invalid_inputs(monkeypatch, tmp_path):
    from pmfi.dashboard.server import _create_dashboard_app

    review_root = tmp_path / "cluster-reviews"
    review_root.mkdir()
    (review_root / "bad.json").write_text("{bad", encoding="utf-8")
    (review_root / "array.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_root",
        lambda: review_root,
    )
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        unsafe = await client.get("/api/calibration-cluster-reviews/..%2Fsecret.json")
        unsafe_body = await unsafe.json()
        missing = await client.get("/api/calibration-cluster-reviews/missing.json")
        missing_body = await missing.json()
        invalid = await client.get("/api/calibration-cluster-reviews/bad.json")
        invalid_body = await invalid.json()
        non_object = await client.get("/api/calibration-cluster-reviews/array.json")
        non_object_body = await non_object.json()
    finally:
        await client.close()

    assert unsafe.status == 400
    assert unsafe_body["error"] == "invalid cluster review name"
    assert missing.status == 404
    assert missing_body["error"] == "not found"
    assert invalid.status == 422
    assert invalid_body["error"] == "invalid cluster review json"
    assert non_object.status == 422
    assert non_object_body["error"] == "invalid cluster review json"


async def test_calibration_cluster_review_coverage_route_uses_shared_summary(
    monkeypatch,
    tmp_path,
):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    review_root = tmp_path / "cluster-reviews"
    packet_root.mkdir()
    review_root.mkdir()
    first = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 801,
                        "venue": "kalshi",
                        "market": "KXBTCD",
                        "review": {"matched": False},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    second = {
        "export_metadata": {
            "schema_version": "volume_spike_calibration_packet.v1",
            "candidate": {"low_notional_min_baseline_median_usd": 20},
        },
        "calibration_summary": {
            "candidate": {"low_notional_min_baseline_median_usd": 20},
            "comparison": {
                "removed_volume_spike_records": [
                    {
                        "raw_event_id": 802,
                        "venue": "kalshi",
                        "market": "KXETH",
                        "review": {"matched": False},
                    },
                ],
                "added_volume_spike_records": [],
            },
        },
    }
    first_review = {
        "schema_version": "calibration_cluster_review.v1",
        "local_only": True,
        "validate_only": True,
        "config_mutation": False,
        "db_mutation": False,
        "live_calls": False,
        "persisted_alert_review": False,
        "generated_at": "2026-06-19T06:00:00+00:00",
        "packet_selection": {"names": ["first.json"], "count": 1},
        "filters": {"state": "removed", "review_group": "unmatched_replay_only"},
        "market_cluster": "KXBTCD",
        "assessment": {"label": "uncertain", "rationale": "needs evidence"},
        "cluster": {"row_count": 1},
        "raw_event_ids": [801],
        "rows": [],
    }
    second_review = {
        **first_review,
        "generated_at": "2026-06-19T07:00:00+00:00",
        "packet_selection": {"names": ["second.json"], "count": 1},
        "market_cluster": "KXETH",
        "assessment": {"label": "noise", "rationale": "safe cluster"},
        "raw_event_ids": [802],
    }
    (packet_root / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (packet_root / "second.json").write_text(json.dumps(second), encoding="utf-8")
    (review_root / "coverage-a.json").write_text(json.dumps(first_review), encoding="utf-8")
    (review_root / "coverage-b.json").write_text(json.dumps(second_review), encoding="utf-8")
    (review_root / "draft-bad.json").write_text("{bad", encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_root",
        lambda: review_root,
    )
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        default_response = await client.get("/api/calibration-cluster-reviews/coverage")
        default_body = await default_response.json()
        selected_response = await client.get(
            "/api/calibration-cluster-reviews/coverage?"
            "name=first.json,second.json&review=coverage-a.json&"
            "state=removed&review_group=unmatched_replay_only&market_cluster=KXBTCD"
        )
        selected_body = await selected_response.json()
    finally:
        await client.close()

    assert default_response.status == 200
    assert default_body["schema_version"] == "calibration_cluster_review_coverage.v1"
    assert default_body["packet_count"] == 2
    assert default_body["review_artifact_count"] == 2
    assert default_body["invalid_review_artifact_count"] == 1
    assert default_body["discovered_review_artifact_count"] == 3
    assert default_body["invalid_review_artifacts"][0]["name"] == "draft-bad.json"
    assert default_body["considered_review_artifact_count"] == 2
    assert default_body["totals"]["market_cluster_count"] == 2
    assert default_body["totals"]["covered_market_cluster_count"] == 2
    assert default_body["totals"]["uncovered_market_cluster_count"] == 0
    assert default_body["totals"]["assessment_counts"] == {
        "noise": 1,
        "uncertain": 1,
    }
    assert default_body["totals"]["candidate_readiness_counts"] == {
        "needs-more-evidence": 2,
    }
    assert default_body["totals"]["candidate_signal_counts"] == {}
    assert default_body["totals"]["candidate_next_action_counts"] == {
        "embed-raw-lookup": 2,
    }
    assert default_body["totals"]["raw_event_lookup_payload_status_counts"] == {
        "not-embedded": 2,
    }
    assert "generated_at" in default_body
    assert selected_response.status == 200
    assert selected_body["filters"] == {
        "state": "removed",
        "review_group": "unmatched_replay_only",
        "market_cluster": "KXBTCD",
    }
    assert selected_body["packet_count"] == 2
    assert selected_body["review_artifact_count"] == 1
    assert selected_body["invalid_review_artifact_count"] == 0
    assert selected_body["considered_review_artifact_count"] == 1
    assert selected_body["queue_totals"]["filtered_rows"] == 1
    assert selected_body["totals"]["covered_market_cluster_count"] == 1
    assert selected_body["market_clusters"][0]["latest_review"]["name"] == "coverage-a.json"
    assert selected_body["market_clusters"][0]["latest_review"]["assessment"] == "uncertain"
    assert (
        selected_body["market_clusters"][0]["latest_review"][
            "calibration_candidate_readiness"
        ]
        == "needs-more-evidence"
    )
    assert (
        selected_body["market_clusters"][0]["latest_review"][
            "calibration_candidate_next_action"
        ]
        == "embed-raw-lookup"
    )
    assert selected_body["market_clusters"][0]["missing_raw_event_id_count"] == 0


async def test_calibration_cluster_review_coverage_route_maps_errors(
    monkeypatch,
    tmp_path,
):
    from pmfi.dashboard.server import _create_dashboard_app

    packet_root = tmp_path / "packets"
    review_root = tmp_path / "cluster-reviews"
    packet_root.mkdir()
    review_root.mkdir()
    (packet_root / "packet.json").write_text(
        json.dumps({
            "export_metadata": {
                "schema_version": "volume_spike_calibration_packet.v1",
                "candidate": {"low_notional_min_baseline_median_usd": 20},
            },
            "calibration_summary": {
                "candidate": {"low_notional_min_baseline_median_usd": 20},
                "comparison": {
                    "removed_volume_spike_records": [],
                    "added_volume_spike_records": [],
                },
            },
        }),
        encoding="utf-8",
    )
    (packet_root / "bad.json").write_text("{bad", encoding="utf-8")
    (review_root / "bad-review.json").write_text("{bad", encoding="utf-8")

    monkeypatch.setattr("pmfi.calibration_packets.calibration_packet_root", lambda: packet_root)
    monkeypatch.setattr(
        "pmfi.calibration_cluster_reviews.calibration_cluster_review_root",
        lambda: review_root,
    )
    client = TestClient(TestServer(_create_dashboard_app(object())))
    await client.start_server()
    try:
        invalid_query = await client.get(
            "/api/calibration-cluster-reviews/coverage?name=packet.json&state=bad"
        )
        invalid_query_body = await invalid_query.json()
        missing_packet = await client.get(
            "/api/calibration-cluster-reviews/coverage?name=missing.json"
        )
        missing_packet_body = await missing_packet.json()
        invalid_packet = await client.get(
            "/api/calibration-cluster-reviews/coverage?name=bad.json"
        )
        invalid_packet_body = await invalid_packet.json()
        unsafe_review = await client.get(
            "/api/calibration-cluster-reviews/coverage?"
            "name=packet.json&review=..%2Fsecret.json"
        )
        unsafe_review_body = await unsafe_review.json()
        invalid_review = await client.get(
            "/api/calibration-cluster-reviews/coverage?"
            "name=packet.json&review=bad-review.json"
        )
        invalid_review_body = await invalid_review.json()
    finally:
        await client.close()

    assert invalid_query.status == 400
    assert invalid_query_body["error"] == "invalid query"
    assert missing_packet.status == 404
    assert missing_packet_body["error"] == "not found"
    assert invalid_packet.status == 422
    assert invalid_packet_body["error"] == "invalid artifact json"
    assert unsafe_review.status == 400
    assert unsafe_review_body["error"] == "invalid query"
    assert invalid_review.status == 422
    assert invalid_review_body["error"] == "invalid artifact json"


async def test_volume_spike_calibration_route_is_read_only_and_maps_errors(monkeypatch):
    from pmfi.dashboard.server import _create_dashboard_app

    class Pool:
        execute = AsyncMock(side_effect=AssertionError("route must not write"))

    calls = []

    async def fake_service(pool, **kwargs):
        calls.append({"pool": pool, **kwargs})
        if kwargs["venue"] == "empty":
            return {
                "current": {"normalized_trades": 0, "volume_spike_alerts": 0},
                "candidate_replay": {"volume_spike_alerts": 0},
                "comparison": {},
            }
        return {
            "schema_version": "volume_spike_calibration.v1",
            "local_only": True,
            "validate_only": True,
            "current": {"normalized_trades": 3, "volume_spike_alerts": 2},
            "candidate_replay": {"volume_spike_alerts": 1},
            "comparison": {
                "volume_spike_delta": -1,
                "removed_low_notional_thin_baseline": 1,
                "removed_review_matches": 1,
                "removed_review_unmatched": 0,
                "removed_trade_usd_buckets": {"500_to_799": 1},
                "details_limit": 1,
                "removed_volume_spike_samples": [
                    {
                        "raw_event_id": 401,
                        "venue_trade_id": "trade-removed",
                        "venue": "kalshi",
                        "market": "KXBTCD-26JUN1817-T63749.99",
                        "this_trade_usd": 750.0,
                        "baseline_median_usd": 100.0,
                        "spike_multiplier": 7.5,
                        "triage_flags": ["low_notional", "thin_baseline"],
                        "review": {
                            "matched": True,
                            "alert_id": "alert-401",
                            "trade_id": "trade-id-401",
                            "label": "noise",
                            "category": "low_notional_thin_baseline",
                            "reviewed_at": "2026-06-18T17:00:00+00:00",
                        },
                    }
                ],
                "added_volume_spike_samples": [],
            },
            "filters": {"since": kwargs["since_dt"].isoformat()},
        }

    monkeypatch.setattr("pmfi.dashboard.server._load_alert_rules_config", lambda: {"rules": {"volume_spike_v1": {}}})
    monkeypatch.setattr("pmfi.volume_spike_calibration.run_volume_spike_calibration_replay", fake_service)
    server = TestServer(_create_dashboard_app(Pool()))
    client = TestClient(server)
    await client.start_server()
    try:
        missing = await client.get("/api/volume-spike-calibration?from=2026-06-18T16:00:00%2B00:00")
        assert missing.status == 400
        assert "candidate" in (await missing.json())["detail"]

        ok = await client.get(
            "/api/volume-spike-calibration?"
            "from=2026-06-18T16:00:00%2B00:00&"
            "to=2026-06-18T17:00:00%2B00:00&"
            "min_trade_usd=1000&low_notional_min_baseline_trades=50&"
            "low_notional_min_baseline_median_usd=20&low_notional_threshold_usd=1000&"
            "venue=kalshi&market=KXBTCD-26JUN1817-T63749.99&cold_start=true&details_limit=1"
        )
        body = await ok.json()
        assert ok.status == 200
        assert body["schema_version"] == "volume_spike_calibration.v1"
        assert body["comparison"]["removed_review_matches"] == 1
        assert body["comparison"]["removed_volume_spike_samples"][0]["raw_event_id"] == 401
        assert calls[0]["limit"] == 0
        assert calls[0]["venue"] == "kalshi"
        assert calls[0]["market"] == "KXBTCD-26JUN1817-T63749.99"
        assert calls[0]["candidate"].low_notional_min_baseline_median_usd == Decimal("20")
        assert calls[0]["candidate"].low_notional_threshold_usd == Decimal("1000")
        assert calls[0]["cold_start"] is True
        assert calls[0]["details_limit"] == 1
        Pool.execute.assert_not_called()

        insufficient = await client.get(
            "/api/volume-spike-calibration?"
            "from=2026-06-18T16:00:00%2B00:00&"
            "min_trade_usd=1000&venue=empty"
        )
        insufficient_body = await insufficient.json()
        assert insufficient.status == 422
        assert insufficient_body["error"] == "insufficient evidence"
        assert "no normalized trades" in insufficient_body["detail"]
    finally:
        await client.close()


def test_recent_alerts_triage_filter_does_not_prelimit_scan():
    from pmfi.dashboard.queries import recent_alerts

    class Conn:
        sql = ""
        params = ()

        async def fetch(self, sql, *params):
            self.sql = sql
            self.params = params
            return []

    conn = Conn()

    result = asyncio.run(
        recent_alerts(conn, limit=20, triage_flags_filter=["low_notional"])
    )

    assert result == []
    assert "LIMIT" not in conn.sql.upper()
    assert conn.params == ()


def test_recent_alerts_rule_filter_is_in_sql_and_combines_with_state_and_triage():
    from pmfi.dashboard.queries import recent_alerts

    class Conn:
        sql = ""
        params = ()

        async def fetch(self, sql, *params):
            self.sql = sql
            self.params = params
            return []

    conn = Conn()

    _ = asyncio.run(
        recent_alerts(
            conn,
            limit=7,
            review_state="reviewed",
            triage_flags_filter=["low_notional"],
            rule_key="momentum_v1",
        )
    )

    assert "a.rule_key = $1" in conn.sql
    assert "lr.alert_id IS NOT NULL" in conn.sql
    assert "WHERE" in conn.sql.upper()
    assert "LIMIT" not in conn.sql.upper()
    assert conn.params == ("momentum_v1",)

    conn2 = Conn()
    _ = asyncio.run(
        recent_alerts(
            conn2,
            limit=7,
            review_label="tp",
            rule_key="volume_spike_v1",
        )
    )

    assert "a.rule_key = $2" in conn2.sql
    assert "lr.review_label = $1" in conn2.sql
    assert "LIMIT $3" in conn2.sql
    assert conn2.params == ("tp", "volume_spike_v1", 7)


def _alert_row(evidence: dict) -> dict:
    return {
        "alert_id": "alert-1",
        "rule_key": "capital_spike",
        "rule_version": "v1",
        "severity": "high",
        "confidence": "medium",
        "score": 1.25,
        "outcome_key": "yes",
        "data_quality": "ok",
        "evidence": evidence,
        "fired_at": None,
        "raw_event_id": "raw-1",
        "trade_id": "trade-1",
        "market_title": "Market",
        "venue_market_id": "venue-1",
        "review_label": None,
        "review_category": None,
        "review_notes": None,
        "reviewed_at": None,
        "reviewed_by": None,
        "is_reviewed": False,
    }


def test_recent_alerts_rows_include_evidence_facts_in_both_filter_paths():
    from pmfi.dashboard.queries import recent_alerts

    evidence = {
        "capital_at_risk_usd": 4500,
        "this_trade_usd": 4000,
        "threshold_usd": 4000,
        "trade_count": 2,
    }

    class Conn:
        async def fetch(self, sql, *params):
            return [_alert_row(evidence)]

    unfiltered = asyncio.run(recent_alerts(Conn(), limit=20))
    triage_filtered = asyncio.run(
        recent_alerts(Conn(), limit=20, triage_flags_filter=["low_notional"])
    )

    assert unfiltered[0]["evidence_facts"] == [
        {"key": "capital_at_risk_usd", "label": "capital", "value": "$4,500"},
        {"key": "this_trade_usd", "label": "trade", "value": "$4,000"},
        {"key": "threshold_usd", "label": "threshold", "value": "$4,000"},
        {"key": "trade_count", "label": "trades", "value": "2"},
    ]
    assert triage_filtered[0]["evidence_facts"] == unfiltered[0]["evidence_facts"]


def test_evidence_facts_are_ordered_display_ready_and_threshold_bounded():
    from pmfi.dashboard.queries import _evidence_facts, _summarize_evidence

    evidence = {
        "capital_at_risk_usd": 12500,
        "this_trade_usd": 88.5,
        "p99_threshold_usd": 10000,
        "threshold_usd": 8000,
        "percentile": 99.25,
        "dominant_side": "yes",
        "trade_count": 7,
        "baseline_median_usd": 2500,
        "spike_multiplier": 4.25,
        "min_spike_multiplier": 2,
        "baseline_trades": 31,
    }

    assert _evidence_facts(evidence) == [
        {"key": "capital_at_risk_usd", "label": "capital", "value": "$12,500"},
        {"key": "this_trade_usd", "label": "trade", "value": "$88.50"},
        {"key": "p99_threshold_usd", "label": "p99", "value": "$10,000"},
        {"key": "percentile", "label": "percentile", "value": "99.2"},
        {"key": "dominant_side", "label": "side", "value": "yes"},
        {"key": "trade_count", "label": "trades", "value": "7"},
        {"key": "baseline_median_usd", "label": "baseline", "value": "$2,500"},
        {"key": "spike_multiplier", "label": "spike", "value": "4.2x"},
        {"key": "min_spike_multiplier", "label": "min spike", "value": "2.0x"},
        {"key": "baseline_trades", "label": "baseline trades", "value": "31"},
    ]
    assert _summarize_evidence(evidence) == (
        "capital_at_risk_usd=$12,500  p99_threshold_usd=$10,000  "
        "percentile=99.2  side=yes  trades=7  this_trade_usd=$88.50  "
        "baseline_median_usd=$2,500  spike_multiplier=4.2x  "
        "min_spike_multiplier=2.0x  baseline_trades=31"
    )


def test_alert_review_history_ui_is_lazy_append_only_and_escaped():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert ".review-history" in html
    assert ".review-history-toggle" in html
    assert ".review-history-body[hidden]" in html
    assert "grid-template-columns: minmax(0, 1fr);" in html
    assert ".review-history-label { color: var(--ink); font-weight: 650; overflow-wrap: anywhere; }" in html
    assert "function reviewHistoryShell(a)" in html
    assert "function renderReviewHistoryRows(reviews)" in html
    assert "async function toggleAlertReviewHistory(button)" in html
    assert 'data-review-history-body hidden' in html
    assert 'aria-expanded="false">History</button>' in html
    assert "Append-only history loads on demand." in html
    assert "Append-only history: newest review first; corrections are additional rows." in html
    assert "No review rows yet. New corrections are appended, not edited in place." in html
    assert 'fetch(`/api/alerts/${encodeURIComponent(alertId)}/reviews?limit=20`)' in html
    assert "reviewHistoryCache.delete(alertId);" in html
    assert 'const category = row.category ? ` / ${esc(row.category)}` : "";' in html
    assert 'const notes = row.notes ? `<div class="review-history-detail">notes: ${esc(row.notes)}</div>` : "";' in html
    assert 'const by = row.reviewed_by ? ` by ${esc(row.reviewed_by)}` : "";' in html
    assert '<span class="review-history-label">${esc(row.label)}${category}</span>' in html
    assert '<span class="review-history-detail">review ${esc(compactId(row.review_id))}</span>' in html
