"""The event bus — the single seam every producer publishes through (CIL-303).

``emit`` persists the raw event first (durable), then fans out to subscribers
(the labeling pipeline, etc.). It only observes/records — it never decides or acts
(decide-not-execute). Future producers (scoring, decision FSM, recovery) call
``emit`` exactly like today's ApplicationMonitor + synthetic source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cil.logging import get_logger

if TYPE_CHECKING:
    from cil.audit.events import ContinuityEvent
    from cil.storage.interface import EventStore, EventSubscriber


class EventBus:
    """Persists each event, then notifies subscribers in registration order."""

    def __init__(self, store: EventStore) -> None:
        self._store = store
        self._subscribers: list[EventSubscriber] = []
        self._log = get_logger("cil.audit.bus")

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers.append(subscriber)

    async def emit(self, event: ContinuityEvent) -> None:
        await self._store.write_event(event)  # durable first
        for subscriber in self._subscribers:
            await subscriber.handle(event)
