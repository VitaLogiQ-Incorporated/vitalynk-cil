"""Failure scenarios for the telemetry simulator (CIL-204).

Maps to the SLA resilience scenarios the system must survive: healthy operation,
latency spikes, packet-loss ramps, jitter bursts, registration (radio) drops,
full blackouts, and dual-modem failover. Each scenario shapes a healthy baseline
toward a degraded state as ``progress`` goes 0.0 -> 1.0 across its duration.

Shaping is deterministic given the same RNG, so scenarios are repeatable — a
requirement for using this as the permanent resilience-test harness.
"""

from __future__ import annotations

from enum import StrEnum
from random import Random

# A raw metric mapping (pre-normalization), values in source units.
Metrics = dict[str, float | bool | None]


class Scenario(StrEnum):
    HEALTHY = "healthy"
    LATENCY_SPIKE = "latency_spike"
    PACKET_LOSS_RAMP = "packet_loss_ramp"
    JITTER_BURST = "jitter_burst"
    REGISTRATION_DROP = "registration_drop"
    BLACKOUT = "blackout"
    DUAL_MODEM_FAILOVER = "dual_modem_failover"


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _noise(rng: Random, amplitude: float) -> float:
    return (rng.random() * 2.0 - 1.0) * amplitude


def healthy_metrics(rng: Random) -> Metrics:
    """A realistic healthy sample with small natural noise."""
    return {
        "rssi": -65.0 + _noise(rng, 3.0),
        "rsrp": -90.0 + _noise(rng, 4.0),
        "rsrq": -10.0 + _noise(rng, 2.0),
        "sinr": 15.0 + _noise(rng, 3.0),
        "latency_ms": 25.0 + _noise(rng, 4.0),
        "packet_loss_pct": max(0.0, 0.1 + _noise(rng, 0.1)),
        "jitter_ms": max(0.0, 2.0 + _noise(rng, 1.0)),
        "throughput_mbps": 85.0 + _noise(rng, 8.0),
        "dns_response_ms": 15.0 + _noise(rng, 3.0),
        "reachable": True,
        "cpu_pct": 20.0 + _noise(rng, 5.0),
        "mem_pct": 45.0 + _noise(rng, 5.0),
    }


def shape_metrics(scenario: Scenario, progress: float, rng: Random) -> Metrics:
    """Return raw metrics for ``scenario`` at ``progress`` in [0.0, 1.0]."""
    p = max(0.0, min(progress, 1.0))
    m = healthy_metrics(rng)

    if scenario == Scenario.HEALTHY:
        return m

    if scenario == Scenario.LATENCY_SPIKE:
        m["latency_ms"] = _lerp(25.0, 450.0, p) + _noise(rng, 10.0 * p)
        m["jitter_ms"] = _lerp(2.0, 25.0, p)
        m["packet_loss_pct"] = _lerp(0.1, 3.0, p)
        m["throughput_mbps"] = _lerp(85.0, 55.0, p)
        return m

    if scenario == Scenario.PACKET_LOSS_RAMP:
        loss = _lerp(0.1, 45.0, p)
        m["packet_loss_pct"] = loss
        m["throughput_mbps"] = _lerp(85.0, 8.0, p)
        m["latency_ms"] = _lerp(25.0, 220.0, p)
        m["reachable"] = loss < 35.0
        return m

    if scenario == Scenario.JITTER_BURST:
        m["jitter_ms"] = _lerp(2.0, 70.0, p)
        m["latency_ms"] = 25.0 + rng.random() * _lerp(0.0, 160.0, p)
        return m

    if scenario == Scenario.REGISTRATION_DROP:
        m["rssi"] = _lerp(-65.0, -110.0, p)
        m["rsrp"] = _lerp(-90.0, -125.0, p)
        m["rsrq"] = _lerp(-10.0, -20.0, p)
        m["sinr"] = _lerp(15.0, -8.0, p)
        m["throughput_mbps"] = _lerp(85.0, 0.0, p)
        if p >= 0.5:
            m["reachable"] = False
            m["latency_ms"] = None
            m["dns_response_ms"] = None
        return m

    if scenario == Scenario.BLACKOUT:
        # Full outage for the whole scenario, independent of progress.
        m.update(
            reachable=False,
            packet_loss_pct=100.0,
            throughput_mbps=0.0,
            latency_ms=None,
            dns_response_ms=None,
            rssi=-115.0,
            rsrp=-130.0,
            rsrq=-25.0,
            sinr=-15.0,
        )
        return m

    if scenario == Scenario.DUAL_MODEM_FAILOVER:
        # First half: the active modem degrades to a blackout. Second half: the
        # simulator has switched to the healthy secondary modem (handled there),
        # so metrics return to healthy.
        if p < 0.5:
            local = p / 0.5
            m["packet_loss_pct"] = _lerp(0.1, 100.0, local)
            m["throughput_mbps"] = _lerp(85.0, 0.0, local)
            m["latency_ms"] = _lerp(25.0, 600.0, local)
            m["sinr"] = _lerp(15.0, -10.0, local)
            m["reachable"] = local < 0.8
        return m

    return m
