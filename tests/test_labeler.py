"""Tests for the rule-based event labeler (CIL-303)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cil.audit.events import (
    ContinuityEvent,
    DecisionAction,
    EventKind,
    EventLabel,
    EventSource,
    new_event_id,
)
from cil.audit.labeler import EventLabeler, LabelingConfig, load_labeling_config
from cil.telemetry.probes import ProbeDepth

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def ev(second: int, kind: EventKind, **fields: object) -> ContinuityEvent:
    ts = BASE + timedelta(seconds=second)
    return ContinuityEvent(
        event_id=new_event_id(ts, kind, str(second)),
        timestamp=ts,
        kind=kind,
        source=EventSource.SYNTHETIC,
        path_id="modem-a",
        **fields,  # type: ignore[arg-type]
    )


def test_no_action_fallthrough() -> None:
    assert EventLabeler().label(ev(0, EventKind.NO_ACTION_SAMPLE)).label is EventLabel.NO_ACTION


def test_optimization_for_optimize_and_shift() -> None:
    lab = EventLabeler()
    assert lab.label(ev(0, EventKind.DECISION, action=DecisionAction.OPTIMIZE)).label is (
        EventLabel.OPTIMIZATION
    )
    assert lab.label(ev(1, EventKind.DECISION, action=DecisionAction.SHIFT)).label is (
        EventLabel.OPTIMIZATION
    )


def test_failover() -> None:
    r = EventLabeler().label(ev(0, EventKind.DECISION, action=DecisionAction.FAILOVER))
    assert r.label is EventLabel.FAILOVER


def test_rollback_takes_precedence_over_failover() -> None:
    # A revert of a prior FAILOVER is ROLLBACK, not FAILOVER.
    r = EventLabeler().label(
        ev(
            0,
            EventKind.DECISION,
            action=DecisionAction.STAY,
            prev_action=DecisionAction.FAILOVER,
            attributes={"seconds_since_prev": 5},
        )
    )
    assert r.label is EventLabel.ROLLBACK


def test_rollback_outside_window_is_not_rollback() -> None:
    r = EventLabeler(LabelingConfig(rollback_window_seconds=10)).label(
        ev(
            0,
            EventKind.DECISION,
            action=DecisionAction.SHIFT,
            prev_action=DecisionAction.FAILOVER,
            attributes={"seconds_since_prev": 999},
        )
    )
    assert r.label is EventLabel.OPTIMIZATION  # a fresh SHIFT, not a revert


def test_escalation() -> None:
    r = EventLabeler().label(ev(0, EventKind.DECISION, action=DecisionAction.ESCALATE))
    assert r.label is EventLabel.ESCALATION


def test_recovery_authoritative_for_app_response_endpoint() -> None:
    r = EventLabeler().label(ev(0, EventKind.ENDPOINT_RECOVERED, endpoint="epic-ehr"))
    assert r.label is EventLabel.RECOVERY


def test_recovery_withheld_for_render_state_endpoint() -> None:
    r = EventLabeler().label(
        ev(
            0,
            EventKind.ENDPOINT_RECOVERED,
            endpoint="or-systems",
            attributes={"required_depth": ProbeDepth.RENDER_STATE.value},
        )
    )
    assert r.label is EventLabel.NO_ACTION
    assert r.rule_id == "recovery_withheld"


def test_sla_breach_via_sla_state_event() -> None:
    r = EventLabeler().label(ev(0, EventKind.SLA_STATE, sla_breaching=True))
    assert r.label is EventLabel.SLA_BREACH


def test_sla_breach_boundary_is_config_driven() -> None:
    # Custom config (50 / 3s) proves no 40/5 is hardcoded.
    cfg = LabelingConfig(outage_threshold=50.0, sla_sustain_s=3.0)
    lab = EventLabeler(cfg)
    for s in range(3):  # below threshold at t0,t1,t2
        lab.observe_score("modem-a", 45.0, BASE + timedelta(seconds=s))
    # at t2 -> sustained 2s < 3s -> not yet a breach
    assert lab.label(ev(2, EventKind.SCORE_SAMPLE, ccs=45.0)).label is EventLabel.NO_ACTION
    # at t3 -> sustained 3s -> breach
    assert lab.label(ev(3, EventKind.SCORE_SAMPLE, ccs=45.0)).label is EventLabel.SLA_BREACH


def test_sla_dormant_without_ccs() -> None:
    # Safe-degrade: no CCS yet (scoring not live) -> never SLA_BREACH.
    assert EventLabeler().label(ev(0, EventKind.NO_ACTION_SAMPLE)).label is EventLabel.NO_ACTION


def test_sla_recovers_when_ccs_returns_above_threshold() -> None:
    cfg = LabelingConfig(outage_threshold=50.0, sla_sustain_s=3.0)
    lab = EventLabeler(cfg)
    for s in range(4):
        lab.observe_score("modem-a", 45.0, BASE + timedelta(seconds=s))
    # CCS climbs back above threshold -> state clears -> no breach
    assert lab.label(ev(5, EventKind.SCORE_SAMPLE, ccs=60.0)).label is EventLabel.NO_ACTION


def test_sla_state_survives_restart_via_replay() -> None:
    cfg = LabelingConfig(outage_threshold=50.0, sla_sustain_s=3.0)
    # Breach onset observed before a "restart"...
    pre = EventLabeler(cfg)
    for s in range(3):
        pre.observe_score("modem-a", 45.0, BASE + timedelta(seconds=s))
    # ...new labeler (post-restart) replays the same score timeline...
    post = EventLabeler(cfg)
    for s in range(3):
        post.observe_score("modem-a", 45.0, BASE + timedelta(seconds=s))
    # ...and the breach timer is intact (not reset by the restart).
    assert post.label(ev(3, EventKind.SCORE_SAMPLE, ccs=45.0)).label is EventLabel.SLA_BREACH


def test_emits_exactly_the_seven_labels() -> None:
    assert EventLabeler().labels == set(EventLabel)
    assert len(set(EventLabel)) == 7


def test_no_ml_imports_guardrail() -> None:
    import cil.audit.labeler as labeler_module

    src = Path(labeler_module.__file__).read_text()
    for forbidden in ("numpy", "sklearn", "xgboost", "torch", "tensorflow", "pandas"):
        assert f"import {forbidden}" not in src


def test_config_loads_ccs001_values() -> None:
    cfg = load_labeling_config()  # real config/ccs_tiers.yaml + labeling.yaml
    assert cfg.outage_threshold == 40.0
    assert cfg.sla_sustain_s == 5.0
