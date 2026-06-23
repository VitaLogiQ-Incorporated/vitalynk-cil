"""WAN monitoring (CIL-202): the telemetry ingest loop.

Polls a ``TelemetryAdapter`` at the configured cadence, persists each normalized
sample to a ``TelemetryStore`` (native resolution), tracks the latest sample and
reachability, and exports Prometheus metrics. This is the ``simulator -> normalize
-> store`` loop that is the Sprint-1 end-to-end exit.

Depends only on the *interfaces* (adapter + store), so it works identically over
the simulator now and the live Ericsson adapter later.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from prometheus_client import Counter, Gauge

from cil.logging import get_logger

if TYPE_CHECKING:
    from cil.storage.interface import TelemetryStore
    from cil.telemetry.adapter import TelemetryAdapter
    from cil.telemetry.schema import TelemetrySample

SAMPLES_TOTAL = Counter(
    "cil_telemetry_samples_total",
    "Telemetry samples ingested.",
    labelnames=("path_id",),
)
REACHABLE = Gauge(
    "cil_telemetry_reachable",
    "WAN reachability of the active path (1=reachable, 0=not).",
    labelnames=("path_id",),
)
LATENCY = Gauge(
    "cil_telemetry_latency_ms",
    "Latest WAN latency of the active path (ms).",
    labelnames=("path_id",),
)


class TelemetryCollector:
    """Samples an adapter on a loop and persists into a store."""

    def __init__(
        self,
        adapter: TelemetryAdapter,
        store: TelemetryStore,
        *,
        interval_s: float = 1.0,
    ) -> None:
        self._adapter = adapter
        self._store = store
        self._interval = interval_s
        self._latest: TelemetrySample | None = None
        self._log = get_logger("cil.telemetry.collector")

    @property
    def latest(self) -> TelemetrySample | None:
        return self._latest

    async def run_once(self) -> TelemetrySample:
        """Sample once, persist, update metrics; return the sample."""
        sample = await self._adapter.sample()
        await self._store.write_sample(sample)
        self._latest = sample

        SAMPLES_TOTAL.labels(sample.path_id).inc()
        REACHABLE.labels(sample.path_id).set(1 if sample.network.reachable else 0)
        if sample.network.latency_ms is not None:
            LATENCY.labels(sample.path_id).set(sample.network.latency_ms)
        return sample

    async def run(
        self,
        *,
        max_samples: int | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> int:
        """Run the ingest loop. Stops after ``max_samples`` or when ``stop_event``
        is set; otherwise runs until cancelled. Returns samples ingested."""
        n = 0
        self._log.info("collector.start", interval_s=self._interval)
        try:
            while True:
                await self.run_once()
                n += 1
                if max_samples is not None and n >= max_samples:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            self._log.info("collector.cancelled", ingested=n)
            raise
        finally:
            self._log.info("collector.stop", ingested=n)
        return n
