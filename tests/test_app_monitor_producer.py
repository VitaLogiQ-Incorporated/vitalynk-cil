"""ApplicationMonitor producer hook — endpoint state-changes feed the event sink."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.probes import ClinicalEndpoint, EndpointHealth, ProbeDepth

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
EP = ClinicalEndpoint(
    name="epic", system="Epic", target="https://epic", required_depth=ProbeDepth.APP_RESPONSE
)


class ScriptedProbe:
    """Returns a fixed sequence of healths: healthy -> frozen -> recovered."""

    def __init__(self) -> None:
        self._i = -1
        self._script = [
            (True, True, True),  # healthy
            (True, False, False),  # reachable but frozen
            (True, True, True),  # recovered
        ]

    async def probe(self, endpoint: ClinicalEndpoint) -> EndpointHealth:
        self._i += 1
        reachable, live, healthy = self._script[self._i]
        return EndpointHealth(
            timestamp=BASE + timedelta(seconds=self._i),
            endpoint=endpoint.name,
            system=endpoint.system,
            reachable=reachable,
            live=live,
            healthy=healthy,
            depth_achieved=endpoint.required_depth if healthy else None,
            required_depth=endpoint.required_depth,
        )


async def test_state_changes_published_to_sink_not_steady_state() -> None:
    captured: list[tuple[str, str]] = []

    async def sink(health: EndpointHealth, kind: str) -> None:
        captured.append((kind, health.endpoint))

    monitor = ApplicationMonitor(ScriptedProbe(), [EP], event_sink=sink)
    for _ in range(3):
        await monitor.run_once()

    # round 0 (first observation, healthy) emits nothing; rounds 1 & 2 are changes.
    assert captured == [("ENDPOINT_FROZEN", "epic"), ("ENDPOINT_RECOVERED", "epic")]


async def test_no_sink_is_safe() -> None:
    monitor = ApplicationMonitor(ScriptedProbe(), [EP])  # no event_sink wired
    healths = await monitor.run_once()
    assert healths[0].healthy is True  # runs fine without a sink
