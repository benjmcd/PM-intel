from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


async def insert_dead_letter(
    conn: asyncpg.Connection,
    *,
    venue_code: str | None,
    raw_event_id: int | None,
    source_channel: str | None,
    failure_stage: str,
    error_class: str | None,
    error_message: str,
    payload: dict,
) -> None:
    await conn.execute(
        """INSERT INTO dead_letters
           (venue_code, raw_event_id, source_channel, failure_stage,
            error_class, error_message, payload)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
        venue_code,
        raw_event_id,
        source_channel,
        failure_stage,
        error_class,
        error_message,
        json.dumps(payload),
    )
