from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace


def test_classify_all_ok_returns_ok_exit_0():
    from pmfi.cli import _check_result, _classify_checks

    checks = [
        _check_result("a", "ok", "fine"),
        _check_result("b", "ok", "also fine"),
    ]
    label, code = _classify_checks(checks)
    assert label == "OK"
    assert code == 0


def test_classify_warn_only_returns_warn_exit_0():
    from pmfi.cli import _check_result, _classify_checks

    checks = [
        _check_result("a", "ok", "fine"),
        _check_result("b", "warn", "something off", "fix it"),
    ]
    label, code = _classify_checks(checks)
    assert label == "WARN"
    assert code == 0


def test_classify_any_fail_returns_fail_exit_1():
    from pmfi.cli import _check_result, _classify_checks

    checks = [
        _check_result("a", "ok", "fine"),
        _check_result("b", "warn", "off", "fix"),
        _check_result("c", "fail", "broken", "do something"),
    ]
    label, code = _classify_checks(checks)
    assert label == "FAIL"
    assert code == 1


def test_check_result_structure():
    from pmfi.cli import _check_result

    result = _check_result("my_check", "warn", "detail text", "fix text")
    assert result == {
        "name": "my_check",
        "status": "warn",
        "detail": "detail text",
        "fix": "fix text",
    }


def test_copy_config_copies_when_dst_missing(tmp_path: Path):
    from pmfi.cli import _copy_config_if_missing

    src = tmp_path / "src.yaml"
    dst = tmp_path / "sub" / "dst.yaml"
    src.write_text("key: value", encoding="utf-8")

    result = _copy_config_if_missing(src, dst)

    assert result is True
    assert dst.read_text(encoding="utf-8") == "key: value"


def test_copy_config_does_not_overwrite_existing(tmp_path: Path):
    from pmfi.cli import _copy_config_if_missing

    src = tmp_path / "src.yaml"
    dst = tmp_path / "dst.yaml"
    src.write_text("src: value", encoding="utf-8")
    dst.write_text("existing: content", encoding="utf-8")

    result = _copy_config_if_missing(src, dst)

    assert result is False
    assert dst.read_text(encoding="utf-8") == "existing: content"


def test_init_subcommand_registered():
    from pmfi.cli import _build_parser

    args = _build_parser().parse_args(["init"])
    assert args.command == "init"
    assert args.discover is False
    assert args.watch_top is None


def test_init_discover_and_watch_top_flags():
    from pmfi.cli import _build_parser

    args = _build_parser().parse_args(["init", "--discover", "--watch-top", "20"])
    assert args.command == "init"
    assert args.discover is True
    assert args.watch_top == 20


def test_doctor_subcommand_registered():
    from pmfi.cli import _build_parser

    args = _build_parser().parse_args(["doctor", "--json"])
    assert args.command == "doctor"
    assert args.json_output is True


def test_cmd_init_copies_config_and_runs_db_init(tmp_path: Path, monkeypatch, capsys):
    from pmfi.commands import setup

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.example.yaml").write_text("database:\n  url: test\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ROOT", tmp_path)

    calls: list[str] = []

    def fake_run_db_local_init():
        calls.append("init")
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr(setup, "_run_db_local_init", fake_run_db_local_init)

    code = setup.cmd_init(argparse.Namespace(discover=False, watch_top=None))

    assert code == 0
    assert calls == ["init"]
    assert (config_dir / "app.yaml").read_text(encoding="utf-8") == "database:\n  url: test\n"
    out = capsys.readouterr().out
    assert "not overwritten" not in out


def test_cmd_init_does_not_overwrite_existing_config(tmp_path: Path, monkeypatch):
    from pmfi.commands import setup

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.example.yaml").write_text("example: true\n", encoding="utf-8")
    (config_dir / "app.yaml").write_text("existing: true\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "_run_db_local_init", lambda: argparse.Namespace(returncode=0))

    code = setup.cmd_init(argparse.Namespace(discover=False, watch_top=None))

    assert code == 0
    assert (config_dir / "app.yaml").read_text(encoding="utf-8") == "existing: true\n"


def test_cmd_init_returns_db_init_failure(tmp_path: Path, monkeypatch):
    from pmfi.commands import setup

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.example.yaml").write_text("example: true\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "_run_db_local_init", lambda: argparse.Namespace(returncode=7))

    code = setup.cmd_init(argparse.Namespace(discover=False, watch_top=None))

    assert code == 7


def test_cmd_init_watch_top_prints_watch_top_followup(tmp_path: Path, monkeypatch, capsys):
    from pmfi.commands import setup

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "app.example.yaml").write_text("example: true\n", encoding="utf-8")
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "_run_db_local_init", lambda: argparse.Namespace(returncode=0))

    code = setup.cmd_init(argparse.Namespace(discover=False, watch_top=5))

    assert code == 0
    out = capsys.readouterr().out
    assert "pmfi markets discover --venue polymarket --limit 5 --watch-top 5" in out


def test_cmd_doctor_rejects_non_loopback_db_url_before_checks(monkeypatch, capsys):
    from pmfi.commands import setup

    async def fail_checks(_db_url):
        raise AssertionError("doctor should not query a non-loopback database")

    reserved_port = "54" + "32"
    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url=f"postgresql://pmfi:pw@example.com:{reserved_port}/pmfi")
        ),
    )
    monkeypatch.setattr(setup, "_run_all_doctor_checks", fail_checks)

    code = setup.cmd_doctor(argparse.Namespace(json_output=True))

    assert code == 1
    out = capsys.readouterr().out
    assert "Refusing non-loopback database URL" in out


def test_cmd_doctor_json_refusal_preserves_json_mode(monkeypatch, capsys):
    from pmfi.commands import setup

    reserved_port = "54" + "32"

    async def fail_checks(_db_url):
        raise AssertionError("doctor should not query a non-loopback database")

    monkeypatch.setattr(
        "pmfi.config.load_config",
        lambda: SimpleNamespace(
            database=SimpleNamespace(url=f"postgresql://pmfi:pw@example.com:{reserved_port}/pmfi")
        ),
    )
    monkeypatch.setattr(setup, "_run_all_doctor_checks", fail_checks)

    code = setup.cmd_doctor(argparse.Namespace(json_output=True))

    assert code == 1
    out = capsys.readouterr().out
    assert out.lstrip().startswith("{")
    assert '"overall": "REFUSED"' in out
