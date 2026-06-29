"""Labeling pipeline (CIL-303) — bus → anchor → capture → label → backfill → audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from cil.audit.bus import EventBus
from cil.audit.events import (
    ContinuityEvent,
    DecisionAction,
    EventKind,
    EventLabel,
    EventSource,
    ScoreKind,
    ScoreSample,
    new_event_id,
    window_id_for,
)
from cil.audit.labeler import EventLabeler, LabelingConfig
from cil.audit.pipeline import LabelingPipeline
from cil.audit.window_capture import WindowCaptureService
from cil.storage.memory import (
    InMemoryApplicationHealthStore,
    InMemoryAuditStore,
    InMemoryEventStore,
    InMemoryLabelStore,
    InMemoryScoreStore,
    InMemoryTelemetryStore,
    InMemoryTrainingStore,
)

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class Wired(NamedTuple):
    bus: EventBus
    events: InMemoryEventStore
    labels: InMemoryLabelStore
    scores: InMemoryScoreStore
    training: InMemoryTrainingStore
    audit: InMemoryAuditStore
    pipeline: LabelingPipeline


async def _wire(labeler: EventLabeler | None = None) -> Wired:
    events = InMemoryEventStore()
    labels = InMemoryLabelStore()
    scores = InMemoryScoreStore()
    tel = InMemoryTelemetryStore()
    app = InMemoryApplicationHealthStore()
    training = InMemoryTrainingStore()
    audit = InMemoryAuditStore()
    for s in (events, labels, scores, tel, app, training, audit):
        await s.setup()
    capture = WindowCaptureService(
        tel, app, scores, training, audit, before_s=5, after_s=5, min_radius_s=0, warn_below_s=0
    )
    pipeline = LabelingPipeline(
        event_store=events,
        label_store=labels,
        score_store=scores,
        capture=capture,
        labeler=labeler or EventLabeler(),
        audit=audit,
        training=training,
    )
    bus = EventBus(events)
    bus.subscribe(pipeline)
    return Wired(bus, events, labels, scores, training, audit, pipeline)


def _event(second: int, kind: EventKind, **fields: object) -> ContinuityEvent:
    ts = BASE + timedelta(seconds=second)
    return ContinuityEvent(
        event_id=new_event_id(ts, kind, str(second)),
        timestamp=ts,
        kind=kind,
        source=EventSource.SYNTHETIC,
        path_id="modem-a",
        **fields,  # type: ignore[arg-type]
    )


async def test_anchoring_event_is_captured_labeled_backfilled_audited() -> None:
    w = await _wire()
    event = _event(10, EventKind.DECISION, action=DecisionAction.FAILOVER)
    await w.bus.emit(event)

    wid = window_id_for(event.event_id)
    assert await w.events.count() == 1  # bus persisted the raw event
    label = await w.labels.get_label(event.event_id)
    assert label is not None and label.label is EventLabel.FAILOVER
    assert label.telemetry_window_id == wid  # label links to window
    stored = await w.events.get_event(event.event_id)
    assert stored is not None and stored.telemetry_window_id == wid  # backfilled
    assert await w.training.get_window(wid) is not None  # window captured
    assert await w.audit.count() == 1  # decision audited


async def test_score_sample_is_persist_only_no_label() -> None:
    w = await _wire()
    await w.bus.emit(_event(0, EventKind.SCORE_SAMPLE, ccs=35.0))
    assert await w.events.count() == 1  # persisted
    assert await w.labels.count() == 0  # not anchored -> no label/window
    assert await w.training.count() == 0


async def test_sla_state_rebuilt_via_replay_survives_restart() -> None:
    cfg = LabelingConfig(outage_threshold=50.0, sla_sustain_s=3.0)
    w = await _wire(EventLabeler(cfg))  # fresh labeler, as if just restarted
    # The score timeline already shows a sustained dip on disk:
    for s in range(4):
        await w.scores.write_score(
            ScoreSample(
                timestamp=BASE + timedelta(seconds=s),
                scope="path",
                subject_id="modem-a",
                kind=ScoreKind.CCS,
                value=45.0,
            )
        )
    replayed = await w.pipeline.replay_sla(now=BASE + timedelta(seconds=4), horizon_s=3600)
    assert replayed == 4

    event = _event(4, EventKind.SLA_STATE, ccs=45.0)
    await w.bus.emit(event)
    label = await w.labels.get_label(event.event_id)
    assert label is not None and label.label is EventLabel.SLA_BREACH


async def test_handle_is_idempotent_on_event_id() -> None:
    w = await _wire()
    event = _event(10, EventKind.DECISION, action=DecisionAction.OPTIMIZE)
    await w.bus.emit(event)
    await w.bus.emit(event)  # re-delivery
    assert await w.events.count() == 1
    assert await w.labels.count() == 1
    assert await w.training.count() == 1
