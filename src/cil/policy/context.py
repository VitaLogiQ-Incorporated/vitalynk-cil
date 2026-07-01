"""The policy evaluation context (CIL-501).

The normalized snapshot of "current state" that CIP policies are evaluated against.
It is derived from the scoring layer (EPIC-04: CCS/CQS + tier + SLA state) plus the
current decision state — it does not compute anything itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from cil.audit.events import DecisionAction


class PolicyContext(BaseModel):
    """The fields a policy condition may reference. Frozen (a snapshot)."""

    model_config = ConfigDict(frozen=True)

    ccs: float
    ccs_tier: str
    cqs: float | None = None
    sla_breaching: bool = False
    sustained_s: float = 0.0
    clinical_all_healthy: bool = True
    # what the brain is currently doing / did last (for hysteresis + escalation policies)
    current_action: DecisionAction | None = None
    prev_action: DecisionAction | None = None
    path_id: str | None = None
    carrier: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Flat, JSON-native field map for the condition engine (enums -> str values)."""
        return self.model_dump(mode="json")
