"""The declarative policy engine (CIL-501).

Evaluates a ``PolicyLibrary`` against a ``PolicyContext`` and returns the recommended
``DecisionAction`` with a full, explainable trail of which policies matched. Pure and
deterministic; the only "logic" is generic condition evaluation — the *policy* lives
entirely in data (the CIP library), never in Python.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cil.audit.events import DecisionAction
from cil.logging import get_logger
from cil.policy.models import (
    Condition,
    ConditionOp,
    Policy,
    PolicyEvaluation,
    PolicyLibrary,
    PolicyMatch,
)

if TYPE_CHECKING:
    from cil.policy.context import PolicyContext


def _compare(op: ConditionOp, actual: Any, expected: Any) -> bool:
    """Apply one operator.

    A condition on an absent/unknown field (``actual is None``) never holds — for
    *every* operator (you can't assert equality, inequality, or ordering about an
    unknown), so an optional context field can't accidentally satisfy ``ne``/``not_in``.
    ``in``/``not_in`` are membership over a list/tuple/set; a scalar operand means
    single-value equality (never substring matching). Type-mismatched ordering is
    False, never raising, so a malformed policy can't crash evaluation.
    """
    if actual is None:
        return False
    if op is ConditionOp.EQ:
        return bool(actual == expected)
    if op is ConditionOp.NE:
        return bool(actual != expected)
    if op is ConditionOp.IN:
        return (
            actual in expected if isinstance(expected, (list, tuple, set)) else actual == expected
        )
    if op is ConditionOp.NOT_IN:
        return (
            actual not in expected
            if isinstance(expected, (list, tuple, set))
            else actual != expected
        )
    try:
        if op is ConditionOp.LT:
            return bool(actual < expected)
        if op is ConditionOp.LE:
            return bool(actual <= expected)
        if op is ConditionOp.GT:
            return bool(actual > expected)
        if op is ConditionOp.GE:
            return bool(actual >= expected)
    except TypeError:
        return False
    return False


class PolicyEngine:
    """Evaluates the CIP policy library against a context (highest-priority match wins)."""

    def __init__(self, library: PolicyLibrary) -> None:
        self._library = library
        self._log = get_logger("cil.policy.engine")

    @property
    def policies(self) -> list[Policy]:
        return list(self._library.policies)

    @property
    def default_action(self) -> DecisionAction:
        return self._library.default_action

    def _condition_holds(self, condition: Condition, ctx: dict[str, Any]) -> bool:
        return _compare(condition.op, ctx.get(condition.field), condition.value)

    def _matches(self, policy: Policy, ctx: dict[str, Any]) -> bool:
        if not policy.all_conditions and not policy.any_conditions:
            return False  # no conditions -> never an unconditional catch-all
        if policy.all_conditions and not all(
            self._condition_holds(c, ctx) for c in policy.all_conditions
        ):
            return False
        return not policy.any_conditions or any(
            self._condition_holds(c, ctx) for c in policy.any_conditions
        )

    def evaluate(self, context: PolicyContext) -> PolicyEvaluation:
        """Return the recommended action + every matching policy (explainable)."""
        ctx = context.as_dict()
        matches = [
            PolicyMatch(
                policy_id=p.id,
                action=p.action,
                priority=p.priority,
                reason=p.reason or f"{p.id} matched",
            )
            for p in self._library.policies
            if p.enabled and self._matches(p, ctx)
        ]
        # highest priority wins; stable sort keeps declaration order on ties.
        matches.sort(key=lambda m: m.priority, reverse=True)
        evaluated = len(self._library.policies)
        if matches:
            top = matches[0]
            return PolicyEvaluation(
                recommended_action=top.action,
                winning_policy=top.policy_id,
                reason=top.reason,
                matches=matches,
                evaluated=evaluated,
            )
        return PolicyEvaluation(
            recommended_action=self._library.default_action,
            winning_policy=None,
            reason="no policy matched — default action",
            matches=[],
            evaluated=evaluated,
        )
