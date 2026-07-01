"""CIP policy library loading (CIL-502): config-driven, strict, with a code fallback."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cil.audit.events import DecisionAction
from cil.policy.loader import DEFAULT_LIBRARY, load_policy_library
from cil.policy.models import Condition, ConditionOp, Policy


def test_shipped_config_matches_builtin_fallback() -> None:
    lib = load_policy_library("config/cip_policies.yaml")
    assert lib.version == "2.0"
    assert lib.default_action is DecisionAction.STAY
    # same ids and same tier->action mapping as the Python fallback (no drift)
    assert {p.id: p.action for p in lib.policies} == {
        p.id: p.action for p in DEFAULT_LIBRARY.policies
    }


def test_fallback_when_file_absent(tmp_path: Path) -> None:
    lib = load_policy_library(str(tmp_path / "nope.yaml"))
    assert len(lib.policies) == len(DEFAULT_LIBRARY.policies)


def test_policy_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        Policy(id="X", action=DecisionAction.STAY, prioriti=5)  # type: ignore[call-arg]


def test_condition_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        Condition(field="ccs", op=ConditionOp.LT, valu=40)  # type: ignore[call-arg]


def test_all_any_aliases_load_from_yaml(tmp_path: Path) -> None:
    p = tmp_path / "cip.yaml"
    p.write_text(
        "policies:\n"
        "  - id: T\n"
        "    priority: 5\n"
        "    any:\n"
        "      - { field: sla_breaching, op: eq, value: true }\n"
        "    action: failover\n"
    )
    lib = load_policy_library(str(p))
    assert lib.policies[0].any_conditions[0].field == "sla_breaching"
    assert lib.policies[0].action is DecisionAction.FAILOVER
