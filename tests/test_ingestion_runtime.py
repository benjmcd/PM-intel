import asyncio
import json


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeConn:
    def __init__(self):
        self.fetchrow_calls = []
        self.execute_calls = []

    async def fetchrow(self, sql, *params):
        self.fetchrow_calls.append((sql, params))
        return {"connection_id": "11111111-1111-1111-1111-111111111111"}

    async def execute(self, sql, *params):
        self.execute_calls.append((sql, params))
        return "OK"


class _FakePool:
    def __init__(self):
        self.conn = _FakeConn()
        self.acquire_count = 0

    def acquire(self):
        self.acquire_count += 1
        return _Acquire(self.conn)


def test_ingestion_runtime_helpers_write_schema_columns():
    from pmfi.db.repos.ingestion_runtime import (
        record_connection_message,
        record_connection_start,
        record_connection_stop,
        record_heartbeat,
    )

    pool = _FakePool()

    async def _run():
        connection_id = await record_connection_start(
            pool,
            venue_code="kalshi",
            source_channel="websocket",
            metadata={"bounded": True},
        )
        await record_connection_message(pool, connection_id)
        await record_connection_stop(pool, connection_id, metadata={"reason": "bounded_complete"})
        await record_heartbeat(
            pool,
            worker_name="pmfi-ingest:kalshi:websocket",
            worker_type="ingest",
            status="stopped",
            metadata={"raw_events_seen": 1},
        )
        return connection_id

    connection_id = asyncio.run(_run())

    assert connection_id == "11111111-1111-1111-1111-111111111111"
    start_sql, start_params = pool.conn.fetchrow_calls[0]
    heartbeat_sql, heartbeat_params = pool.conn.execute_calls[-1]
    all_sql = "\n".join([start_sql, *[call[0] for call in pool.conn.execute_calls]])

    assert "ingestion_connections" in start_sql
    assert "system_heartbeats" in heartbeat_sql
    assert "worker_type" in heartbeat_sql
    assert "metadata" in heartbeat_sql
    assert "component" not in all_sql
    assert "details" not in all_sql
    assert json.loads(start_params[-1]) == {"bounded": True}
    assert json.loads(heartbeat_params[-1]) == {"raw_events_seen": 1}
    assert pool.acquire_count == 4
