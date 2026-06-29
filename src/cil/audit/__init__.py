"""Audit: audit/event logging + the automated event labeler.

Immutable event log (CIL-902) plus the rule-based labeler (CIL-303) that tags
each event as it occurs — FAILOVER, RECOVERY, ROLLBACK, ESCALATION, SLA_BREACH,
OPTIMIZATION, NO_ACTION — and links it to its telemetry window. Curation and
model training are out of UC1 scope.
"""

from cil.audit.bus import EventBus
from cil.audit.events import (
    AuditRecord,
    ContinuityEvent,
    DecisionAction,
    EventKind,
    EventLabel,
    EventSource,
    LabeledEvent,
    ScoreKind,
    ScoreSample,
    TelemetryWindow,
    new_event_id,
    window_id_for,
)
from cil.audit.labeler import EventLabeler, LabelingConfig, LabelResult, load_labeling_config
from cil.audit.pipeline import DEFAULT_ANCHOR_KINDS, LabelingPipeline
from cil.audit.synthetic import SyntheticEventSource
from cil.audit.window_capture import WindowCaptureService

__all__ = [
    "DEFAULT_ANCHOR_KINDS",
    "AuditRecord",
    "ContinuityEvent",
    "DecisionAction",
    "EventBus",
    "EventKind",
    "EventLabel",
    "EventLabeler",
    "EventSource",
    "LabelResult",
    "LabeledEvent",
    "LabelingConfig",
    "LabelingPipeline",
    "ScoreKind",
    "ScoreSample",
    "SyntheticEventSource",
    "TelemetryWindow",
    "WindowCaptureService",
    "load_labeling_config",
    "new_event_id",
    "window_id_for",
]
