r"""Canonical local verification entrypoint.

Runs baseline checks that must work in a Windows local directory without network
access and without a running database. Database verification is a separate
opt-in gate: `python scripts\db_local.py verify`.
"""

from __future__ import annotations

import compileall
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_script(rel_path: str) -> ModuleType:
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"cannot load {rel_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_check(label: str, func: Callable[[], int | None]) -> None:
    print(f"== {label} ==", flush=True)
    result = func()
    if result not in (0, None):
        raise SystemExit(int(result))


def run_subprocess(args: list[str], *, env: dict[str, str] | None = None, timeout_seconds: int = 180) -> None:
    print("==", " ".join(args), "==", flush=True)
    completed = subprocess.run(args, cwd=ROOT, text=True, check=False, env=env, timeout=timeout_seconds)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    agent_context_check = load_script("scripts/agent_context_check.py")
    verify_workspace = load_script("scripts/verify_workspace.py")
    consistency_audit = load_script("scripts/consistency_audit.py")

    run_check(r"python scripts\agent_context_check.py --quiet", lambda: agent_context_check.main(["--quiet"]))
    run_check(r"python scripts\verify_workspace.py", verify_workspace.main)
    run_check(r"python scripts\consistency_audit.py", consistency_audit.main)

    print("== compileall ==", flush=True)
    ok = (
        compileall.compile_dir(ROOT / "src", quiet=1)
        and compileall.compile_dir(ROOT / "tests", quiet=1)
        and compileall.compile_dir(ROOT / "scripts", quiet=1)
    )
    if not ok:
        return 1

    pytest_env = os.environ.copy()
    pytest_env.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    run_subprocess([sys.executable, "-m", "pytest", "-q"], env=pytest_env)

    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
