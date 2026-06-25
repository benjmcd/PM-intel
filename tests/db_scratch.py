"""Shared scratch database helpers for DB-gated tests."""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from pmfi.commands._shared import is_loopback_db_url
from pmfi.qualification.soak_stability import _drop_database, _init_schema, _quote_ident

TESTISO_DB_PREFIX = "pmfi_testiso_"
MAX_POSTGRES_IDENTIFIER_LENGTH = 63
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass(frozen=True)
class ScratchDatabase:
    name: str
    dsn: str
    configured_dsn: str


def _configured_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def _admin_dsn(base_dsn: str) -> str:
    if not is_loopback_db_url(base_dsn):
        raise RuntimeError("DB-gated scratch tests require a loopback PMFI_DB_URL")
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))


def _database_dsn(base_dsn: str, database: str) -> str:
    parsed = urlsplit(base_dsn)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _safe_label(label: str, *, max_length: int) -> str:
    if max_length <= 0:
        raise RuntimeError(f"scratch database label has no safe budget: {label!r}")
    safe = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    if not safe:
        raise RuntimeError(f"unsafe empty scratch database label: {label!r}")
    return safe[:max_length].strip("_")


def _ensure_testiso_database(name: str) -> None:
    if not name.startswith(TESTISO_DB_PREFIX) or not _DB_NAME_RE.fullmatch(name):
        raise RuntimeError("target must be a test-isolation scratch database named pmfi_testiso_*")


def _scratch_database_name(label: str) -> str:
    suffix = f"_p{os.getpid()}_{uuid.uuid4().hex[:8]}"
    label_budget = MAX_POSTGRES_IDENTIFIER_LENGTH - len(TESTISO_DB_PREFIX) - len(suffix)
    name = f"{TESTISO_DB_PREFIX}{_safe_label(label, max_length=label_budget)}{suffix}"
    _ensure_testiso_database(name)
    return name


async def _create_scratch_database(base_dsn: str, name: str, *, init_schema: bool) -> str:
    _ensure_testiso_database(name)
    conn = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(conn, name)
        await conn.execute(f"CREATE DATABASE {_quote_ident(name)}")
    finally:
        await conn.close()
    scratch_dsn = _database_dsn(base_dsn, name)
    if init_schema:
        await _init_schema(scratch_dsn)
    return scratch_dsn


async def _drop_scratch_database(base_dsn: str, name: str) -> None:
    _ensure_testiso_database(name)
    conn = await asyncpg.connect(_admin_dsn(base_dsn))
    try:
        await _drop_database(conn, name)
    finally:
        await conn.close()


async def list_testiso_scratch_databases(base_dsn: str | None = None) -> list[str]:
    conn = await asyncpg.connect(_admin_dsn(base_dsn or _configured_dsn()))
    try:
        rows = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname",
            f"{TESTISO_DB_PREFIX}%",
        )
        return [str(row["datname"]) for row in rows]
    finally:
        await conn.close()


def create_test_scratch_database(label: str, *, init_schema: bool = True) -> ScratchDatabase:
    configured = _configured_dsn()
    name = _scratch_database_name(label)
    dsn = asyncio.run(_create_scratch_database(configured, name, init_schema=init_schema))
    return ScratchDatabase(name=name, dsn=dsn, configured_dsn=configured)


def drop_test_scratch_database(scratch: ScratchDatabase) -> None:
    asyncio.run(_drop_scratch_database(scratch.configured_dsn, scratch.name))
