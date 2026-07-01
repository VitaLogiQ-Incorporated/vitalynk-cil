"""In-memory telemetry store.

A dependency-free implementation of ``TelemetryStore`` — handy for tests and a
concrete demonstration that callers depend only on the interface, not on SQLite.
Not durable: data is lost on process exit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from cil.timeutil import to_us

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


def _in_ranges(us: int, ranges: list[tuple[int, int]] | None) -> bool:
    return any(lo <= us <= hi for lo, hi in ranges) if ranges else False


class InMemoryTelemetryStore:
    """Keeps samples in a list. Implements ``TelemetryStore``."""

    def __init__(self) -> None:
        self._rows: list[TelemetrySample] = []

    async def setup(self) -> None:
        return None

    async def write_sample(self, sample: TelemetrySample) -> None:
        self._rows.append(sample)

    async def write_samples(self, samples: Iterable[TelemetrySample]) -> int:
        count = 0
        for sample in samples:
            self._rows.append(sample)
            count += 1
        return count

    async def read_samples(
        self, *, path_id: str | None = None, limit: int = 100
    ) -> list[TelemetrySample]:
        rows = [r for r in self._rows if path_id is None or r.path_id == path_id]
        rows.sort(key=lambda r: to_us(r.timestamp))  # most-recent-N by canonical ts_us
        return rows[-limit:]

    async def read_range(
        self, *, start_us: int, end_us: int, path_id: str | None = None
    ) -> list[TelemetrySample]:
        rows = [
            r
            for r in self._rows
            if start_us <= to_us(r.timestamp) <= end_us
            and (path_id is None or r.path_id == path_id)
        ]
        return sorted(rows, key=lambda r: to_us(r.timestamp))

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[tuple[int, int]] | None = None
    ) -> int:
        keep: list[TelemetrySample] = []
        deleted = 0
        for r in self._rows:
            us = to_us(r.timestamp)
            if us < cutoff_us and not _in_ranges(us, exclude_ranges):
                deleted += 1
            else:
                keep.append(r)
        self._rows = keep
        return deleted

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class InMemoryApplicationHealthStore:
    """In-memory ``ApplicationHealthStore`` for tests and demos."""

    def __init__(self) -> None:
        self._rows: list[EndpointHealth] = []

    async def setup(self) -> None:
        return None

    async def write_health(self, health: EndpointHealth) -> None:
        self._rows.append(health)

    async def read_health(
        self, *, endpoint: str | None = None, limit: int = 100
    ) -> list[EndpointHealth]:
        rows = [r for r in self._rows if endpoint is None or r.endpoint == endpoint]
        rows.sort(key=lambda r: to_us(r.timestamp))  # most-recent-N by canonical ts_us
        return rows[-limit:]

    async def read_range(
        self, *, start_us: int, end_us: int, endpoint: str | None = None
    ) -> list[EndpointHealth]:
        rows = [
            r
            for r in self._rows
            if start_us <= to_us(r.timestamp) <= end_us
            and (endpoint is None or r.endpoint == endpoint)
        ]
        return sorted(rows, key=lambda r: to_us(r.timestamp))

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[tuple[int, int]] | None = None
    ) -> int:
        keep: list[EndpointHealth] = []
        deleted = 0
        for r in self._rows:
            us = to_us(r.timestamp)
            if us < cutoff_us and not _in_ranges(us, exclude_ranges):
                deleted += 1
            else:
                keep.append(r)
        self._rows = keep
        return deleted

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class InMemoryEventStore:
    """In-memory ``EventStore`` (append-only; set_window mutates)."""

    def __init__(self) -> None:
        self._events: dict[str, ContinuityEvent] = {}

    async def setup(self) -> None:
        return None

    async def write_event(self, event: ContinuityEvent) -> None:
        self._events.setdefault(event.event_id, event)  # idempotent on event_id

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
        rows = [
            e
            for e in self._events.values()
            if (kind is None or e.kind == kind)
            and (source is None or e.source == source)
            and (path_id is None or e.path_id == path_id)
            and (endpoint is None or e.endpoint == endpoint)
            and (since_us is None or to_us(e.timestamp) >= since_us)
            and (until_us is None or to_us(e.timestamp) <= until_us)
        ]
        rows.sort(key=lambda e: to_us(e.timestamp))
        return rows[-limit:]

    async def get_event(self, event_id: str) -> ContinuityEvent | None:
        return self._events.get(event_id)

    async def set_window(self, event_id: str, window_id: str) -> None:
        event = self._events.get(event_id)
        if event is not None:
            self._events[event_id] = event.model_copy(update={"telemetry_window_id": window_id})

    async def count(self) -> int:
        return len(self._events)

    async def close(self) -> None:
        return None


class InMemoryLabelStore:
    """In-memory ``LabelStore`` (upsert-by-event_id)."""

    def __init__(self) -> None:
        self._labels: dict[str, LabeledEvent] = {}

    async def setup(self) -> None:
        return None

    async def write_label(self, label: LabeledEvent) -> None:
        self._labels[label.event_id] = label

    async def get_label(self, event_id: str) -> LabeledEvent | None:
        return self._labels.get(event_id)

    async def read_labels(
        self, *, label: str | None = None, limit: int = 100
    ) -> list[LabeledEvent]:
        rows = [v for v in self._labels.values() if label is None or v.label.value == label]
        rows.sort(key=lambda v: to_us(v.timestamp))
        return rows[-limit:]

    async def set_window(self, event_id: str, window_id: str) -> None:
        cur = self._labels.get(event_id)
        if cur is not None:
            self._labels[event_id] = cur.model_copy(update={"telemetry_window_id": window_id})

    async def count(self) -> int:
        return len(self._labels)

    async def close(self) -> None:
        return None


class InMemoryScoreStore:
    """In-memory ``ScoreStore``."""

    def __init__(self) -> None:
        self._rows: list[ScoreSample] = []

    async def setup(self) -> None:
        return None

    async def write_score(self, score: ScoreSample) -> None:
        self._rows.append(score)

    async def write_scores(self, scores: Iterable[ScoreSample]) -> int:
        count = 0
        for s in scores:
            self._rows.append(s)
            count += 1
        return count

    async def read_scores(
        self, *, subject_id: str | None = None, kind: ScoreKind | None = None, limit: int = 100
    ) -> list[ScoreSample]:
        rows = [
            s
            for s in self._rows
            if (subject_id is None or s.subject_id == subject_id)
            and (kind is None or s.kind == kind)
        ]
        rows.sort(key=lambda s: to_us(s.timestamp))
        return rows[-limit:]

    async def read_score_range(
        self,
        *,
        start_us: int,
        end_us: int,
        subject_id: str | None = None,
        kind: ScoreKind | None = None,
    ) -> list[ScoreSample]:
        rows = [
            s
            for s in self._rows
            if start_us <= to_us(s.timestamp) <= end_us
            and (subject_id is None or s.subject_id == subject_id)
            and (kind is None or s.kind == kind)
        ]
        return sorted(rows, key=lambda s: to_us(s.timestamp))

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[tuple[int, int]] | None = None
    ) -> int:
        keep: list[ScoreSample] = []
        deleted = 0
        for s in self._rows:
            us = to_us(s.timestamp)
            if us < cutoff_us and not _in_ranges(us, exclude_ranges):
                deleted += 1
            else:
                keep.append(s)
        self._rows = keep
        return deleted

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class InMemoryAuditStore:
    """In-memory ``AuditStore`` (append-only)."""

    def __init__(self) -> None:
        self._rows: list[AuditRecord] = []

    async def setup(self) -> None:
        return None

    async def append(self, record: AuditRecord) -> None:
        self._rows.append(record)

    async def read(self, *, limit: int = 100) -> list[AuditRecord]:
        rows = sorted(self._rows, key=lambda r: to_us(r.timestamp))
        return rows[-limit:]

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class InMemoryTrainingStore:
    """In-memory ``TrainingStore`` (no purge path)."""

    def __init__(self) -> None:
        self._windows: dict[str, TelemetryWindow] = {}
        self._tel: dict[str, list[TelemetrySample]] = {}
        self._health: dict[str, list[EndpointHealth]] = {}
        self._scores: dict[str, list[ScoreSample]] = {}
        self._records: dict[str, tuple[ContinuityEvent, LabeledEvent | None]] = {}

    async def setup(self) -> None:
        return None

    async def write_window(self, window: TelemetryWindow) -> None:
        self._windows[window.window_id] = window

    async def get_window(self, window_id: str) -> TelemetryWindow | None:
        return self._windows.get(window_id)

    async def write_telemetry_rows(self, window_id: str, samples: Iterable[TelemetrySample]) -> int:
        rows = list(samples)
        self._tel.setdefault(window_id, []).extend(rows)
        return len(rows)

    async def write_health_rows(self, window_id: str, rows_in: Iterable[EndpointHealth]) -> int:
        rows = list(rows_in)
        self._health.setdefault(window_id, []).extend(rows)
        return len(rows)

    async def write_score_rows(self, window_id: str, rows_in: Iterable[ScoreSample]) -> int:
        rows = list(rows_in)
        self._scores.setdefault(window_id, []).extend(rows)
        return len(rows)

    async def write_event_record(self, event: ContinuityEvent, label: LabeledEvent | None) -> None:
        self._records[event.event_id] = (event, label)

    async def read_window_rows(self, window_id: str) -> list[TelemetrySample]:
        return sorted(self._tel.get(window_id, []), key=lambda r: to_us(r.timestamp))

    async def read_window_health(self, window_id: str) -> list[EndpointHealth]:
        return sorted(self._health.get(window_id, []), key=lambda r: to_us(r.timestamp))

    async def read_window_scores(self, window_id: str) -> list[ScoreSample]:
        return sorted(self._scores.get(window_id, []), key=lambda r: to_us(r.timestamp))

    async def list_windows(self, *, limit: int = 100) -> list[TelemetryWindow]:
        rows = sorted(self._windows.values(), key=lambda w: w.start_us, reverse=True)
        return rows[:limit]

    async def list_window_ranges(self) -> list[tuple[int, int]]:
        return [(w.start_us, w.end_us) for w in self._windows.values()]

    async def list_unfinalized(self) -> list[TelemetryWindow]:
        return [w for w in self._windows.values() if w.finalized_at is None]

    async def count(self) -> int:
        return len(self._windows)

    async def close(self) -> None:
        return None
