from __future__ import annotations

import json
from pathlib import Path


def _paths(tmp_path: Path, run_id: str):
    from pmfi.qualification.soak_runner import SoakRunPaths

    paths = SoakRunPaths.from_root(tmp_path, run_id)
    paths.run_dir.mkdir(parents=True)
    return paths


def test_soak_dashboard_html_embeds_data_and_refresh_control(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import build_dashboard_html

    paths = _paths(tmp_path, "dashboard-html-test")
    samples = [
        {
            "sampled_at": "2026-06-25T00:00:00+00:00",
            "events_processed": 10,
            "rss_mb": 42.0,
            "db_size_mb": 9.5,
            "disk_free_bytes": 50 * 1024 * 1024 * 1024,
            "pool_acquire": {"p95_ms": 0.04},
            "dead_letters_created": 0,
        },
        {
            "sampled_at": "2026-06-25T00:01:00+00:00",
            "events_processed": 20,
            "rss_mb": 42.5,
            "db_size_mb": 9.7,
            "disk_free_bytes": 49 * 1024 * 1024 * 1024,
            "pool_acquire": {"p95_ms": 0.05},
            "dead_letters_created": 1,
        },
    ]
    paths.samples_file.write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples),
        encoding="utf-8",
    )

    html = build_dashboard_html(paths)

    assert html.startswith("<!doctype html")
    assert "dashboard-html-test" in html
    assert 'id="soak-data"' in html
    assert "RSS" in html
    assert "refresh-button" in html
    assert "Refresh" in html
    assert "verdict" in html
    assert "https://" not in html
    assert "http://" not in html

    empty_paths = _paths(tmp_path, "empty-dashboard")
    html = build_dashboard_html(empty_paths)

    assert html.startswith("<!doctype html")
    assert "empty-dashboard" in html
    assert 'id="soak-data"' in html
    assert "waiting for samples" in html


def test_soak_dashboard_refresh_regex_escape_survives_template_render(tmp_path: Path) -> None:
    from pmfi.qualification.soak_runner import build_dashboard_html

    paths = _paths(tmp_path, "escape-regression")

    html = build_dashboard_html(paths)
    script_start = html.index("<script>\n") + len("<script>\n")
    script_end = html.index("</script>", script_start)
    inline_js = html[script_start:script_end]

    assert "text.split(/\\r?\\n/)" in inline_js
    assert "\r" not in inline_js
    disallowed_controls = "\x00\x08\x0b\x0c"
    assert not any(control in inline_js for control in disallowed_controls)
