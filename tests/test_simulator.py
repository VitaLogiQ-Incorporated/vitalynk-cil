"""Tests for the telemetry simulator: protocol conformance, determinism, scenarios."""

from __future__ import annotations

from cil.telemetry.adapter import TelemetryAdapter
from cil.telemetry.scenarios import Scenario
from cil.telemetry.simulator import SimulatorAdapter


async def _samples(sim: SimulatorAdapter, n: int) -> list:
    return [await sim.sample() for _ in range(n)]


async def test_simulator_satisfies_adapter_protocol() -> None:
    sim = SimulatorAdapter()
    assert isinstance(sim, TelemetryAdapter)
    assert await sim.list_paths() == ["modem-a", "modem-b"]


async def test_healthy_baseline() -> None:
    sim = SimulatorAdapter(seed=1)
    s = await sim.sample()
    assert s.network.reachable is True
    assert s.network.latency_ms is not None and 10 < s.network.latency_ms < 60
    assert s.network.packet_loss_pct is not None and s.network.packet_loss_pct < 1


async def test_deterministic_for_same_seed() -> None:
    a = await _samples(SimulatorAdapter(seed=42), 20)
    b = await _samples(SimulatorAdapter(seed=42), 20)
    assert a == b  # frozen pydantic models compare by value


async def test_different_seeds_differ() -> None:
    a = await _samples(SimulatorAdapter(seed=1), 10)
    b = await _samples(SimulatorAdapter(seed=2), 10)
    assert a != b


async def test_blackout_is_full_outage() -> None:
    sim = SimulatorAdapter(seed=3)
    sim.set_scenario(Scenario.BLACKOUT, duration=5)
    s = await sim.sample()
    assert s.network.reachable is False
    assert s.network.packet_loss_pct == 100.0
    assert s.network.throughput_mbps == 0.0
    assert s.network.latency_ms is None


async def test_latency_spike_ramps_up() -> None:
    sim = SimulatorAdapter(seed=4)
    baseline = await sim.sample()
    sim.set_scenario(Scenario.LATENCY_SPIKE, duration=4)
    peak = (await _samples(sim, 5))[-1]  # reach progress == 1.0
    assert baseline.network.latency_ms is not None
    assert peak.network.latency_ms is not None
    assert peak.network.latency_ms > 200
    assert peak.network.latency_ms > baseline.network.latency_ms


async def test_packet_loss_ramp_increases() -> None:
    sim = SimulatorAdapter(seed=5)
    sim.set_scenario(Scenario.PACKET_LOSS_RAMP, duration=10)
    series = await _samples(sim, 11)
    first = series[0].network.packet_loss_pct
    last = series[-1].network.packet_loss_pct
    assert first is not None and last is not None
    assert last > first
    assert last > 30


async def test_registration_drop_collapses_radio() -> None:
    sim = SimulatorAdapter(seed=6)
    sim.set_scenario(Scenario.REGISTRATION_DROP, duration=6)
    peak = (await _samples(sim, 7))[-1]
    assert peak.radio.rsrp is not None and peak.radio.rsrp < -115
    assert peak.network.reachable is False


async def test_dual_modem_failover_switches_path_and_recovers() -> None:
    sim = SimulatorAdapter(seed=7)
    sim.set_scenario(Scenario.DUAL_MODEM_FAILOVER, duration=10)
    series = await _samples(sim, 11)
    assert series[0].path_id == "modem-a"  # start on primary
    assert series[-1].path_id == "modem-b"  # switched to secondary
    assert series[-1].network.reachable is True  # recovered on secondary
    assert series[-1].carrier == "AT&T"


async def test_scenario_property_reflects_injection() -> None:
    sim = SimulatorAdapter()
    assert sim.scenario == Scenario.HEALTHY
    sim.set_scenario(Scenario.JITTER_BURST, duration=3)
    assert sim.scenario == Scenario.JITTER_BURST


async def test_jitter_burst_actually_raises_jitter() -> None:
    # behavioral: the scenario must ramp jitter well above the healthy baseline,
    # not just flip the .scenario property.
    sim = SimulatorAdapter(seed=1)
    baseline = (await sim.sample()).network.jitter_ms
    sim.set_scenario(Scenario.JITTER_BURST, duration=5)
    peak = max((s.network.jitter_ms or 0.0) for s in await _samples(sim, 6))
    assert baseline is not None and baseline < 6.0  # healthy jitter is low
    assert peak >= 20.0  # ramped toward the ~25ms burst peak
