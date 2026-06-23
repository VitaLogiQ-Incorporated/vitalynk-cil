"""Tests for the telemetry schema: validity, immutability, range constraints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cil.telemetry.schema import (
    DeviceMetrics,
    NetworkMetrics,
    RadioMetrics,
    TelemetrySample,
)


def _sample() -> TelemetrySample:
    return TelemetrySample(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        path_id="modem-a",
        carrier="Verizon",
        profile="primary",
        radio=RadioMetrics(rssi=-65, rsrp=-90, rsrq=-10, sinr=15),
        network=NetworkMetrics(
            latency_ms=25,
            packet_loss_pct=0.1,
            jitter_ms=2,
            throughput_mbps=85,
            dns_response_ms=15,
            reachable=True,
        ),
        device=DeviceMetrics(cpu_pct=20, mem_pct=45, uptime_s=3600),
    )


def test_valid_sample_constructs() -> None:
    s = _sample()
    assert s.network.reachable is True
    assert s.radio.sinr == 15
    assert s.path_id == "modem-a"


def test_sample_is_frozen() -> None:
    s = _sample()
    with pytest.raises(ValidationError):
        s.path_id = "modem-b"  # type: ignore[misc]


def test_optional_metrics_default_none() -> None:
    r = RadioMetrics()
    assert r.rssi is None and r.sinr is None


def test_packet_loss_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        NetworkMetrics(packet_loss_pct=150, reachable=True)


def test_negative_latency_rejected() -> None:
    with pytest.raises(ValidationError):
        NetworkMetrics(latency_ms=-1, reachable=True)


def test_cpu_over_100_rejected() -> None:
    with pytest.raises(ValidationError):
        DeviceMetrics(cpu_pct=101)
