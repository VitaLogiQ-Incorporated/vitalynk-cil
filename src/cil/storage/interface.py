"""The storage interface — callers depend on this, never on a concrete store.

Abstracting the store behind a Protocol is what lets UC1 ship on SQLite while
keeping the door open to a time-series DB later (CLAUDE.md §3/§5) with zero
changes to callers. ``@runtime_checkable`` so wiring code can guard with
``isinstance``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cil.audit.events import (
        AuditRecord,
        ContinuityEvent,
        EventKind,
        EventSource,
        LabeledEvent,
        ScoreKind,
        ScoreSample,
        TelemetryWindow,
    )
    from cil.telemetry.probes import EndpointHealth
    from cil.telemetry.schema import TelemetrySample

# A [start_us, end_us] microsecond range (inclusive) to protect from retention.
TimeRange = tuple[int, int]


@runtime_checkable
class TelemetryStore(Protocol):
    """Persistence for normalized telemetry samples."""

    async def setup(self) -> None:
        """Create/connect the backing store. Idempotent."""
        ...

    async def write_sample(self, sample: TelemetrySample) -> None:
        """Persist a single sample at native resolution (no downsampling)."""
        ...

    async def write_samples(self, samples: Iterable[TelemetrySample]) -> int:
        """Persist many samples; return the count written."""
        ...

    async def read_samples(
        self, *, path_id: str | None = None, limit: int = 100
    ) -> list[TelemetrySample]:
        """Return up to ``limit`` most-recent samples, oldest-first."""
        ...

    async def read_range(
        self, *, start_us: int, end_us: int, path_id: str | None = None
    ) -> list[TelemetrySample]:
        """Return ALL samples with ``start_us <= ts_us <= end_us``, ts-ascending.

        Unbounded (no default limit) and ordered by the canonical ``ts_us`` key —
        this is what window capture uses, so it must never silently truncate.
        """
        ...

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[TimeRange] | None = None
    ) -> int:
        """Delete samples older than ``cutoff_us`` (retention); rows inside any
        ``exclude_ranges`` (pinned training windows) are kept. Returns rows deleted."""
        ...

    async def count(self) -> int:
        """Return the total number of stored samples."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class ApplicationHealthStore(Protocol):
    """Persistence for clinical endpoint health results (CIL-203)."""

    async def setup(self) -> None:
        """Create/connect the backing store. Idempotent."""
        ...

    async def write_health(self, health: EndpointHealth) -> None:
        """Persist a single endpoint health result."""
        ...

    async def read_health(
        self, *, endpoint: str | None = None, limit: int = 100
    ) -> list[EndpointHealth]:
        """Return up to ``limit`` most-recent results, oldest-first."""
        ...

    async def read_range(
        self, *, start_us: int, end_us: int, endpoint: str | None = None
    ) -> list[EndpointHealth]:
        """Return ALL results with ``start_us <= ts_us <= end_us``, ts-ascending."""
        ...

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[TimeRange] | None = None
    ) -> int:
        """Delete results older than ``cutoff_us`` except inside ``exclude_ranges``."""
        ...

    async def count(self) -> int:
        """Return the total number of stored results."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class EventStore(Protocol):
    """Append-only store for continuity events (CIL-301/902). Never pruned."""

    async def setup(self) -> None: ...

    async def write_event(self, event: ContinuityEvent) -> None:
        """Persist an event (append-only)."""
        ...

    async def read_events(
        self,
        *,
        kind: EventKind | None = None,
        source: EventSource | None = None,
        path_id: str | None = None,
        endpoint: str | None = None,
        since_us: int | None = None,
        until_us: int | None = None,
        limit: int = 100,
    ) -> list[ContinuityEvent]:
        """Return most-recent matching events, oldest-first."""
        ...

    async def get_event(self, event_id: str) -> ContinuityEvent | None: ...

    async def set_window(self, event_id: str, window_id: str) -> None:
        """Backfill the telemetry_window_id (the only permitted mutation)."""
        ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class LabelStore(Protocol):
    """Store for automated event labels (CIL-303). Upsert-by-event_id; never pruned."""

    async def setup(self) -> None: ...

    async def write_label(self, label: LabeledEvent) -> None:
        """Insert or replace the label for an event (idempotent on event_id)."""
        ...

    async def get_label(self, event_id: str) -> LabeledEvent | None: ...

    async def read_labels(
        self, *, label: str | None = None, limit: int = 100
    ) -> list[LabeledEvent]: ...

    async def set_window(self, event_id: str, window_id: str) -> None: ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class ScoreStore(Protocol):
    """Store for CQS/CCS score samples (CIL-301). Pruned at the operational horizon."""

    async def setup(self) -> None: ...

    async def write_score(self, score: ScoreSample) -> None: ...

    async def write_scores(self, scores: Iterable[ScoreSample]) -> int: ...

    async def read_scores(
        self, *, subject_id: str | None = None, kind: ScoreKind | None = None, limit: int = 100
    ) -> list[ScoreSample]:
        """Return up to ``limit`` most-recent scores, oldest-first."""
        ...

    async def read_score_range(
        self,
        *,
        start_us: int,
        end_us: int,
        subject_id: str | None = None,
        kind: ScoreKind | None = None,
    ) -> list[ScoreSample]:
        """Return ALL scores in [start_us, end_us], ts-ascending (unbounded)."""
        ...

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[TimeRange] | None = None
    ) -> int: ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class AuditStore(Protocol):
    """Truly append-only audit log (CIL-301/902). Never deleted."""

    async def setup(self) -> None: ...

    async def append(self, record: AuditRecord) -> None: ...

    async def read(self, *, limit: int = 100) -> list[AuditRecord]:
        """Return up to ``limit`` most-recent records, oldest-first."""
        ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class TrainingStore(Protocol):
    """The indefinite UC2 training repository (CIL-302).

    Has NO purge/delete method — the *absence* of a delete path is the structural
    guarantee that the un-recreatable dataset can never be pruned. Lives in its own
    DB file the retention sweeper is never pointed at.
    """

    async def setup(self) -> None: ...

    async def write_window(self, window: TelemetryWindow) -> None:
        """Insert/replace a window header (idempotent on window_id)."""
        ...

    async def get_window(self, window_id: str) -> TelemetryWindow | None: ...

    async def write_telemetry_rows(
        self, window_id: str, samples: Iterable[TelemetrySample]
    ) -> int: ...

    async def write_health_rows(self, window_id: str, rows: Iterable[EndpointHealth]) -> int: ...

    async def write_score_rows(self, window_id: str, rows: Iterable[ScoreSample]) -> int: ...

    async def write_event_record(self, event: ContinuityEvent, label: LabeledEvent | None) -> None:
        """Persist the full event (+ label) record alongside its window."""
        ...

    async def read_window_rows(self, window_id: str) -> list[TelemetrySample]: ...

    async def read_window_health(self, window_id: str) -> list[EndpointHealth]: ...

    async def read_window_scores(self, window_id: str) -> list[ScoreSample]: ...

    async def list_windows(self, *, limit: int = 100) -> list[TelemetryWindow]: ...

    async def list_unfinalized(self) -> list[TelemetryWindow]:
        """Windows whose post-side has not been finalized yet (for the sweeper)."""
        ...

    async def list_window_ranges(self) -> list[TimeRange]:
        """All [start_us, end_us] window ranges — the retention sweeper's pin list."""
        ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...


@runtime_checkable
class EventSubscriber(Protocol):
    """A consumer the event bus fans out to (observe-only)."""

    async def handle(self, event: ContinuityEvent) -> None: ...
