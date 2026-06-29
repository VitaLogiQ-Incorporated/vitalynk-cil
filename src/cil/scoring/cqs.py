"""Carrier Quality Score (CQS) — CIL-401.

A deterministic 0-100 summary of raw network/carrier quality for one WAN path,
computed from a normalized ``TelemetrySample``. Pure and explainable: each metric
is linearly mapped to a 0-100 sub-score between a configured ``good`` value (scores
100) and ``bad`` value (scores 0), then combined as a weighted average over the
metrics actually present. Reachability gates everything — an unreachable path
scores ``unreachable_score`` regardless of radio stats.

All weights/bounds are config-driven (``config/cqs.yaml``); nothing is hardcoded.
No ML, no decisions — CQS is an input the rest of the brain consumes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cil.audit.events import ScoreKind, ScoreSample

if TYPE_CHECKING:
    from datetime import datetime

    from cil.telemetry.schema import TelemetrySample


class MetricSpec(BaseModel):
    """How one telemetry metric maps to a 0-100 sub-score."""

    model_config = ConfigDict(frozen=True)

    weight: float = Field(gt=0)
    good: float  # value that scores 100
    bad: float  # value that scores 0


# Sensible Tier-1 defaults so the engine works without a config file present.
_DEFAULT_METRICS: dict[str, dict[str, float]] = {
    "sinr": {"weight": 2.0, "good": 25.0, "bad": 0.0},
    "rsrp": {"weight": 1.0, "good": -80.0, "bad": -120.0},
    "rsrq": {"weight": 1.0, "good": -8.0, "bad": -20.0},
    "latency_ms": {"weight": 1.5, "good": 20.0, "bad": 200.0},
    "packet_loss_pct": {"weight": 2.0, "good": 0.0, "bad": 5.0},
    "jitter_ms": {"weight": 1.0, "good": 1.0, "bad": 50.0},
    "throughput_mbps": {"weight": 1.0, "good": 100.0, "bad": 1.0},
}


class CQSConfig(BaseModel):
    """Config for the CQS engine (CIL-401). Loaded from ``config/cqs.yaml``."""

    model_config = ConfigDict(frozen=True)

    unreachable_score: float = Field(default=0.0, ge=0, le=100)
    metrics: dict[str, MetricSpec] = Field(
        default_factory=lambda: {k: MetricSpec(**v) for k, v in _DEFAULT_METRICS.items()}
    )


def load_cqs_config(path: str = "config/cqs.yaml") -> CQSConfig:
    """Load the CQS config (with safe defaults if the file is absent)."""
    if not Path(path).exists():
        return CQSConfig()
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return CQSConfig.model_validate(raw)


def _normalize(value: float, good: float, bad: float) -> float:
    """Linear-map ``value`` to 0-100 where ``good``->100 and ``bad``->0 (clamped)."""
    if good == bad:
        return 100.0
    frac = (value - bad) / (good - bad)
    return max(0.0, min(1.0, frac)) * 100.0


class CQSEngine:
    """Computes the Carrier Quality Score for a path from one telemetry sample."""

    def __init__(self, config: CQSConfig | None = None) -> None:
        self._config = config or CQSConfig()

    def compute(self, sample: TelemetrySample) -> float:
        """Return the 0-100 CQS for ``sample`` (reachability-gated, weighted mean)."""
        if not sample.network.reachable:
            return float(self._config.unreachable_score)

        values: dict[str, float | None] = {
            "rssi": sample.radio.rssi,
            "rsrp": sample.radio.rsrp,
            "rsrq": sample.radio.rsrq,
            "sinr": sample.radio.sinr,
            "latency_ms": sample.network.latency_ms,
            "packet_loss_pct": sample.network.packet_loss_pct,
            "jitter_ms": sample.network.jitter_ms,
            "throughput_mbps": sample.network.throughput_mbps,
            "dns_response_ms": sample.network.dns_response_ms,
        }
        total_w = 0.0
        acc = 0.0
        for name, spec in self._config.metrics.items():
            value = values.get(name)
            if value is None:
                continue
            acc += spec.weight * _normalize(value, spec.good, spec.bad)
            total_w += spec.weight
        if total_w == 0.0:
            return 100.0  # reachable but nothing measurable -> assume healthy
        return round(acc / total_w, 2)

    def score(self, sample: TelemetrySample) -> ScoreSample:
        """Compute the CQS and wrap it as a publishable ``ScoreSample``."""
        return ScoreSample(
            timestamp=sample.timestamp,
            scope="path",
            subject_id=sample.path_id,
            kind=ScoreKind.CQS,
            value=self.compute(sample),
        )

    def score_at(self, sample: TelemetrySample, ts: datetime) -> ScoreSample:
        """Like :meth:`score` but stamped at ``ts`` (e.g. the scoring tick)."""
        return ScoreSample(
            timestamp=ts,
            scope="path",
            subject_id=sample.path_id,
            kind=ScoreKind.CQS,
            value=self.compute(sample),
        )
