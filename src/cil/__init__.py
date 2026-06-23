"""VitaLogiQ Carrier Intelligence Layer (CIL) — UC1.

A deterministic, on-device clinical network-continuity "brain". It watches the
network, scores health (CQS/CCS), evaluates policy, and *decides* an action —
Ericsson hardware executes it. No ML and no LLM sit on the UC1 decision path.

Modular monolith: one deployable, clean internal module boundaries
(see CLAUDE.md §4). Subpackages:

    telemetry/  adapters (Ericsson + simulator) + normalization into the schema
    storage/    storage interface + SQLite implementation
    scoring/    CQS (carrier quality) and CCS (clinical continuity) engines
    policy/     declarative YAML policy framework + CIP policy library
    decision/   decision FSM + orchestration (emits actions, does NOT execute)
    recovery/   recovery validation (application-level liveness)
    audit/      audit/event logging + automated event labeling
    api/        FastAPI app, health/metrics, config wiring
"""

__version__ = "0.1.0"
