"""Tests for normalization: completeness, missing vs null, coercion, timestamps."""

from __future__ import annotations

from datetime import UTC, datetime

from structlog.testing import capture_logs

from cil.logging import get_logger
from cil.telemetry.normalize import normalize


def _complete_raw() -> dict[str, object]:
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "path_id": "modem-a",
        "carrier": "Verizon",
        "profile": "primary",
        "rssi": -65,
        "rsrp": -90,
        "rsrq": -10,
        "sinr": 15,
        "latency_ms": 25,
        "packet_loss_pct": 0.1,
        "jitter_ms": 2,
        "throughput_mbps": 85,
        "dns_response_ms": 15,
        "reachable": True,
        "cpu_pct": 20,
        "mem_pct": 45,
        "uptime_s": 3600,
    }


def test_complete_raw_normalizes() -> None:
    s = normalize(_complete_raw())
    assert s.carrier == "Verizon"
    assert s.network.latency_ms == 25.0
    assert s.network.reachable is True
    assert s.device.uptime_s == 3600.0
    assert isinstance(s.network.latency_ms, float)


def test_missing_field_is_detected_and_logged() -> None:
    raw = _complete_raw()
    del raw["latency_ms"]
    del raw["sinr"]
    with capture_logs() as logs:
        s = normalize(raw, logger=get_logger("test"))
    assert s.network.latency_ms is None
    assert s.radio.sinr is None
    warnings = [e for e in logs if e["event"] == "telemetry.missing_fields"]
    assert warnings, "expected a missing_fields warning"
    assert "latency_ms" in warnings[0]["fields"]
    assert "sinr" in warnings[0]["fields"]


def test_present_null_is_not_missing() -> None:
    # latency present but null (e.g. unmeasurable during a blackout) != missing.
    raw = _complete_raw()
    raw["latency_ms"] = None
    with capture_logs() as logs:
        s = normalize(raw, logger=get_logger("test"))
    assert s.network.latency_ms is None
    warnings = [e for e in logs if e["event"] == "telemetry.missing_fields"]
    assert not warnings, "present-null must not be reported as missing"


def test_unparseable_value_treated_as_missing() -> None:
    raw = _complete_raw()
    raw["throughput_mbps"] = "not-a-number"
    with capture_logs() as logs:
        s = normalize(raw, logger=get_logger("test"))
    assert s.network.throughput_mbps is None
    warnings = [e for e in logs if e["event"] == "telemetry.missing_fields"]
    assert "throughput_mbps" in warnings[0]["fields"]


def test_timestamp_accepts_datetime_and_adds_utc() -> None:
    raw = _complete_raw()
    raw["timestamp"] = datetime(2026, 6, 1, 12, 0, 0)  # naive
    s = normalize(raw)
    assert s.timestamp.tzinfo is not None
    assert s.timestamp == datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_unknown_context_defaults() -> None:
    s = normalize({"reachable": True})
    assert s.carrier == "unknown"
    assert s.path_id == "unknown"
    assert s.profile == "unknown"
