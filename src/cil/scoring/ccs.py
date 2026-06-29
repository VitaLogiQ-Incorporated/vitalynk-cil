"""Clinical Continuity Score (CCS) — CIL-402, the authoritative decision metric.

A deterministic 0-100 answer to "can the hospital still reach its clinical
systems?", combining clinical-endpoint liveness (CIL-203) with carrier quality
(CQS). Clinical health dominates; a single down critical system pulls the score
down via a configurable worst-case blend (a healthy *average* must not mask one
dead EHR). The result is classified into the approved ``CCS-001`` tier matrix.

Tiers + the OUTAGE threshold come from ``config/ccs_tiers.yaml`` (CCS-001);
weights + per-state endpoint scores from ``config/ccs.yaml``. Nothing hardcoded,
no ML. Sustained ``SLA_BREACH`` detection (CCS < threshold for N s) lives in the
CIL-303 labeler, which consumes the score samples this engine publishes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cil.audit.events import ScoreKind, ScoreSample

if TYPE_CHECKING:
    from datetime import datetime

    from cil.telemetry.probes import EndpointHealth


class EndpointScores(BaseModel):
    """The 0-100 contribution of a clinical endpoint by its observed state."""

    model_config = ConfigDict(frozen=True)

    healthy: float = 100.0  # meets its required probe depth
    degraded: float = 60.0  # reachable + live but below required depth
    frozen: float = 30.0  # reachable but not live (the frozen-screen case)
    unreachable: float = 0.0  # link/IP not reachable


class CCSConfig(BaseModel):
    """Config for the CCS engine (CIL-402). Loaded from ``config/ccs.yaml``."""

    model_config = ConfigDict(frozen=True)

    clinical_weight: float = Field(default=0.8, ge=0)
    carrier_weight: float = Field(default=0.2, ge=0)
    # within the clinical component, how much the *worst* endpoint counts vs the mean
    worst_weight: float = Field(default=0.5, ge=0, le=1)
    endpoint_scores: EndpointScores = Field(default_factory=EndpointScores)


class Tier(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    min: float
    max: float


_DEFAULT_TIERS = [
    Tier(name="Protected", min=90, max=100),
    Tier(name="Stable", min=75, max=89),
    Tier(name="Degraded", min=60, max=74),
    Tier(name="Breach Risk", min=40, max=59),
    Tier(name="OUTAGE", min=0, max=39),
]


class CCSTiers(BaseModel):
    """The CCS-001 tier matrix + the OUTAGE/SLA threshold (shared with the labeler)."""

    model_config = ConfigDict(frozen=True)

    outage_threshold: float = 40.0
    sla_sustain_s: float = 5.0
    tiers: list[Tier] = Field(default_factory=lambda: list(_DEFAULT_TIERS))


def load_ccs_config(path: str = "config/ccs.yaml") -> CCSConfig:
    """Load the CCS weighting config (safe defaults if the file is absent)."""
    if not Path(path).exists():
        return CCSConfig()
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return CCSConfig.model_validate(raw)


def load_ccs_tiers(path: str = "config/ccs_tiers.yaml") -> CCSTiers:
    """Load the CCS-001 tier matrix (safe defaults if the file is absent)."""
    if not Path(path).exists():
        return CCSTiers()
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    return CCSTiers.model_validate(raw)


class CCSEngine:
    """Computes + tiers the Clinical Continuity Score from clinical health + CQS."""

    def __init__(self, config: CCSConfig | None = None, tiers: CCSTiers | None = None) -> None:
        self._config = config or CCSConfig()
        self._tiers = tiers or CCSTiers()

    def _endpoint_value(self, health: EndpointHealth) -> float:
        s = self._config.endpoint_scores
        if not health.reachable:
            return s.unreachable
        if not health.live:
            return s.frozen
        if not health.healthy:
            return s.degraded
        return s.healthy

    def compute(self, healths: list[EndpointHealth], cqs: float) -> float:
        """Return the 0-100 CCS for the given clinical healths + carrier quality."""
        cqs = max(0.0, min(100.0, cqs))
        if healths:
            values = [self._endpoint_value(h) for h in healths]
            mean_v = sum(values) / len(values)
            worst_v = min(values)
            w = self._config.worst_weight
            clinical = (1.0 - w) * mean_v + w * worst_v
        else:
            clinical = cqs  # no clinical signal -> fall back to carrier quality

        denom = self._config.clinical_weight + self._config.carrier_weight
        if denom == 0:
            return round(clinical, 2)
        ccs = (self._config.clinical_weight * clinical + self._config.carrier_weight * cqs) / denom
        return round(max(0.0, min(100.0, ccs)), 2)

    def classify(self, value: float) -> str:
        """Map a CCS value to its CCS-001 tier name (by descending min threshold)."""
        for tier in sorted(self._tiers.tiers, key=lambda t: t.min, reverse=True):
            if value >= tier.min:
                return tier.name
        return self._tiers.tiers[-1].name if self._tiers.tiers else "OUTAGE"

    def score(
        self,
        healths: list[EndpointHealth],
        cqs: float,
        ts: datetime,
        *,
        subject_id: str = "site",
    ) -> ScoreSample:
        """Compute the CCS + tier and wrap it as a publishable ``ScoreSample``."""
        value = self.compute(healths, cqs)
        return ScoreSample(
            timestamp=ts,
            scope="site",
            subject_id=subject_id,
            kind=ScoreKind.CCS,
            value=value,
            tier=self.classify(value),
        )
