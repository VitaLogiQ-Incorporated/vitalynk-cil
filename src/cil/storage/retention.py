"""Retention sweeper (CIL-301) — operational purge that can't touch the dataset.

Deletes telemetry / application_health / score_samples older than the operational
horizon (24 months). It is **never** pointed at the event spine, the audit log, or
the training store — those are immutable / indefinite by design.

Defense-in-depth (critique #9): it queries the training store (a SEPARATE DB) for
live window ranges and passes them as ``exclude_ranges``, so any source rows still
pinned by a training window are skipped — no cross-DB SQL needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cil.audit.events import AuditRecord
from cil.logging import get_logger
from cil.timeutil import to_us

if TYPE_CHECKING:
    from datetime import datetime

    from cil.storage.interface import (
        ApplicationHealthStore,
        AuditStore,
        ScoreStore,
        TelemetryStore,
        TrainingStore,
    )

_DAY_US = 86_400 * 1_000_000


class RetentionSweeper:
    """Purges aged operational data while protecting pinned training windows."""

    def __init__(
        self,
        telemetry: TelemetryStore,
        app: ApplicationHealthStore,
        score: ScoreStore,
        training: TrainingStore,
        audit: AuditStore | None = None,
        *,
        retention_days: int = 730,
    ) -> None:
        self._telemetry = telemetry
        self._app = app
        self._score = score
        self._training = training
        self._audit = audit
        self._retention_days = retention_days
        self._log = get_logger("cil.storage.retention")

    async def sweep_once(self, now: datetime) -> dict[str, int]:
        """Purge once. ``now`` is injectable so it's testable without the clock."""
        cutoff_us = to_us(now) - self._retention_days * _DAY_US
        # Pin every live training-window range (defense-in-depth across DB files).
        exclude = await self._training.list_window_ranges()

        purged = {
            "telemetry": await self._telemetry.delete_older_than(cutoff_us, exclude_ranges=exclude),
            "application_health": await self._app.delete_older_than(
                cutoff_us, exclude_ranges=exclude
            ),
            "score_samples": await self._score.delete_older_than(cutoff_us, exclude_ranges=exclude),
        }
        total = sum(purged.values())
        self._log.info("retention.sweep", cutoff_us=cutoff_us, purged=total, detail=purged)
        if self._audit is not None and total:
            await self._audit.append(
                AuditRecord(
                    timestamp=now,
                    actor="retention",
                    action="sweep",
                    outcome=f"purged {total} rows",
                    detail=str(purged),
                )
            )
        return purged
