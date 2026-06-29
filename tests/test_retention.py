"""Retention sweeper (CIL-301): purge aged operational data; protect the dataset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cil.audit.events import (
    AuditRecord,
    ContinuityEvent,
    EventKind,
    EventSource,
    TelemetryWindow,
    new_event_id,
    window_id_for,
)
from cil.storage.memory import (
    InMemoryApplicationHealthStore,
    InMemoryAuditStore,
    InMemoryEventStore,
    InMemoryScoreStore,
    InMemoryTelemetryStore,
    InMemoryTrainingStore,
)
from cil.storage.retention import RetentionSweeper
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample
from cil.timeutil import to_us

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def sample_at(dt: datetime) -> TelemetrySample:
    return TelemetrySample(
        timestamp=dt,
        path_id="modem-a",
        carrier="c",
        profile="p",
        radio=RadioMetrics(),
        network=NetworkMetrics(reachable=True),
        device=DeviceMetrics(),
    )


async def test_sweep_purges_aged_operational_data_only() -> None:
    tel = InMemoryTelemetryStore()
    app = InMemoryApplicationHealthStore()
    score = InMemoryScoreStore()
    training = InMemoryTrainingStore()
    events = InMemoryEventStore()
    audit = InMemoryAuditStore()
    for s in (tel, app, score, training, events, audit):
        await s.setup()

    old = BASE  # day 0 (will be older than the cutoff)
    old2 = BASE + timedelta(seconds=10)
    recent = BASE + timedelta(days=750)
    await tel.write_samples([sample_at(old), sample_at(old2), sample_at(recent)])

    # event spine + audit must be untouched by retention
    ev = ContinuityEvent(
        event_id=new_event_id(old, EventKind.ENDPOINT_FROZEN),
        timestamp=old,
        kind=EventKind.ENDPOINT_FROZEN,
        source=EventSource.APP_MONITOR,
        path_id="modem-a",
    )
    await events.write_event(ev)
    await audit.append(AuditRecord(timestamp=old, actor="x", action="y"))

    # a training window pins the `old` sample's range (defense-in-depth)
    await training.write_window(
        TelemetryWindow(
            window_id=window_id_for(ev.event_id),
            event_id=ev.event_id,
            center_ts=old,
            start_ts=old,
            end_ts=old,
            start_us=to_us(old),
            end_us=to_us(old),
            before_s=0,
            after_s=0,
        )
    )

    sweeper = RetentionSweeper(tel, app, score, training, audit, retention_days=730)
    now = BASE + timedelta(days=800)  # cutoff = now - 730d = BASE + 70d
    purged = await sweeper.sweep_once(now)

    assert purged["telemetry"] == 1  # only old2 (old is pinned, recent is fresh)
    remaining = await tel.read_range(start_us=0, end_us=to_us(now))
    secs = {r.timestamp for r in remaining}
    assert old in secs and recent in secs and old2 not in secs  # pinned + recent kept

    # the immutable spine + audit are never pruned (audit even gains a sweep row)
    assert await events.count() == 1
    assert await training.count() == 1
    assert await audit.count() == 2  # original + the sweep summary


async def test_sweep_with_nothing_aged_is_noop() -> None:
    tel = InMemoryTelemetryStore()
    app = InMemoryApplicationHealthStore()
    score = InMemoryScoreStore()
    training = InMemoryTrainingStore()
    for s in (tel, app, score, training):
        await s.setup()
    await tel.write_sample(sample_at(BASE + timedelta(days=799)))
    sweeper = RetentionSweeper(tel, app, score, training, retention_days=730)
    purged = await sweeper.sweep_once(BASE + timedelta(days=800))
    assert purged == {"telemetry": 0, "application_health": 0, "score_samples": 0}
    assert await tel.count() == 1
