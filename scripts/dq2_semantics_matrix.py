from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pmfi.db import create_pool
from pmfi.qualification.dq2_semantics import DEFAULT_MANIFEST, run_dq2_semantics_matrix


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the offline DQ-2 semantics matrix.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("PMFI_DB_URL"),
        help="Local Postgres DSN. Defaults to PMFI_DB_URL.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    if not args.db_url:
        print("DQ-2 semantics matrix requires --db-url or PMFI_DB_URL.")
        return 2
    pool = await create_pool(args.db_url)
    try:
        evidence = await run_dq2_semantics_matrix(pool, args.manifest)
    finally:
        await pool.close()
    rendered = yaml.safe_dump(evidence, sort_keys=False, allow_unicode=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0 if evidence["outcome"] == "PASS" else 1


def main() -> int:
    return asyncio.run(_run(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
