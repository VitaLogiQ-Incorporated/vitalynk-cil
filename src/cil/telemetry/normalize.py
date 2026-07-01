"""Normalization: raw telemetry (any source) -> the internal ``TelemetrySample``.

This is the layer the live Ericsson adapter and the simulator share. A source
emits a flat mapping of raw fields; ``normalize`` maps them into the typed
schema, coerces types, and detects *missing* fields (a key the source failed to
provide) versus *null* fields (a metric that was present but unmeasurable, e.g.
latency during a blackout). Missing fields are logged for observability
(FR-101: "missing telemetry detected + logged").

The keys in ``RAW_FIELDS`` are the contract a real Ericsson adapter must emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import structlog

from cil.telemetry.schema import (
    DeviceMetrics,
    NetworkMetrics,
    RadioMetrics,
    TelemetrySample,
)
from cil.timeutil import coerce_utc

RawTelemetry = Mapping[str, Any]

# The raw field keys a telemetry source is expected to provide.
RAW_FIELDS: tuple[str, ...] = (
    "timestamp",
    "path_id",
    "carrier",
    "profile",
    "rssi",
    "rsrp",
    "rsrq",
    "sinr",
    "latency_ms",
    "packet_loss_pct",
    "jitter_ms",
    "throughput_mbps",
    "dns_response_ms",
    "reachable",
    "cpu_pct",
    "mem_pct",
    "uptime_s",
)


def _as_float(raw: RawTelemetry, key: str, missing: list[str]) -> float | None:
    """Coerce a raw value to float. Absent key -> missing; present-null -> None."""
    if key not in raw:
        missing.append(key)
        return None
    value = raw[key]
    if value is None:
        return None  # measured-but-unavailable, not a source error
    try:
        return float(value)
    except (TypeError, ValueError):
        missing.append(key)  # present but unparseable == bad/missing
        return None


def _parse_timestamp(value: Any) -> tuple[datetime, bool]:
    """Parse a raw timestamp to UTC. Returns ``(utc, was_naive)`` — naive inputs are
    assumed UTC (and flagged so the caller warns), aware inputs are converted via
    ``astimezone`` so a non-UTC offset is handled consistently with the schema."""
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return coerce_utc(parsed)


def normalize(
    raw: RawTelemetry,
    *,
    logger: structlog.stdlib.BoundLogger | None = None,
) -> TelemetrySample:
    """Map a raw telemetry mapping into a validated ``TelemetrySample``."""
    missing: list[str] = []

    radio = RadioMetrics(
        rssi=_as_float(raw, "rssi", missing),
        rsrp=_as_float(raw, "rsrp", missing),
        rsrq=_as_float(raw, "rsrq", missing),
        sinr=_as_float(raw, "sinr", missing),
    )

    if "reachable" not in raw:
        missing.append("reachable")
    network = NetworkMetrics(
        latency_ms=_as_float(raw, "latency_ms", missing),
        packet_loss_pct=_as_float(raw, "packet_loss_pct", missing),
        jitter_ms=_as_float(raw, "jitter_ms", missing),
        throughput_mbps=_as_float(raw, "throughput_mbps", missing),
        dns_response_ms=_as_float(raw, "dns_response_ms", missing),
        reachable=bool(raw.get("reachable", False)),
    )

    device = DeviceMetrics(
        cpu_pct=_as_float(raw, "cpu_pct", missing),
        mem_pct=_as_float(raw, "mem_pct", missing),
        uptime_s=_as_float(raw, "uptime_s", missing),
    )

    naive_ts = False
    if "timestamp" in raw and raw["timestamp"] is not None:
        timestamp, naive_ts = _parse_timestamp(raw["timestamp"])
    else:
        missing.append("timestamp")
        timestamp = datetime.now(tz=UTC)

    sample = TelemetrySample(
        timestamp=timestamp,
        path_id=str(raw.get("path_id", "unknown")),
        carrier=str(raw.get("carrier", "unknown")),
        profile=str(raw.get("profile", "unknown")),
        radio=radio,
        network=network,
        device=device,
    )

    if missing and logger is not None:
        logger.warning(
            "telemetry.missing_fields",
            fields=sorted(set(missing)),
            path_id=sample.path_id,
        )
    if naive_ts and logger is not None:
        # A naive source timestamp was assumed to be UTC — surface it rather than
        # letting silent clock skew leak into the indefinite training windows.
        logger.warning("telemetry.naive_timestamp", path_id=sample.path_id)

    return sample
