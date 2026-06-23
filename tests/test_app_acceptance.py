"""CIL-203 acceptance (VIT-98): application monitoring acceptance criteria.

Criteria: application probe status recorded; reachability changes detected; probe
failures generate events; results feed CCS. ("Feed CCS" = the liveness signal is
produced and queryable; CCS consumption itself is CIL-402, Sprint 3.)
"""

from __future__ import annotations

from structlog.testing import capture_logs

from cil.storage.memory import InMemoryApplicationHealthStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.probes import DEFAULT_CLINICAL_ENDPOINTS
from cil.telemetry.simprobe import EndpointCondition, SimulatedClinicalProbe


async def test_application_monitoring_acceptance() -> None:
    probe = SimulatedClinicalProbe(seed=1)
    store = InMemoryApplicationHealthStore()
    await store.setup()
    monitor = ApplicationMonitor(probe, DEFAULT_CLINICAL_ENDPOINTS, store)

    n = len(DEFAULT_CLINICAL_ENDPOINTS)

    # 1) status recorded
    await monitor.run_once()
    assert await store.count() == n
    assert len(monitor.latest) == n

    # 2) reachability/liveness change detected + 3) probe failures generate events
    probe.set_condition("epic-ehr", EndpointCondition.FROZEN)
    with capture_logs() as logs:
        await monitor.run_once()
    events = [
        e
        for e in logs
        if e.get("event") == "clinical.endpoint_event" and e.get("endpoint") == "epic-ehr"
    ]
    assert events and events[0]["event_kind"] == "ENDPOINT_FROZEN"
    assert monitor.latest["epic-ehr"].live is False  # reachable but not live

    # 4) results feed CCS: the liveness signal is produced + queryable downstream
    assert await store.count() == 2 * n
    recorded = await store.read_health(endpoint="epic-ehr", limit=10)
    assert recorded[-1].live is False
    # the latest per-endpoint signal CCS will consume:
    assert {h.endpoint for h in monitor.latest.values()} == {
        e.name for e in DEFAULT_CLINICAL_ENDPOINTS
    }

    # recovery is detected too
    probe.set_condition("epic-ehr", EndpointCondition.HEALTHY)
    with capture_logs() as logs:
        await monitor.run_once()
    recovered = [
        e
        for e in logs
        if e.get("event") == "clinical.endpoint_event" and e.get("endpoint") == "epic-ehr"
    ]
    assert recovered and recovered[0]["event_kind"] == "ENDPOINT_RECOVERED"
    assert monitor.latest["epic-ehr"].live is True
