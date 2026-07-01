"""Policy engine (CIL-501): declarative eval, precedence, explainability."""

from __future__ import annotations

from cil.audit.events import DecisionAction
from cil.policy.context import PolicyContext
from cil.policy.engine import PolicyEngine
from cil.policy.loader import DEFAULT_LIBRARY
from cil.policy.models import Condition, ConditionOp, Policy, PolicyLibrary

_TIER_CCS = {"Protected": 95, "Stable": 80, "Degraded": 65, "Breach Risk": 50, "OUTAGE": 20}


def ctx(tier: str, *, ccs: float | None = None, **kw: object) -> PolicyContext:
    value = _TIER_CCS.get(tier, 50) if ccs is None else ccs
    return PolicyContext(ccs=value, ccs_tier=tier, **kw)  # type: ignore[arg-type]


def _engine() -> PolicyEngine:
    return PolicyEngine(DEFAULT_LIBRARY)


def test_each_ccs_tier_maps_to_its_action() -> None:
    e = _engine()
    assert e.evaluate(ctx("Protected")).recommended_action is DecisionAction.STAY
    assert e.evaluate(ctx("Stable")).recommended_action is DecisionAction.STAY
    assert e.evaluate(ctx("Degraded")).recommended_action is DecisionAction.OPTIMIZE
    assert e.evaluate(ctx("Breach Risk")).recommended_action is DecisionAction.SHIFT


def test_sustained_breach_recommends_failover() -> None:
    r = _engine().evaluate(ctx("OUTAGE", sla_breaching=True))
    assert r.recommended_action is DecisionAction.FAILOVER
    assert r.winning_policy == "CIP-FAILOVER"


def test_transient_outage_without_dwell_does_not_failover() -> None:
    # instantaneous OUTAGE tier but NOT sustained -> no disruptive failover (wait-and-see)
    r = _engine().evaluate(ctx("OUTAGE"))  # sla_breaching defaults False
    assert r.recommended_action is DecisionAction.STAY
    assert r.winning_policy is None


def test_escalate_outranks_failover_when_already_failed_over() -> None:
    e = _engine()
    r = e.evaluate(ctx("OUTAGE", sla_breaching=True, current_action=DecisionAction.FAILOVER))
    assert r.recommended_action is DecisionAction.ESCALATE
    assert r.winning_policy == "CIP-ESCALATE"
    # both matched; the higher-priority one wins and the trail records both
    assert {m.policy_id for m in r.matches} >= {"CIP-ESCALATE", "CIP-FAILOVER"}


def test_sustained_breach_outranks_tier_action() -> None:
    # a sustained breach at Breach Risk tier -> failover (priority 90) beats shift (70)
    r = _engine().evaluate(ctx("Breach Risk", sla_breaching=True))
    assert r.recommended_action is DecisionAction.FAILOVER


def test_no_match_returns_default_action() -> None:
    r = _engine().evaluate(ctx("Unknown"))
    assert r.recommended_action is DecisionAction.STAY
    assert r.winning_policy is None
    assert r.matches == []


def test_disabled_policy_is_skipped() -> None:
    lib = PolicyLibrary(
        policies=[
            Policy(
                id="X",
                priority=99,
                enabled=False,
                all=[Condition(field="ccs_tier", op=ConditionOp.EQ, value="Degraded")],
                action=DecisionAction.FAILOVER,
            )
        ]
    )
    r = PolicyEngine(lib).evaluate(ctx("Degraded"))
    assert r.winning_policy is None and r.recommended_action is DecisionAction.STAY


def test_policy_with_no_conditions_never_matches() -> None:
    lib = PolicyLibrary(
        policies=[Policy(id="CATCHALL", priority=5, action=DecisionAction.FAILOVER)]
    )
    assert PolicyEngine(lib).evaluate(ctx("Protected")).winning_policy is None


def test_numeric_and_in_operators() -> None:
    lib = PolicyLibrary(
        policies=[
            Policy(
                id="LOW",
                priority=1,
                all=[Condition(field="ccs", op=ConditionOp.LT, value=40)],
                action=DecisionAction.FAILOVER,
            )
        ]
    )
    e = PolicyEngine(lib)
    assert e.evaluate(ctx("OUTAGE", ccs=20)).recommended_action is DecisionAction.FAILOVER
    assert e.evaluate(ctx("Protected", ccs=95)).recommended_action is DecisionAction.STAY  # default


def test_in_operator_with_scalar_is_equality_not_substring() -> None:
    lib = PolicyLibrary(
        policies=[
            Policy(
                id="X",
                priority=1,
                all=[Condition(field="ccs_tier", op=ConditionOp.IN, value="OUTAGE")],
                action=DecisionAction.FAILOVER,
            )
        ]
    )
    e = PolicyEngine(lib)
    assert e.evaluate(ctx("OUTAGE")).recommended_action is DecisionAction.FAILOVER  # equality
    assert e.evaluate(ctx("OUT")).recommended_action is DecisionAction.STAY  # not a substring


def test_ne_on_missing_field_does_not_match() -> None:
    lib = PolicyLibrary(
        policies=[
            Policy(
                id="X",
                priority=1,
                all=[Condition(field="cqs", op=ConditionOp.NE, value=100)],
                action=DecisionAction.FAILOVER,
            )
        ]
    )
    e = PolicyEngine(lib)
    # cqs unknown (None) -> `ne` must NOT match (a condition on an unknown field never holds)
    assert e.evaluate(PolicyContext(ccs=95, ccs_tier="Protected")).recommended_action is (
        DecisionAction.STAY
    )
    # cqs present and != 100 -> matches
    assert (
        e.evaluate(PolicyContext(ccs=95, ccs_tier="Protected", cqs=50)).recommended_action
        is DecisionAction.FAILOVER
    )


def test_missing_field_does_not_crash_and_does_not_match() -> None:
    lib = PolicyLibrary(
        policies=[
            Policy(
                id="NEEDS_MISSING",
                priority=1,
                all=[Condition(field="does_not_exist", op=ConditionOp.GT, value=1)],
                action=DecisionAction.FAILOVER,
            )
        ]
    )
    r = PolicyEngine(lib).evaluate(ctx("Protected"))
    assert r.winning_policy is None  # ordering op on a missing field -> no match, no crash


def test_evaluation_is_explainable() -> None:
    r = _engine().evaluate(ctx("Degraded"))
    assert r.reason == "CCS Degraded"
    assert r.evaluated == len(DEFAULT_LIBRARY.policies)
