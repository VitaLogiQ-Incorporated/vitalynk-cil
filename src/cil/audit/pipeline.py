"""Labeling pipeline (CIL-303) — the event-bus subscriber that ties it together.

For each event the bus delivers:
  * feed the CCS reading (if any) into the labeler's SLA timeline;
  * if the kind doesn't anchor a window (e.g. SCORE_SAMPLE), stop — it's persist-only;
  * otherwise: capture the ±15-min window, label the event, write the label,
    backfill the window pointer onto the event, persist the training record, and
    append an audit row.

Order matters: the bus has already persisted the raw event; here the window is
minted before the label references it, and the audit row records the decision.
Everything keys on ``event_id``, so the whole step is idempotent.

On startup, ``replay_sla`` rebuilds the labeler's SLA state from the recent score
timeline so a restart mid-breach doesn't reset the dwell timer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import Counter

from cil.audit.events import AuditRecord, EventKind, LabeledEvent
from cil.logging import get_logger
from cil.timeutil import to_us

EVENTS_TOTAL = Counter(
    "cil_events_total", "Continuity events handled by the pipeline.", labelnames=("kind",)
)
LABELS_TOTAL = Counter("cil_labels_total", "Automated event labels written.", labelnames=("label",))

if TYPE_CHECKING:
    from datetime import datetime

    from cil.audit.events import ContinuityEvent
    from cil.audit.labeler import EventLabeler
    from cil.audit.window_capture import WindowCaptureService
    from cil.storage.interface import AuditStore, EventStore, LabelStore, ScoreStore, TrainingStore

# Kinds that anchor a telemetry window + label. SCORE_SAMPLE is persist-only (it
# feeds the SLA timeline but would otherwise mint a window every second).
DEFAULT_ANCHOR_KINDS: frozenset[EventKind] = frozenset(
    {
        EventKind.ENDPOINT_UNREACHABLE,
        EventKind.ENDPOINT_FROZEN,
        EventKind.ENDPOINT_RECOVERED,
        EventKind.ENDPOINT_STATE_CHANGE,
        EventKind.DECISION,
        EventKind.SLA_STATE,
        EventKind.RECOVERY_VALIDATED,
        EventKind.RECOVERY_FAILED,
        EventKind.NO_ACTION_SAMPLE,
    }
)


def _subject(event: ContinuityEvent) -> str:
    return event.path_id or event.endpoint or "global"


class LabelingPipeline:
    """Subscriber: anchor → capture → label → backfill → audit. Implements ``EventSubscriber``."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        label_store: LabelStore,
        score_store: ScoreStore,
        capture: WindowCaptureService,
        labeler: EventLabeler,
        audit: AuditStore | None = None,
        training: TrainingStore | None = None,
        anchor_kinds: frozenset[EventKind] = DEFAULT_ANCHOR_KINDS,
    ) -> None:
        self._event_store = event_store
        self._label_store = label_store
        self._score_store = score_store
        self._capture = capture
        self._labeler = labeler
        self._audit = audit
        self._training = training
        self._anchor_kinds = anchor_kinds
        self._log = get_logger("cil.audit.pipeline")

    async def handle(self, event: ContinuityEvent) -> None:
        EVENTS_TOTAL.labels(event.kind.value).inc()
        # Feed the SLA timeline from any CCS-carrying event.
        if event.ccs is not None:
            self._labeler.observe_score(_subject(event), event.ccs, event.timestamp)

        if event.kind not in self._anchor_kinds:
            return  # persist-only (the bus already persisted it)

        window = await self._capture.capture(event)
        result = self._labeler.label(event)
        LABELS_TOTAL.labels(result.label.value).inc()
        labeled = LabeledEvent(
            event_id=event.event_id,
            label=result.label,
            timestamp=event.timestamp,
            telemetry_window_id=window.window_id,
            rule_id=result.rule_id,
            label_reason=result.reason,
        )
        await self._label_store.write_label(labeled)
        await self._event_store.set_window(event.event_id, window.window_id)
        if self._training is not None:
            await self._training.write_event_record(event, labeled)
        if self._audit is not None:
            await self._audit.append(
                AuditRecord(
                    timestamp=event.timestamp,
                    actor="labeler",
                    action="label",
                    event_id=event.event_id,
                    outcome=result.label.value,
                    detail=result.rule_id,
                )
            )

    async def replay_sla(self, *, now: datetime, horizon_s: float) -> int:
        """Rebuild SLA dwell state from the recent score timeline (startup)."""
        start_us = to_us(now) - int(horizon_s * 1_000_000)
        scores = await self._score_store.read_score_range(start_us=start_us, end_us=to_us(now))
        for s in scores:
            self._labeler.observe_score(s.subject_id, s.value, s.timestamp)
        return len(scores)
