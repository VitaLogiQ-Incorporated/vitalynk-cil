"""Tests for the application probe model + simulated clinical probe."""

from __future__ import annotations

from cil.telemetry.probes import (
    ApplicationProbe,
    ClinicalEndpoint,
    ProbeDepth,
)
from cil.telemetry.simprobe import EndpointCondition, SimulatedClinicalProbe

EPIC = ClinicalEndpoint(name="epic-ehr", system="Epic", target="https://epic.local/health")
OR = ClinicalEndpoint(
    name="or-systems",
    system="OR",
    target="https://or.local/health",
    required_depth=ProbeDepth.RENDER_STATE,
)


async def test_simprobe_is_an_application_probe() -> None:
    assert isinstance(SimulatedClinicalProbe(), ApplicationProbe)


async def test_healthy_endpoint() -> None:
    probe = SimulatedClinicalProbe(seed=1)
    h = await probe.probe(EPIC)
    assert h.reachable and h.live and h.healthy
    assert h.depth_achieved == ProbeDepth.APP_RESPONSE


async def test_frozen_is_reachable_but_not_live() -> None:
    # The frozen-OR-screen differentiator.
    probe = SimulatedClinicalProbe()
    probe.set_condition("epic-ehr", EndpointCondition.FROZEN)
    h = await probe.probe(EPIC)
    assert h.reachable is True
    assert h.live is False
    assert h.healthy is False
    assert h.depth_achieved == ProbeDepth.IP
    assert h.detail is not None and "frozen" in h.detail


async def test_unreachable_endpoint() -> None:
    probe = SimulatedClinicalProbe()
    probe.set_condition("epic-ehr", EndpointCondition.UNREACHABLE)
    h = await probe.probe(EPIC)
    assert h.reachable is False
    assert h.live is False
    assert h.depth_achieved is None


async def test_slow_endpoint_is_live_but_high_latency() -> None:
    probe = SimulatedClinicalProbe(seed=2)
    probe.set_condition("epic-ehr", EndpointCondition.SLOW)
    h = await probe.probe(EPIC)
    assert h.live is True
    assert h.latency_ms is not None and h.latency_ms > 800


async def test_render_state_capped_and_flagged() -> None:
    probe = SimulatedClinicalProbe(seed=3)
    h = await probe.probe(OR)
    # Verified only to APP_RESPONSE; treated as healthy but the gap is flagged.
    assert h.depth_achieved == ProbeDepth.APP_RESPONSE
    assert h.required_depth == ProbeDepth.RENDER_STATE
    assert h.healthy is True
    assert h.detail is not None and "render-state" in h.detail
