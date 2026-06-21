from __future__ import annotations

import asyncio
import json


def test_ingestion_connection_repo_persists_lifecycle_classification() -> None:
    from pmfi.db.repos.ingestion_connections import (
        finish_ingestion_connection,
        mark_ingestion_connection_message,
        start_ingestion_connection,
    )

    class _Conn:
        def __init__(self) -> None:
            self.insert_args: tuple = ()
            self.execute_calls: list[tuple[str, tuple]] = []

        async def fetchrow(self, query: str, *args):
            assert "INSERT INTO ingestion_connections" in query
            self.insert_args = args
            return {"connection_id": "33333333-3333-3333-3333-333333333333"}

        async def execute(self, query: str, *args):
            self.execute_calls.append((query, args))
            return "UPDATE 1"

    conn = _Conn()

    connection_id = asyncio.run(
        start_ingestion_connection(
            conn,  # type: ignore[arg-type]
            venue_code="polymarket",
            source_channel="ws_clob",
            reconnect_count=2,
            metadata={"asset_count": 3},
        )
    )
    asyncio.run(mark_ingestion_connection_message(conn, connection_id))  # type: ignore[arg-type]
    asyncio.run(
        finish_ingestion_connection(
            conn,  # type: ignore[arg-type]
            connection_id,
            reason="Polymarket WS closed/error",
            classification="best_effort_gap",
        )
    )

    assert connection_id == "33333333-3333-3333-3333-333333333333"
    assert conn.insert_args[:4] == ("polymarket", "ws_clob", 2, json.dumps({"asset_count": 3}))
    assert len(conn.execute_calls) == 2
    finish_query, finish_args = conn.execute_calls[-1]
    assert "disconnected_at" in finish_query
    assert finish_args[0] == connection_id
    assert finish_args[1] == "disconnected"
    assert finish_args[2] == "Polymarket WS closed/error"
    assert json.loads(finish_args[3]) == {"classification": "best_effort_gap"}
