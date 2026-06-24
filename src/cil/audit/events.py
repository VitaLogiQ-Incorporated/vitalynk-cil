"""Event domain models — the join spine of the data platform (EPIC-03).

A ``ContinuityEvent`` is the anchor everything attaches to: the labeler tags it,
and the training repository captures a ±15-min telemetry window around its
``timestamp``. Events, labels, score samples, and audit records are all
frozen, tz-aware-UTC Pydantic models (see ``cil.timeutil``).

The producers that will emit most event kinds (scoring, decision FSM, recovery)
do not exist yet; today events come from the ApplicationMonitor and the
synthetic source. The enums fix the contract now so those producers drop in
without schema churn.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cil.timeutil import ensure_utc, ensure_utc_opt, to_us


class EventKind(StrEnum):
    # Emitted today (ApplicationMonitor).
    ENDPOINT_UNREACHABLE = "endpoint_unreachable"
    ENDPOINT_FROZEN = "endpoint_frozen"
    ENDPOINT_RECOVERED = "endpoint_recovered"
    ENDPOINT_STATE_CHANGE = "endpoint_state_change"
    # Future producers (scoring / decision / recovery — Sprint 3+).
    SCORE_SAMPLE = "score_sample"
    SLA_STATE = "sla_state"
    DECISION = "decision"
    RECOVERY_VALIDATED = "recovery_validated"
    RECOVERY_FAILED = "recovery_failed"
    # Negative-class sampling + test scaffolding.
    NO_ACTION_SAMPLE = "no_action_sample"
    SYNTHETIC = "synthetic"


class DecisionAction(StrEnum):
    """Actions the decision engine may *request* (decide-not-execute)."""

    STAY = "stay"
    SHIFT = "shift"
    FAILOVER = "failover"
    OPTIMIZE = "optimize"
    ESCALATE = "escalate"


class EventSource(StrEnum):
    APP_MONITOR = "app_monitor"
    WAN_MONITOR = "wan_monitor"
    SCORING = "scoring"
    DECISION = "decision"
    RECOVERY = "recovery"
    SYNTHETIC = "synthetic"


class ScoreKind(StrEnum):
    CQS = "CQS"
    CCS = "CCS"


class EventLabel(StrEnum):
    """The exactly-seven automated event labels (rule-based; CIL-303)."""

    FAILOVER = "FAILOVER"
    RECOVERY = "RECOVERY"
    ROLLBACK = "ROLLBACK"
    ESCALATION = "ESCALATION"
    SLA_BREACH = "SLA_BREACH"
    OPTIMIZATION = "OPTIMIZATION"
    NO_ACTION = "NO_ACTION"


# Flat scalar value allowed in ContinuityEvent.attributes.
AttrValue = str | int | float | bool | None


class ContinuityEvent(BaseModel):
    """A point-in-time continuity event — the window/label anchor."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    timestamp: datetime  # the window anchor (tz-aware UTC)
    clock_source: str = "ingest"
    kind: EventKind
    source: EventSource

    path_id: str | None = None
    carrier: str | None = None
    profile: str | None = None
    endpoint: str | None = None
    system: str | None = None

    action: DecisionAction | None = None
    prev_action: DecisionAction | None = None

    cqs: float | None = Field(default=None, ge=0, le=100)
    ccs: float | None = Field(default=None, ge=0, le=100)
    ccs_tier: str | None = None
    reachable: bool | None = None
    live: bool | None = None
    sla_breaching: bool | None = None
    sustained_s: float | None = Field(default=None, ge=0)

    # Promoted policy/decision provenance (first-class, not buried in attributes).
    policy_id: str | None = None
    rule_id: str | None = None
    emitted_action: DecisionAction | None = None
    input_digest: str | None = None

    detail: str | None = None
    attributes: dict[str, AttrValue] = Field(default_factory=dict)
    telemetry_window_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("attributes")
    @classmethod
    def _flat_attributes(cls, v: dict[str, AttrValue]) -> dict[str, AttrValue]:
        for key, val in v.items():
            if not isinstance(val, str | int | float | bool | type(None)):
                raise ValueError(
                    f"attributes must be flat scalars; {key!r} is {type(val).__name__}"
                )
        return v

    @property
    def ts_us(self) -> int:
        return to_us(self.timestamp)


class LabeledEvent(BaseModel):
    """A rule-based label for one event, linked to its telemetry window."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    label: EventLabel
    timestamp: datetime
    telemetry_window_id: str | None = None
    rule_id: str | None = None
    label_reason: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class ScoreSample(BaseModel):
    """A CQS/CCS value at a point in time (native cadence)."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    scope: str  # e.g. "path" or "site"
    subject_id: str  # e.g. a path_id or site id
    kind: ScoreKind
    value: float = Field(ge=0, le=100)
    tier: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class AuditRecord(BaseModel):
    """An immutable audit-log entry."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    actor: str
    action: str
    event_id: str | None = None
    outcome: str | None = None
    detail: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class TelemetryWindow(BaseModel):
    """Header/metadata for a captured ±N-min telemetry window (CIL-302)."""

    model_config = ConfigDict(frozen=True)

    window_id: str
    event_id: str
    center_ts: datetime
    start_ts: datetime
    end_ts: datetime
    start_us: int
    end_us: int
    before_s: float
    after_s: float

    sample_count: int = 0
    app_health_count: int = 0
    score_count: int = 0

    expected_pre: int = 0
    actual_pre: int = 0
    expected_post: int = 0
    actual_post: int = 0
    complete_pre: bool = False
    complete_post: bool = False

    clock_source: str = "ingest"
    captured_at: datetime | None = None
    finalized_at: datetime | None = None
    archived_at: datetime | None = None
    resolution_note: str | None = None

    @field_validator("center_ts", "start_ts", "end_ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("captured_at", "finalized_at", "archived_at")
    @classmethod
    def _utc_opt(cls, v: datetime | None) -> datetime | None:
        return ensure_utc_opt(v)


def window_id_for(event_id: str) -> str:
    """Deterministic window id derived from the event id."""
    return f"w_{event_id}"


def new_event_id(timestamp: datetime, kind: EventKind, discriminator: str = "") -> str:
    """A deterministic, time-ordered event id (no wall clock / randomness)."""
    base = f"evt_{to_us(timestamp)}_{kind.value}"
    return f"{base}_{discriminator}" if discriminator else base
