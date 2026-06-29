"""Scoring loop (EPIC-04 wiring) — produces CQS/CCS on a tick and publishes them.

Each tick it reads the latest telemetry + clinical health, computes CQS (per path)
and CCS (site), persists both as ``ScoreSample`` rows (so ``/scores`` lights up),
and emits a persist-only ``SCORE_SAMPLE`` event onto the bus so the CIL-303 labeler
sees the CCS timeline and can detect sustained ``SLA_BREACH``.

It also runs a small SLA edge-detector: when CCS stays below the CCS-001 outage
threshold for the sustained window, it emits one anchoring ``SLA_STATE`` event —
which the pipeline captures a telemetry window for and the labeler tags
``SLA_BREACH``. Thresholds come from CCS-001; nothing is hardcoded. (Richer
decisioning on these signals is EPIC-06.)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cil.audit.events import ContinuityEvent, EventKind, EventSource, new_event_id
from cil.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from cil.audit.bus import EventBus
    from cil.scoring.ccs import CCSEngine
    from cil.scoring.cqs import CQSEngine
    from cil.storage.interface import ScoreStore
    from cil.telemetry.probes import EndpointHealth
    from cil.telemetry.schema import TelemetrySample

    TelemetryProvider = Callable[[], TelemetrySample | None]
    HealthProvider = Callable[[], list[EndpointHealth]]


class ScoringService:
    """Computes CQS/CCS on a loop and publishes scores + SLA-state events."""

    def __init__(
        self,
        *,
        cqs: CQSEngine,
        ccs: CCSEngine,
        score_store: ScoreStore,
        telemetry_provider: TelemetryProvider,
        health_provider: HealthProvider,
        bus: EventBus | None = None,
        outage_threshold: float = 40.0,
        sla_sustain_s: float = 5.0,
        primary_path: str = "modem-a",
        site_id: str = "site",
    ) -> None:
        self._cqs = cqs
        self._ccs = ccs
        self._score_store = score_store
        self._telemetry_provider = telemetry_provider
        self._health_provider = health_provider
        self._bus = bus
        self._outage_threshold = outage_threshold
        self._sla_sustain_s = sla_sustain_s
        self._primary_path = primary_path
        self._site_id = site_id
        self._below_since: datetime | None = None
        self._breach_emitted = False
        self._seq = 0
        self._log = get_logger("cil.scoring.service")

    async def tick(self, now: datetime) -> dict[str, object]:
        """Score once: persist CQS/CCS, feed the labeler, maybe raise SLA_STATE."""
        sample = self._telemetry_provider()
        healths = self._health_provider()

        cqs_val: float | None = self._cqs.compute(sample) if sample is not None else None
        carrier = cqs_val if cqs_val is not None else 100.0
        ccs_sample = self._ccs.score(healths, carrier, now, subject_id=self._site_id)

        await self._score_store.write_score(ccs_sample)
        if sample is not None and cqs_val is not None:
            await self._score_store.write_score(self._cqs.score_at(sample, now))

        if self._bus is not None:
            self._seq += 1
            await self._bus.emit(
                ContinuityEvent(
                    event_id=new_event_id(now, EventKind.SCORE_SAMPLE, str(self._seq)),
                    timestamp=now,
                    kind=EventKind.SCORE_SAMPLE,
                    source=EventSource.SCORING,
                    path_id=self._primary_path,
                    cqs=cqs_val,
                    ccs=ccs_sample.value,
                    ccs_tier=ccs_sample.tier,
                )
            )
            await self._maybe_emit_sla(now, ccs_sample.value, ccs_sample.tier)

        return {"cqs": cqs_val, "ccs": ccs_sample.value, "tier": ccs_sample.tier}

    async def _maybe_emit_sla(self, now: datetime, ccs_val: float, tier: str | None) -> None:
        if self._bus is None:
            return
        if ccs_val < self._outage_threshold:
            if self._below_since is None:
                self._below_since = now
            sustained = (now - self._below_since).total_seconds()
            if sustained >= self._sla_sustain_s and not self._breach_emitted:
                self._breach_emitted = True
                self._seq += 1
                await self._bus.emit(
                    ContinuityEvent(
                        event_id=new_event_id(now, EventKind.SLA_STATE, str(self._seq)),
                        timestamp=now,
                        kind=EventKind.SLA_STATE,
                        source=EventSource.SCORING,
                        path_id=self._primary_path,
                        ccs=ccs_val,
                        ccs_tier=tier,
                        sla_breaching=True,
                        sustained_s=sustained,
                    )
                )
                self._log.warning("scoring.sla_breach", ccs=ccs_val, sustained_s=sustained)
        else:
            self._below_since = None
            self._breach_emitted = False

    async def run(
        self,
        *,
        interval_s: float = 1.0,
        max_rounds: int | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> int:
        rounds = 0
        self._log.info("scoring.start", interval_s=interval_s)
        try:
            while True:
                await self.tick(datetime.now(UTC))
                rounds += 1
                if max_rounds is not None and rounds >= max_rounds:
                    break
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                        break
                    except TimeoutError:
                        continue
                await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise
        return rounds
