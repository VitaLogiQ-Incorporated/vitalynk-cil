"""Export seam (EPIC-03) — the hand-off to UC2 / the cloud archive.

Operational data exports as JSONL (one record per line); each training window
exports as a single self-describing JSON record (header + label-carrying event +
native telemetry / app-health / score rows). Pure read-only serialization — no
mutation, no curation (curation is UC2).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cil.storage.interface import AuditStore, EventStore, ScoreStore, TrainingStore


class OperationalExporter:
    """Serializes operational events / scores / audit to JSONL."""

    def __init__(self, events: EventStore, scores: ScoreStore, audit: AuditStore) -> None:
        self._events = events
        self._scores = scores
        self._audit = audit

    async def events_jsonl(self, *, limit: int = 10000) -> str:
        rows = await self._events.read_events(limit=limit)
        return "\n".join(e.model_dump_json() for e in rows)

    async def scores_jsonl(self, *, limit: int = 10000) -> str:
        rows = await self._scores.read_scores(limit=limit)
        return "\n".join(s.model_dump_json() for s in rows)

    async def audit_jsonl(self, *, limit: int = 10000) -> str:
        rows = await self._audit.read(limit=limit)
        return "\n".join(r.model_dump_json() for r in rows)


class TrainingExporter:
    """Serializes the indefinite training windows for the UC2 dataset."""

    def __init__(self, training: TrainingStore) -> None:
        self._training = training

    async def export_window(self, window_id: str) -> dict[str, Any] | None:
        window = await self._training.get_window(window_id)
        if window is None:
            return None
        return {
            "window": window.model_dump(mode="json"),
            "telemetry": [
                s.model_dump(mode="json") for s in await self._training.read_window_rows(window_id)
            ],
            "app_health": [
                h.model_dump(mode="json")
                for h in await self._training.read_window_health(window_id)
            ],
            "scores": [
                s.model_dump(mode="json")
                for s in await self._training.read_window_scores(window_id)
            ],
        }

    async def export_all_jsonl(self, *, limit: int = 10000) -> str:
        lines: list[str] = []
        for window in await self._training.list_windows(limit=limit):
            record = await self.export_window(window.window_id)
            lines.append(json.dumps(record))
        return "\n".join(lines)
