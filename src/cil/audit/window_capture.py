"""±15-min telemetry window capture (CIL-302) — the un-retrofittable core.

For each anchoring continuity event, capture a native-resolution window of
telemetry / app-health / score rows around the event, **copied verbatim** into the
separate training DB (no downsampling). Two-phase + crash-safe:

  * ``capture`` (phase 1, at event time): write the window header + ``set_window``
    pointer. Pre-event source data is safe (operational retention is 730 days vs a
    ±15-min window), so it can't be lost before finalize.
  * ``finalize_window`` (phase 2, once ``end_us`` has passed): copy the full
    ``[start_us, end_us]`` range by absolute time (so overlapping windows agree
    byte-for-byte), record completeness, stamp ``finalized_at``.

Both phases are idempotent (re-running never duplicates rows — each row type is
only copied if the window has none yet). Source reads go through the unbounded,
ts-ascending ``read_range`` so a window is never silently truncated.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from cil.audit.events import AuditRecord, TelemetryWindow, window_id_for
from cil.logging import get_logger
from cil.timeutil import from_us, to_us

if TYPE_CHECKING:
    from datetime import datetime

    from cil.audit.events import ContinuityEvent
    from cil.storage.interface import (
        ApplicationHealthStore,
        AuditStore,
        ScoreStore,
        TelemetryStore,
        TrainingStore,
    )


class WindowCaptureService:
    """Captures + finalizes ±N-min telemetry windows into the training store."""

    def __init__(
        self,
        telemetry: TelemetryStore,
        app: ApplicationHealthStore,
        score: ScoreStore,
        training: TrainingStore,
        audit: AuditStore | None = None,
        *,
        before_s: float = 900.0,
        after_s: float = 900.0,
        min_radius_s: float = 300.0,
        warn_below_s: float = 900.0,
        sample_interval_s: float = 1.0,
        tolerance: float = 0.9,
    ) -> None:
        self._telemetry = telemetry
        self._app = app
        self._score = score
        self._training = training
        self._audit = audit
        self._before_s = max(before_s, min_radius_s)  # 300s floor
        self._after_s = max(after_s, min_radius_s)
        self._warn_below_s = warn_below_s
        self._interval = sample_interval_s
        self._tolerance = tolerance
        self._log = get_logger("cil.audit.window_capture")

    async def capture(self, event: ContinuityEvent) -> TelemetryWindow:
        """Phase 1: write the window header (idempotent on window_id)."""
        wid = window_id_for(event.event_id)
        existing = await self._training.get_window(wid)
        if existing is not None:
            return existing

        center = event.timestamp
        start = center - timedelta(seconds=self._before_s)
        end = center + timedelta(seconds=self._after_s)
        note: str | None = None
        if self._before_s < self._warn_below_s or self._after_s < self._warn_below_s:
            note = f"window radius below target {self._warn_below_s}s"
            self._log.warning("window.radius_below_target", event_id=event.event_id, note=note)
            if self._audit is not None:
                await self._audit.append(
                    AuditRecord(
                        timestamp=center,
                        actor="window_capture",
                        action="radius_warning",
                        event_id=event.event_id,
                        outcome=note,
                    )
                )

        header = TelemetryWindow(
            window_id=wid,
            event_id=event.event_id,
            center_ts=center,
            start_ts=start,
            end_ts=end,
            start_us=to_us(start),
            end_us=to_us(end),
            before_s=self._before_s,
            after_s=self._after_s,
            expected_pre=int(self._before_s / self._interval),
            expected_post=int(self._after_s / self._interval),
            clock_source=event.clock_source,
            captured_at=center,
            resolution_note=note,
        )
        await self._training.write_window(header)
        return header

    async def finalize_window(
        self, window: TelemetryWindow, now: datetime | None = None
    ) -> TelemetryWindow:
        """Phase 2: copy the full window range verbatim + record completeness."""
        if window.finalized_at is not None:
            return window
        wid = window.window_id
        center_us = to_us(window.center_ts)

        # Copy each row type once (idempotent: only if the window has none yet).
        tel = await self._telemetry.read_range(start_us=window.start_us, end_us=window.end_us)
        if not await self._training.read_window_rows(wid):
            await self._training.write_telemetry_rows(wid, tel)
        health = await self._app.read_range(start_us=window.start_us, end_us=window.end_us)
        if not await self._training.read_window_health(wid):
            await self._training.write_health_rows(wid, health)
        scores = await self._score.read_score_range(start_us=window.start_us, end_us=window.end_us)
        if not await self._training.read_window_scores(wid):
            await self._training.write_score_rows(wid, scores)

        copied = await self._training.read_window_rows(wid)
        actual_pre = sum(1 for s in copied if to_us(s.timestamp) <= center_us)
        actual_post = len(copied) - actual_pre
        complete_pre = actual_pre >= window.expected_pre * self._tolerance
        complete_post = actual_post >= window.expected_post * self._tolerance

        note = window.resolution_note
        if not (complete_pre and complete_post):
            shortage = []
            if not complete_pre:
                shortage.append(f"pre {actual_pre}/{window.expected_pre}")
            if not complete_post:
                shortage.append(f"post {actual_post}/{window.expected_post}")
            flag = "short window: " + ", ".join(shortage)
            note = f"{note}; {flag}" if note else flag

        finalized = window.model_copy(
            update={
                "sample_count": len(copied),
                "app_health_count": len(await self._training.read_window_health(wid)),
                "score_count": len(await self._training.read_window_scores(wid)),
                "actual_pre": actual_pre,
                "actual_post": actual_post,
                "complete_pre": complete_pre,
                "complete_post": complete_post,
                "finalized_at": now if now is not None else window.end_ts,
                "resolution_note": note,
            }
        )
        await self._training.write_window(finalized)
        if self._audit is not None:
            await self._audit.append(
                AuditRecord(
                    timestamp=finalized.finalized_at or window.end_ts,
                    actor="window_capture",
                    action="window_finalized",
                    event_id=window.event_id,
                    outcome=f"samples={len(copied)}",
                    detail=note,
                )
            )
        return finalized

    async def finalize_due(self, now: datetime) -> int:
        """Finalize every window whose post-side window has fully elapsed."""
        now_us = to_us(now)
        finalized = 0
        for window in await self._training.list_unfinalized():
            if now_us >= window.end_us:
                await self.finalize_window(window, now=from_us(window.end_us))
                finalized += 1
        return finalized
