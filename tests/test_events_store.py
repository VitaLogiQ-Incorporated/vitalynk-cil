"""Tests for the event + label stores (CIL-301/303)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cil.audit.events import (
    ContinuityEvent,
    EventKind,
    EventLabel,
    EventSource,
    LabeledEvent,
    new_event_id,
)
from cil.storage.interface import EventStore, LabelStore
from cil.storage.memory import InMemoryEventStore, InMemoryLabelStore
from cil.storage.sqlite_events import SQLiteEventStore, SQLiteLabelStore

BASE = datetime(2026, 1, 1, tzinfo=UTC)

EVENT_STORES: list[Callable[[], EventStore]] = [
    InMemoryEventStore,
    lambda: SQLiteEventStore(":memory:"),
]
LABEL_STORES: list[Callable[[], LabelStore]] = [
    InMemoryLabelStore,
    lambda: SQLiteLabelStore(":memory:"),
]


def make_event(i: int, *, kind: EventKind = EventKind.ENDPOINT_FROZEN) -> ContinuityEvent:
    ts = BASE + timedelta(seconds=i)
    return ContinuityEvent(
        event_id=new_event_id(ts, kind, str(i)),
        timestamp=ts,
        kind=kind,
        source=EventSource.SYNTHETIC,
        path_id="modem-a",
        endpoint="epic-ehr",
        reachable=True,
        live=False,
        attributes={"i": i, "note": "x"},
    )


@pytest.mark.parametrize("make", EVENT_STORES)
async def test_event_roundtrip_and_protocol(make: Callable[[], EventStore]) -> None:
    store = make()
    await store.setup()
    assert isinstance(store, EventStore)
    e = make_event(0)
    await store.write_event(e)
    assert await store.get_event(e.event_id) == e
    assert await store.count() == 1
    await store.close()


@pytest.mark.parametrize("make", EVENT_STORES)
async def test_event_write_is_idempotent(make: Callable[[], EventStore]) -> None:
    store = make()
    await store.setup()
    e = make_event(0)
    await store.write_event(e)
    await store.write_event(e)
    assert await store.count() == 1
    await store.close()


@pytest.mark.parametrize("make", EVENT_STORES)
async def test_event_read_oldest_first_and_filters(make: Callable[[], EventStore]) -> None:
    store = make()
    await store.setup()
    for i in range(5):
        await store.write_event(make_event(i))
    await store.write_event(make_event(99, kind=EventKind.DECISION))

    rows = await store.read_events(limit=100)
    times = [r.timestamp for r in rows]
    assert times == sorted(times)  # oldest-first

    frozen = await store.read_events(kind=EventKind.ENDPOINT_FROZEN, limit=100)
    assert len(frozen) == 5
    decisions = await store.read_events(kind=EventKind.DECISION, limit=100)
    assert len(decisions) == 1
    await store.close()


@pytest.mark.parametrize("make", EVENT_STORES)
async def test_event_set_window(make: Callable[[], EventStore]) -> None:
    store = make()
    await store.setup()
    e = make_event(0)
    await store.write_event(e)
    await store.set_window(e.event_id, "w_test")
    got = await store.get_event(e.event_id)
    assert got is not None and got.telemetry_window_id == "w_test"
    await store.close()


async def test_event_store_survives_reboot(tmp_path: Path) -> None:
    db = str(tmp_path / "op.db")
    s1 = SQLiteEventStore(db)
    await s1.setup()
    await s1.write_event(make_event(0))
    await s1.close()
    s2 = SQLiteEventStore(db)
    await s2.setup()
    assert await s2.count() == 1
    await s2.close()


@pytest.mark.parametrize("make", LABEL_STORES)
async def test_label_upsert(make: Callable[[], LabelStore]) -> None:
    store = make()
    await store.setup()
    assert isinstance(store, LabelStore)
    eid = "evt_1"
    await store.write_label(LabeledEvent(event_id=eid, label=EventLabel.NO_ACTION, timestamp=BASE))
    await store.write_label(
        LabeledEvent(event_id=eid, label=EventLabel.SLA_BREACH, timestamp=BASE, rule_id="sla")
    )
    got = await store.get_label(eid)
    assert got is not None and got.label is EventLabel.SLA_BREACH and got.rule_id == "sla"
    assert await store.count() == 1
    await store.close()
