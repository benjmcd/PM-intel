from __future__ import annotations

import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src" / "pmfi" / "dashboard" / "static" / "index.html"


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
    assert "value=\"unreviewed\"" in html
    assert "value=\"reviewed\"" in html
    assert 'id="alerts-triage-flags"' in html
    assert 'name="triage_flag" value="low_notional"' in html
    assert 'name="triage_flag" value="thin_baseline"' in html
    assert 'name="triage_flag" value="near_threshold"' in html
    assert 'name="triage_flag" value="degraded_data_quality"' in html
    assert 'name="triage_flag" value="missing_lineage"' in html
    assert "function buildAlertsUrl()" in html
    assert 'params.append("triage_flag", cb.value);' in html
    assert 'params.set("review_state", reviewState)' in html
    assert 'params.set("review_label", reviewLabel)' in html
    assert 'replace(/"/g,"&quot;")' in html
    assert "replace(/'/g,\"&#39;\")" in html
    assert "function flagsCell(a)" in html
    assert 'td("Flags", flagsCell(a)' in html
    assert "function td(label, content, attrs = \"\")" in html
    assert "data-label=" in html
    assert "@media (max-width: 900px)" in html
    assert "function updateAlertSummary(rows)" in html
    assert 'card.addEventListener("click", () => setQuickFilter(card.dataset.quickFilter));' in html
    assert 'class="review-box"' in html
    assert "<summary>Record review</summary>" in html
    assert 'class="review-actions"' in html
    assert 'data-review-alert-id' in html
    assert "function submitAlertReview(" in html
    assert 'fetch(`/api/alerts/${encodeURIComponent(alertId)}/review`' in html
    assert 'colspan="11" class="muted">waiting for alerts' in html
    assert 'colspan="11" class="muted">no recent alerts' in html


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
        "triage_flag": ["low_notional,thin_baseline", "near_threshold"],
        "limit": "25",
    })
    params = _parse_alerts_query(q)
    assert params["review_state"] == "reviewed"
    assert params["review_label"] == "tp"
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
