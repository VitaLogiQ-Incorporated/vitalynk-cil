"""Carrier Quality Score (CIL-401) — scoring math, gating, config-driven behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from cil.audit.events import ScoreKind
from cil.scoring.cqs import CQSConfig, CQSEngine, MetricSpec, load_cqs_config
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def sample(*, reachable: bool = True, **net: float) -> TelemetrySample:
    radio = RadioMetrics(
        rssi=net.pop("rssi", -60.0),
        rsrp=net.pop("rsrp", -80.0),
        rsrq=net.pop("rsrq", -8.0),
        sinr=net.pop("sinr", 25.0),
    )
    network = NetworkMetrics(
        latency_ms=net.pop("latency_ms", 20.0),
        packet_loss_pct=net.pop("packet_loss_pct", 0.0),
        jitter_ms=net.pop("jitter_ms", 1.0),
        throughput_mbps=net.pop("throughput_mbps", 100.0),
        dns_response_ms=net.pop("dns_response_ms", 10.0),
        reachable=reachable,
    )
    return TelemetrySample(
        timestamp=BASE,
        path_id="modem-a",
        carrier="Verizon",
        profile="primary",
        radio=radio,
        network=network,
        device=DeviceMetrics(),
    )


def test_pristine_sample_scores_near_100() -> None:
    # all metrics at their "good" bound -> ~100
    assert CQSEngine().compute(sample()) == 100.0


def test_terrible_sample_scores_near_0() -> None:
    bad = sample(
        sinr=0.0,
        rsrp=-120.0,
        rsrq=-20.0,
        latency_ms=200.0,
        packet_loss_pct=5.0,
        jitter_ms=50.0,
        throughput_mbps=1.0,
    )
    assert CQSEngine().compute(bad) == 0.0


def test_unreachable_is_gated_to_unreachable_score() -> None:
    # even with perfect radio, an unreachable path scores the configured floor
    assert CQSEngine().compute(sample(reachable=False)) == 0.0


def test_values_beyond_bounds_are_clamped() -> None:
    # better-than-good and worse-than-bad don't push outside 0-100
    great = sample(sinr=999.0, latency_ms=0.0)
    assert CQSEngine().compute(great) == 100.0
    awful = sample(
        sinr=-999.0,
        latency_ms=99999.0,
        packet_loss_pct=100.0,
        jitter_ms=9999.0,
        rsrp=-200.0,
        rsrq=-99.0,
        throughput_mbps=0.0,
    )
    assert CQSEngine().compute(awful) == 0.0


def test_missing_metrics_are_skipped_weighted_over_present() -> None:
    # only sinr present (perfect) + everything else None -> 100 over the one metric
    s = TelemetrySample(
        timestamp=BASE,
        path_id="modem-a",
        carrier="c",
        profile="p",
        radio=RadioMetrics(sinr=25.0),
        network=NetworkMetrics(reachable=True),
        device=DeviceMetrics(),
    )
    assert CQSEngine().compute(s) == 100.0


def test_config_override_changes_score() -> None:
    # a single-metric config makes the score exactly that metric's sub-score
    cfg = CQSConfig(metrics={"latency_ms": MetricSpec(weight=1.0, good=0.0, bad=100.0)})
    # latency 50 -> halfway between good(0) and bad(100) -> 50
    assert CQSEngine(cfg).compute(sample(latency_ms=50.0)) == 50.0


def test_score_wraps_as_cqs_scoresample() -> None:
    out = CQSEngine().score(sample())
    assert out.kind is ScoreKind.CQS
    assert out.subject_id == "modem-a"
    assert out.scope == "path"
    assert out.value == 100.0


def test_default_config_loads_when_file_absent() -> None:
    cfg = load_cqs_config("config/does-not-exist.yaml")
    assert "sinr" in cfg.metrics  # falls back to built-in defaults
