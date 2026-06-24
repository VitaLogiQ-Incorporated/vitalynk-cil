"""Canonical time handling (EPIC-03 critique #1): UTC enforcement + ts_us."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from cil.telemetry.probes import EndpointHealth, ProbeDepth
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample
from cil.timeutil import ensure_utc, from_us, to_us


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ensure_utc(datetime(2026, 1, 1))


def test_ensure_utc_coerces_offset_to_utc() -> None:
    est = timezone(timedelta(hours=-5))
    coerced = ensure_utc(datetime(2026, 1, 1, 7, 0, 0, tzinfo=est))
    assert coerced == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_to_us_from_us_roundtrip() -> None:
    dt = datetime(2026, 6, 1, 12, 0, 0, 500000, tzinfo=UTC)
    assert from_us(to_us(dt)) == dt


def test_telemetry_sample_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        TelemetrySample(
            timestamp=datetime(2026, 1, 1),
            path_id="p",
            carrier="c",
            profile="x",
            radio=RadioMetrics(),
            network=NetworkMetrics(reachable=True),
            device=DeviceMetrics(),
        )


def test_endpoint_health_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        EndpointHealth(
            timestamp=datetime(2026, 1, 1),
            endpoint="e",
            system="s",
            reachable=True,
            live=True,
            healthy=True,
            depth_achieved=ProbeDepth.APP_RESPONSE,
            required_depth=ProbeDepth.APP_RESPONSE,
        )
