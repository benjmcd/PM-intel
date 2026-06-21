from __future__ import annotations

import asyncio
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


DiskUsage = namedtuple("DiskUsage", "total used free")


def _usage(*, total: int = 1000, free: int = 500) -> DiskUsage:
    return DiskUsage(total=total, used=total - free, free=free)


def _now() -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_disk_guard_sets_degraded_and_blocks_intake_when_free_below_threshold(tmp_path: Path) -> None:
    from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState

    state = OperationalHealthState()
    guard = DiskHeadroomGuard(
        path=tmp_path,
        min_bytes=100,
        min_fraction=0.10,
        disk_usage=lambda path: _usage(total=1000, free=90),
    )

    snapshot = guard.evaluate(state)

    assert snapshot["status"] == "DEGRADED"
    assert snapshot["intake_allowed"] is False
    assert snapshot["reasons"][0]["reason"] == "disk_low"
    assert snapshot["reasons"][0]["observed"]["free_bytes"] == 90
    assert snapshot["reasons"][0]["threshold"]["free_bytes"] == 100


def test_disk_guard_stays_ok_and_clears_previous_breach_when_headroom_recovers(tmp_path: Path) -> None:
    from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState

    values = [_usage(total=1000, free=90), _usage(total=1000, free=250)]
    state = OperationalHealthState()
    guard = DiskHeadroomGuard(
        path=tmp_path,
        min_bytes=100,
        min_fraction=0.10,
        disk_usage=lambda path: values.pop(0),
    )

    assert guard.evaluate(state)["status"] == "DEGRADED"
    snapshot = guard.evaluate(state)

    assert snapshot["status"] == "OK"
    assert snapshot["intake_allowed"] is True
    assert snapshot["reasons"] == []


def test_guarded_source_waits_for_disk_recovery_before_pulling_event(tmp_path: Path) -> None:
    from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState, guarded_source

    values = [
        _usage(total=1000, free=90),
        _usage(total=1000, free=80),
        _usage(total=1000, free=250),
    ]
    state = OperationalHealthState()
    guard = DiskHeadroomGuard(
        path=tmp_path,
        min_bytes=100,
        min_fraction=0.10,
        disk_usage=lambda path: values.pop(0),
    )
    pulls: list[str] = []
    sleeps: list[float] = []

    async def source():
        pulls.append("pulled")
        yield "event-1"

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def run() -> str:
        shutdown = asyncio.Event()
        guarded = guarded_source(
            source(),
            state=state,
            intake_guards=[guard],
            shutdown=shutdown,
            sleep_seconds=0.01,
            sleep=sleep,
        )
        return await anext(guarded)

    assert asyncio.run(run()) == "event-1"
    assert pulls == ["pulled"]
    assert len(sleeps) == 2
    assert state.snapshot()["status"] == "OK"


def test_heartbeat_persists_operational_health_and_cmd_health_surfaces_degraded(
    tmp_path: Path,
    capsys,
) -> None:
    from pmfi.commands.reporting import cmd_health
    from pmfi.health import read_heartbeat, write_heartbeat
    from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState

    hb_path = tmp_path / "heartbeat.json"
    state = OperationalHealthState()
    guard = DiskHeadroomGuard(
        path=tmp_path,
        min_bytes=100,
        min_fraction=0.10,
        disk_usage=lambda path: _usage(total=1000, free=90),
    )
    operational = guard.evaluate(state)

    write_heartbeat(
        hb_path,
        events_total=0,
        alerts_total=0,
        started_at=_now(),
        now=_now(),
        operational_health=operational,
    )

    payload = read_heartbeat(hb_path)
    assert payload["operational_health"]["status"] == "DEGRADED"
    assert payload["operational_health"]["reasons"][0]["reason"] == "disk_low"

    rc = cmd_health(
        SimpleNamespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=999999999,
            json_output=False,
            venue_stale_seconds=None,
        )
    )
    out = capsys.readouterr().out

    assert rc == 1
    assert "operational=DEGRADED" in out
    assert "disk_low" in out
    assert "intake_paused=true" in out


def test_cmd_health_surfaces_operational_ok_without_warning(tmp_path: Path, capsys) -> None:
    from pmfi.commands.reporting import cmd_health
    from pmfi.health import write_heartbeat
    from pmfi.operational_health import DiskHeadroomGuard, OperationalHealthState

    hb_path = tmp_path / "heartbeat.json"
    state = OperationalHealthState()
    guard = DiskHeadroomGuard(
        path=tmp_path,
        min_bytes=100,
        min_fraction=0.10,
        disk_usage=lambda path: _usage(total=1000, free=250),
    )
    operational = guard.evaluate(state)

    write_heartbeat(
        hb_path,
        events_total=0,
        alerts_total=0,
        started_at=_now(),
        now=_now(),
        operational_health=operational,
    )

    rc = cmd_health(
        SimpleNamespace(
            heartbeat_path=str(hb_path),
            max_age_seconds=999999999,
            json_output=False,
            venue_stale_seconds=None,
        )
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "operational=OK" in out
    assert "intake_paused=false" in out
    assert "disk_low" not in out
