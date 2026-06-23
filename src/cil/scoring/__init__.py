"""Scoring engines: CQS and CCS.

- CQS — Carrier Quality Score (0-100): raw network/carrier quality.
- CCS — Clinical Continuity Score (0-100): THE authoritative decision metric
  ("can the hospital still reach its clinical systems?").

Pure, fully unit-testable functions. All thresholds/weights are config-driven
(CCS-001) — nothing is hardcoded (CIL-401/402).
"""
