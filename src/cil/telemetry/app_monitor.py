"""Application monitoring loop (CIL-203): clinical endpoint liveness.

Probes each clinical endpoint on a cadence, persists results, exposes Prometheus
metrics, tracks state per endpoint, and emits a structured event when an
endpoint's health changes (unreachable / frozen / recovered). Results feed CCS
later (FR-103); for now they are the authoritative application-liveness signal.

The formal audit log + automated labeling (CIL-902 / CIL-303) consume these
events later — here we emit them as structured logs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING

from prometheus_client import Counter, Gauge

from cil.logging import get_logger

if TYPE_CHECKING:
    from cil.storage.interface import ApplicationHealthStore
    from cil.telemetry.probes import ApplicationProbe, ClinicalEndpoint, EndpointHealth

APP_REACHABLE = Gauge(
    "cil_app_reachable",
    "Clinical endpoint reachability (1=reachable).",
    labelnames=("endpoint", "system"),
)
APP_LIVE = Gauge(
    "cil_app_live",
    "Clinical endpoint application-level liveness (1=live).",
    labelnames=("endpoint", "system"),
)
APP_HEALTHY = Gauge(
    "cil_app_healthy",
    "Clinical endpoint meets required probe depth (1=healthy).",
    labelnames=("endpoint", "system"),
)
APP_PROBE_FAILURES = Counter(
    "cil_app_probe_failures_total",
    "Clinical endpoint probes that did not meet required liveness.",
    labelnames=("endpoint", "system"),
)


class ApplicationMonitor:
    """Probes clinical endpoints on a loop and tracks their health."""

    def __init__(
        self,
        probe: ApplicationProbe,
        endpoints: Sequence[ClinicalEndpoint],
        store: ApplicationHealthStore | None = None,
        *,
        interval_s: float = 5.0,
    ) -> None:
        self._probe = probe
        self._endpoints = tuple(endpoints)
        self._store = store
        self._interval = interval_s
        self._latest: dict[str, EndpointHealth] = {}
        self._prev: dict[str, EndpointHealth] = {}
        self._log = get_logger("cil.telemetry.app_monitor")

    @property
    def endpoints(self) -> list[ClinicalEndpoint]:
        return list(self._endpoints)

    @property
    def latest(self) -> dict[str, EndpointHealth]:
        return dict(self._latest)

    async def run_once(self) -> list[EndpointHealth]:
        results: list[EndpointHealth] = []
        for endpoint in self._endpoints:
            health = await self._probe.probe(endpoint)
            results.append(health)
            self._latest[endpoint.name] = health
            if self._store is not None:
                await self._store.write_health(health)

            APP_REACHABLE.labels(health.endpoint, health.system).set(1 if health.reachable else 0)
            APP_LIVE.labels(health.endpoint, health.system).set(1 if health.live else 0)
            APP_HEALTHY.labels(health.endpoint, health.system).set(1 if health.healthy else 0)
            if not health.healthy:
                APP_PROBE_FAILURES.labels(health.endpoint, health.system).inc()

            self._maybe_emit_event(health)
            self._prev[endpoint.name] = health
        return results

    def _maybe_emit_event(self, health: EndpointHealth) -> None:
        prev = self._prev.get(health.endpoint)
        if prev is None:
            if health.healthy:
                return  # first observation healthy => nothing to report
        elif (prev.reachable, prev.live, prev.healthy) == (
            health.reachable,
            health.live,
            health.healthy,
        ):
            return  # unchanged

        if not health.reachable:
            kind = "ENDPOINT_UNREACHABLE"
        elif not health.live:
            kind = "ENDPOINT_FROZEN"  # reachable but not live
        elif prev is not None and not prev.healthy and health.healthy:
            kind = "ENDPOINT_RECOVERED"
        else:
            kind = "ENDPOINT_STATE_CHANGE"

        log = self._log.info if health.healthy else self._log.warning
        log(
            "clinical.endpoint_event",
            event_kind=kind,
            endpoint=health.endpoint,
            system=health.system,
            reachable=health.reachable,
            live=health.live,
            healthy=health.healthy,
            detail=health.detail,
        )

    async def run(
        self,
        *,
        max_rounds: int | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> int:
        rounds = 0
        self._log.info(
            "app_monitor.start", interval_s=self._interval, endpoints=len(self._endpoints)
        )
        try:
            while True:
                await self.run_once()
                rounds += 1
                if max_rounds is not None and rounds >= max_rounds:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            self._log.info("app_monitor.cancelled", rounds=rounds)
            raise
        finally:
            self._log.info("app_monitor.stop", rounds=rounds)
        return rounds
