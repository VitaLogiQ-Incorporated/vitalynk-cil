"""Load the CIP policy library (CIL-502) from YAML — with a built-in fallback.

The YAML at ``config/cip_policies.yaml`` is the source of truth; the Python default
mirrors it so the engine still works if the file is absent (simulator-first build).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from cil.audit.events import DecisionAction
from cil.logging import get_logger
from cil.policy.models import Condition, ConditionOp, Policy, PolicyLibrary

# Built-in CIP v2.0 fallback (kept in sync with config/cip_policies.yaml). Tiers map to
# the decide-not-execute action set; higher priority wins. CCS tiers come from CCS-001.
DEFAULT_LIBRARY = PolicyLibrary(
    version="2.0",
    default_action=DecisionAction.STAY,
    policies=[
        Policy(
            id="CIP-ESCALATE",
            description="SLA breach persists after a failover was already requested — escalate.",
            priority=100,
            all=[
                Condition(field="sla_breaching", op=ConditionOp.EQ, value=True),
                Condition(field="current_action", op=ConditionOp.EQ, value="failover"),
            ],
            action=DecisionAction.ESCALATE,
            reason="SLA breach persists after failover",
        ),
        Policy(
            id="CIP-FAILOVER",
            description="Sustained SLA breach (CCS < 40 for >= 5s, CCS-001) — request failover. "
            "Keys on the dwell-gated breach, not a single sub-40 sample, so a transient "
            "dip does not trigger a disruptive failover.",
            priority=90,
            all=[Condition(field="sla_breaching", op=ConditionOp.EQ, value=True)],
            action=DecisionAction.FAILOVER,
            reason="sustained SLA breach",
        ),
        Policy(
            id="CIP-SHIFT",
            description="At risk (Breach Risk tier) — shift to a better carrier before failover.",
            priority=70,
            all=[Condition(field="ccs_tier", op=ConditionOp.EQ, value="Breach Risk")],
            action=DecisionAction.SHIFT,
            reason="CCS at Breach Risk",
        ),
        Policy(
            id="CIP-OPTIMIZE",
            description="Degraded — optimize the current path.",
            priority=50,
            all=[Condition(field="ccs_tier", op=ConditionOp.EQ, value="Degraded")],
            action=DecisionAction.OPTIMIZE,
            reason="CCS Degraded",
        ),
        Policy(
            id="CIP-STAY",
            description="Healthy (Protected/Stable) — stay on the current path.",
            priority=10,
            all=[Condition(field="ccs_tier", op=ConditionOp.IN, value=["Protected", "Stable"])],
            action=DecisionAction.STAY,
            reason="CCS healthy",
        ),
    ],
)


def load_policy_library(path: str = "config/cip_policies.yaml") -> PolicyLibrary:
    """Load the CIP library from YAML, falling back to the built-in default if absent."""
    log = get_logger("cil.policy.loader")
    if not Path(path).exists():
        log.warning("policy.fallback_default", path=path, policies=len(DEFAULT_LIBRARY.policies))
        return DEFAULT_LIBRARY
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    library = PolicyLibrary.model_validate(raw)
    log.info("policy.loaded", path=path, version=library.version, policies=len(library.policies))
    return library
