from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 10) -> "asyncpg.Pool":
    import asyncpg as _asyncpg
    return await _asyncpg.create_pool(
        dsn, min_size=min_size, max_size=max_size,
        server_settings={"search_path": "pmfi,public"},
    )


async def create_pool_with_retry(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    retries: int = 3,
    delay: float = 2.0,
) -> "asyncpg.Pool":
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await create_pool(dsn, min_size=min_size, max_size=max_size)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                logger.warning("DB pool attempt %d/%d failed: %s — retrying in %.1fs", attempt, retries, exc, delay)
                await asyncio.sleep(delay)
            else:
                logger.error("DB pool failed after %d attempts: %s", retries, exc)
    raise last_exc  # type: ignore[misc]


async def close_pool(pool: "asyncpg.Pool") -> None:
    await pool.close()
