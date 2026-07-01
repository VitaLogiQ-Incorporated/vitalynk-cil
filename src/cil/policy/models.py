"""Declarative policy data model (CIL-501).

A policy is pure data: a set of conditions over a ``PolicyContext`` and the action
it *recommends* if they hold. There is **no policy logic in Python** — the framework
only evaluates these declarations (loaded from YAML, the CIP library, CIL-502).

Decide-not-execute: a policy recommends a ``DecisionAction``; it never acts. The
decision FSM (EPIC-06) consumes the recommendation; Ericsson executes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cil.audit.events import DecisionAction


class ConditionOp(StrEnum):
    """The comparison operators a condition may use (no code/eval — safe by construction)."""

    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    NOT_IN = "not_in"


class Condition(BaseModel):
    """One predicate: ``<field> <op> <value>`` evaluated against the policy context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    op: ConditionOp
    value: Any = None


class Policy(BaseModel):
    """A declarative policy: when all/any of its conditions hold, recommend ``action``."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    id: str
    description: str = ""
    priority: int = 0  # higher wins when several policies match
    enabled: bool = True
    # ``all`` must all hold; ``any`` (if present) requires at least one. A policy with
    # neither never matches — guards against an accidental unconditional catch-all.
    all_conditions: list[Condition] = Field(default_factory=list, alias="all")
    any_conditions: list[Condition] = Field(default_factory=list, alias="any")
    action: DecisionAction
    reason: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class PolicyLibrary(BaseModel):
    """The loaded CIP policy library (CIL-502) — a versioned set of policies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "2.0"
    default_action: DecisionAction = DecisionAction.STAY
    policies: list[Policy] = Field(default_factory=list)


class PolicyMatch(BaseModel):
    """A single policy that matched the context (for the explainable audit trail)."""

    model_config = ConfigDict(frozen=True)

    policy_id: str
    action: DecisionAction
    priority: int
    reason: str


class PolicyEvaluation(BaseModel):
    """The result of evaluating the whole library against one context."""

    model_config = ConfigDict(frozen=True)

    recommended_action: DecisionAction
    winning_policy: str | None
    reason: str
    matches: list[PolicyMatch] = Field(default_factory=list)
    evaluated: int = 0
