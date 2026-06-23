"""The internal telemetry schema — the one contract everything normalizes into.

This is *the* seam between telemetry sources (Ericsson adapter, simulator), the
store, and the scoring engines. Every adapter must produce a ``TelemetrySample``;
nothing downstream knows where the data came from.

All metric fields are optional (``None`` = not measured / unavailable) except
``network.reachable``, which is always known. Numeric ranges are validated so a
bad source surfaces as a ValidationError rather than poisoning the scores.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RadioMetrics(BaseModel):
    """Cellular radio quality for the sampled path."""

    model_config = ConfigDict(frozen=True)

    rssi: float | None = Field(default=None, description="Received signal strength (dBm)")
    rsrp: float | None = Field(default=None, description="Reference signal received power (dBm)")
    rsrq: float | None = Field(default=None, description="Reference signal received quality (dB)")
    sinr: float | None = Field(default=None, description="Signal-to-interference-plus-noise (dB)")


class NetworkMetrics(BaseModel):
    """WAN/network path health for the sampled path."""

    model_config = ConfigDict(frozen=True)

    latency_ms: float | None = Field(default=None, ge=0)
    packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    jitter_ms: float | None = Field(default=None, ge=0)
    throughput_mbps: float | None = Field(default=None, ge=0)
    dns_response_ms: float | None = Field(default=None, ge=0)
    reachable: bool = Field(description="WAN/application reachability at sample time")


class DeviceMetrics(BaseModel):
    """Host device health (the Ericsson E400)."""

    model_config = ConfigDict(frozen=True)

    cpu_pct: float | None = Field(default=None, ge=0, le=100)
    mem_pct: float | None = Field(default=None, ge=0, le=100)
    uptime_s: float | None = Field(default=None, ge=0)


class TelemetrySample(BaseModel):
    """One normalized telemetry observation for a single WAN path.

    Immutable (frozen): a sample is a record of what was observed and is never
    mutated after creation.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime = Field(description="When the sample was observed (UTC).")
    path_id: str = Field(description="WAN path / modem identifier this sample is for.")
    carrier: str = Field(description="Carrier serving this path at sample time.")
    profile: str = Field(description="SIM/eSIM profile in use on this path.")

    radio: RadioMetrics
    network: NetworkMetrics
    device: DeviceMetrics
