from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src" / "pmfi" / "dashboard" / "static" / "index.html"


def test_alerts_table_keeps_triage_flags_column_contract():
    html = HTML_PATH.read_text(encoding="utf-8")

    assert "<th style=\"text-align:left\">Flags</th>" in html
    assert "function flagsCell(a)" in html
    assert "${flagsCell(a)}" in html
    assert 'colspan="11" class="muted">waiting for alerts' in html
    assert 'colspan="11" class="muted">no recent alerts' in html
