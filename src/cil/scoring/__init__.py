"""Scoring engines: CQS and CCS.

- CQS — Carrier Quality Score (0-100): raw network/carrier quality.
- CCS — Clinical Continuity Score (0-100): THE authoritative decision metric
  ("can the hospital still reach its clinical systems?").

Pure, fully unit-testable functions. All thresholds/weights are config-driven
(CCS-001) — nothing is hardcoded (CIL-401/402).
"""

from cil.scoring.ccs import (
    CCSConfig,
    CCSEngine,
    CCSTiers,
    EndpointScores,
    Tier,
    load_ccs_config,
    load_ccs_tiers,
)
from cil.scoring.cqs import CQSConfig, CQSEngine, MetricSpec, load_cqs_config

__all__ = [
    "CCSConfig",
    "CCSEngine",
    "CCSTiers",
    "CQSConfig",
    "CQSEngine",
    "EndpointScores",
    "MetricSpec",
    "Tier",
    "load_ccs_config",
    "load_ccs_tiers",
    "load_cqs_config",
]
