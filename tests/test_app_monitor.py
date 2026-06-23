"""Tests for the application monitor: probing, persistence, events, metrics."""

from __future__ import annotations

from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from cil.storage.memory import InMemoryApplicationHealthStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.probes import DEFAULT_CLINICAL_ENDPOINTS
from cil.telemetry.simprobe import EndpointCondition, SimulatedClinicalProbe


def _live(endpoint: str, system: str) -> float:
    return (
        REGISTRY.get_sample_value("cil_app_live", {"endpoint": endpoint, "system": system}) or 0.0
    )


async def test_run_once_probes_all_and_persists() -> None:
    store = InMemoryApplicationHealthStore()
    await store.setup()
    monitor = ApplicationMonitor(SimulatedClinicalProbe(), DEFAULT_CLINICAL_ENDPOINTS, store)

    results = await monitor.run_once()
    assert len(results) == len(DEFAULT_CLINICAL_ENDPOINTS)
    assert len(monitor.latest) == len(DEFAULT_CLINICAL_ENDPOINTS)
    assert await store.count() == len(DEFAULT_CLINICAL_ENDPOINTS)


async def test_frozen_endpoint_emits_event_and_metric() -> None:
    probe = SimulatedClinicalProbe()
    probe.set_condition("epic-ehr", EndpointCondition.FROZEN)
    monitor = ApplicationMonitor(probe, DEFAULT_CLINICAL_ENDPOINTS)

    with capture_logs() as logs:
        await monitor.run_once()

    frozen = [
        e
        for e in logs
        if e.get("event") == "clinical.endpoint_event" and e.get("endpoint") == "epic-ehr"
    ]
    assert frozen and frozen[0]["event_kind"] == "ENDPOINT_FROZEN"
    assert _live("epic-ehr", "Epic") == 0.0


async def test_recovery_emits_event() -> None:
    probe = SimulatedClinicalProbe()
    probe.set_condition("cerner", EndpointCondition.UNREACHABLE)
    monitor = ApplicationMonitor(probe, DEFAULT_CLINICAL_ENDPOINTS)

    await monitor.run_once()  # cerner unreachable
    probe.set_condition("cerner", EndpointCondition.HEALTHY)
    with capture_logs() as logs:
        await monitor.run_once()  # cerner recovers

    recovered = [
        e
        for e in logs
        if e.get("endpoint") == "cerner" and e.get("event") == "clinical.endpoint_event"
    ]
    assert recovered and recovered[0]["event_kind"] == "ENDPOINT_RECOVERED"
    assert _live("cerner", "Cerner") == 1.0


async def test_run_loop_rounds() -> None:
    monitor = ApplicationMonitor(
        SimulatedClinicalProbe(), DEFAULT_CLINICAL_ENDPOINTS, interval_s=0.0
    )
    rounds = await monitor.run(max_rounds=3)
    assert rounds == 3
