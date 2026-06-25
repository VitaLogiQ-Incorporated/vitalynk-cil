"""Deterministic synthetic event source (CIL-303 testing enabler).

The real producers (scoring engine, decision FSM, recovery validator) don't exist
yet, so this synthesises a continuity-event stream that exercises every label
path — including periodic NO_ACTION_SAMPLE negatives — so the labeler + pipeline
can be built and tested today. Deterministic: tick-based timestamps, no wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cil.audit.events import (
    ContinuityEvent,
    DecisionAction,
    EventKind,
    EventSource,
    new_event_id,
)
from cil.telemetry.probes import ProbeDepth

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class SyntheticEventSource:
    """Emits a canonical sequence of events covering all seven labels."""

    def __init__(self, *, start: datetime = _EPOCH, path_id: str = "modem-a") -> None:
        self._start = start
        self._path = path_id

    def _at(self, second: int) -> datetime:
        return self._start + timedelta(seconds=second)

    def _event(self, second: int, kind: EventKind, **fields: object) -> ContinuityEvent:
        ts = self._at(second)
        disc = str(fields.get("endpoint") or fields.get("action") or second)
        return ContinuityEvent(
            event_id=new_event_id(ts, kind, disc),
            timestamp=ts,
            kind=kind,
            source=EventSource.SYNTHETIC,
            path_id=self._path,
            **fields,  # type: ignore[arg-type]
        )

    def events(self) -> list[ContinuityEvent]:
        """A deterministic stream exercising every label path."""
        return [
            # NO_ACTION negatives
            self._event(0, EventKind.NO_ACTION_SAMPLE),
            self._event(5, EventKind.NO_ACTION_SAMPLE),
            # OPTIMIZATION (within-carrier tune)
            self._event(10, EventKind.DECISION, action=DecisionAction.OPTIMIZE),
            # FAILOVER (requested; decide-not-execute)
            self._event(20, EventKind.DECISION, action=DecisionAction.FAILOVER),
            # ROLLBACK (revert the failover)
            self._event(
                25,
                EventKind.DECISION,
                action=DecisionAction.STAY,
                prev_action=DecisionAction.FAILOVER,
                attributes={"seconds_since_prev": 5},
            ),
            # ESCALATION
            self._event(30, EventKind.DECISION, action=DecisionAction.ESCALATE),
            # RECOVERY (app-response endpoint -> authoritative)
            self._event(40, EventKind.ENDPOINT_RECOVERED, endpoint="epic-ehr", system="Epic"),
            # RECOVERY withheld (render-state endpoint -> pending clinical input)
            self._event(
                42,
                EventKind.ENDPOINT_RECOVERED,
                endpoint="or-systems",
                system="OR",
                attributes={"required_depth": ProbeDepth.RENDER_STATE.value},
            ),
        ]

    def sla_breach_scores(self, *, below: float = 35.0, n: int = 7) -> list[ContinuityEvent]:
        """A run of below-threshold SCORE_SAMPLE events that sustains an SLA breach."""
        return [self._event(50 + i, EventKind.SCORE_SAMPLE, ccs=below) for i in range(n)]
