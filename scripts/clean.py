"""Remove local Python cache/test artifacts without a platform-specific command runner."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CACHE_DIR_NAMES = {".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__"}


def main() -> int:
    candidates = [path for path in ROOT.rglob("*") if path.is_dir() and path.name in CACHE_DIR_NAMES]
    # Remove deepest cache directories first so nested caches are not skipped when
    # a parent directory is deleted during iteration.
    candidates.sort(key=lambda path: len(path.parts), reverse=True)
    removed = 0
    for path in candidates:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    print(f"removed {removed} cache directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
