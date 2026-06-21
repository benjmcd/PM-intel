from __future__ import annotations

import sys
import tomllib
from types import SimpleNamespace

from scripts import verify


def test_verify_pytest_invocation_is_explicitly_scoped_to_tests(monkeypatch):
    commands: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_load_script(_rel_path: str):
        return SimpleNamespace(main=lambda *_args: 0)

    def fake_compile_dir(_path, *, quiet: int):
        assert quiet == 1
        return True

    def fake_run_subprocess(args: list[str], *, env=None, timeout_seconds: int = 180) -> None:
        commands.append((args, env))

    monkeypatch.setattr(verify, "load_script", fake_load_script)
    monkeypatch.setattr(verify.compileall, "compile_dir", fake_compile_dir)
    monkeypatch.setattr(verify, "run_subprocess", fake_run_subprocess)

    assert verify.main() == 0

    assert len(commands) == 1
    pytest_args, pytest_env = commands[0]
    assert pytest_args == [sys.executable, "-m", "pytest", "-q", "tests"]
    assert pytest_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_pytest_config_ignores_in_repo_virtualenv_and_site_packages_dirs():
    pyproject = tomllib.loads((verify.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignored = set(pyproject["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert {".venv", "venv", "env*", ".venv-*", "*site-packages*"}.issubset(ignored)
