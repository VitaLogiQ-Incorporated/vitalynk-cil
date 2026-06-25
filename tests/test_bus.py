"""Tests for the event bus (CIL-303): persist-then-fan-out."""

from __future__ import annotations

from datetime import UTC, datetime

from cil.audit.bus import EventBus
from cil.audit.events import ContinuityEvent, EventKind, EventSource, new_event_id
from cil.storage.memory import InMemoryEventStore

BASE = datetime(2026, 1, 1, tzinfo=UTC)


class _Recorder:
    """A minimal EventSubscriber that records what it sees."""

    def __init__(self) -> None:
        self.seen: list[ContinuityEvent] = []

    async def handle(self, event: ContinuityEvent) -> None:
        self.seen.append(event)


def _event() -> ContinuityEvent:
    return ContinuityEvent(
        event_id=new_event_id(BASE, EventKind.NO_ACTION_SAMPLE),
        timestamp=BASE,
        kind=EventKind.NO_ACTION_SAMPLE,
        source=EventSource.SYNTHETIC,
        path_id="modem-a",
    )


async def test_emit_persists_then_fans_out() -> None:
    store = InMemoryEventStore()
    await store.setup()
    bus = EventBus(store)
    rec = _Recorder()
    bus.subscribe(rec)

    event = _event()
    await bus.emit(event)

    assert await store.count() == 1  # durable first
    assert rec.seen == [event]  # fanned out


async def test_emit_with_no_subscribers_still_persists() -> None:
    store = InMemoryEventStore()
    await store.setup()
    bus = EventBus(store)
    await bus.emit(_event())
    assert await store.count() == 1
