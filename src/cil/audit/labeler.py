"""Rule-based automated event labeler (CIL-303) — deterministic, NOT ML.

Tags each continuity event with exactly one of the seven ``EventLabel`` values
using an ordered, first-match-wins rule set. All thresholds are config-driven
(``ccs_tiers.yaml`` = CCS-001, ``labeling.yaml``) — nothing hardcoded.

The SLA rule is **stateful**: it tracks, per subject, when CCS first dropped
below the outage threshold (on the native score timeline), so an OUTAGE is only
labelled once it has been sustained for the configured dwell. State can be rebuilt
by replaying the score timeline (the pipeline does this on startup), so a restart
mid-breach doesn't reset the timer.

There are deliberately no ML/LLM imports here — this is the UC1 deterministic path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from cil.audit.events import ContinuityEvent, DecisionAction, EventKind, EventLabel
from cil.telemetry.probes import ProbeDepth


@dataclass(frozen=True)
class LabelResult:
    label: EventLabel
    rule_id: str
    reason: str | None = None


@dataclass(frozen=True)
class LabelingConfig:
    outage_threshold: float = 40.0
    sla_sustain_s: float = 5.0
    rollback_window_seconds: float = 120.0
    render_state_conservative: bool = True


def load_labeling_config(
    ccs_tiers_path: str = "config/ccs_tiers.yaml",
    labeling_path: str = "config/labeling.yaml",
) -> LabelingConfig:
    """Load the labeling config from CCS-001 + labeling.yaml (with safe defaults)."""
    defaults = LabelingConfig()
    ccs: dict[str, Any] = {}
    lab: dict[str, Any] = {}
    if Path(ccs_tiers_path).exists():
        ccs = yaml.safe_load(Path(ccs_tiers_path).read_text()) or {}
    if Path(labeling_path).exists():
        lab = yaml.safe_load(Path(labeling_path).read_text()) or {}
    return LabelingConfig(
        outage_threshold=float(ccs.get("outage_threshold", defaults.outage_threshold)),
        sla_sustain_s=float(ccs.get("sla_sustain_s", defaults.sla_sustain_s)),
        rollback_window_seconds=float(
            lab.get("rollback_window_seconds", defaults.rollback_window_seconds)
        ),
        render_state_conservative=bool(
            lab.get("render_state_conservative", defaults.render_state_conservative)
        ),
    )


def _subject(event: ContinuityEvent) -> str:
    return event.path_id or event.endpoint or "global"


class EventLabeler:
    """Stateful, config-driven, rule-based labeler."""

    def __init__(self, config: LabelingConfig | None = None) -> None:
        self._cfg = config or LabelingConfig()
        # subject -> first timestamp CCS went below the outage threshold.
        self._first_below: dict[str, datetime] = {}

    @property
    def config(self) -> LabelingConfig:
        return self._cfg

    @property
    def labels(self) -> set[EventLabel]:
        """The exact set this labeler can emit (guardrail: must be the 7)."""
        return set(EventLabel)

    def observe_score(self, subject_id: str, ccs: float, timestamp: datetime) -> None:
        """Feed the native CCS timeline so SLA dwell can be tracked + replayed."""
        if ccs < self._cfg.outage_threshold:
            self._first_below.setdefault(subject_id, timestamp)
        else:
            self._first_below.pop(subject_id, None)

    def label(self, event: ContinuityEvent) -> LabelResult:
        """Return the label for an anchoring event (first matching rule wins)."""
        cfg = self._cfg
        subject = _subject(event)
        if event.ccs is not None:
            self.observe_score(subject, event.ccs, event.timestamp)

        # 1. SLA_BREACH — highest priority (the clinically critical label).
        if self._is_sla_breach(event, subject):
            return LabelResult(
                EventLabel.SLA_BREACH,
                "sla_breach",
                f"CCS<{cfg.outage_threshold} sustained >= {cfg.sla_sustain_s}s",
            )
        # 2. ESCALATION
        if event.kind == EventKind.DECISION and event.action == DecisionAction.ESCALATE:
            return LabelResult(EventLabel.ESCALATION, "escalation", "decision=escalate")
        # 3. ROLLBACK — checked before FAILOVER (a revert is not a fresh failover).
        if event.kind == EventKind.DECISION and self._is_rollback(event):
            return LabelResult(
                EventLabel.ROLLBACK,
                "rollback",
                f"{event.action} reverts prev {event.prev_action}",
            )
        # 4. FAILOVER
        if event.kind == EventKind.DECISION and event.action == DecisionAction.FAILOVER:
            return LabelResult(EventLabel.FAILOVER, "failover", "decision=failover")
        # 5. OPTIMIZATION
        if event.kind == EventKind.DECISION and event.action in (
            DecisionAction.OPTIMIZE,
            DecisionAction.SHIFT,
        ):
            return LabelResult(EventLabel.OPTIMIZATION, "optimization", f"decision={event.action}")
        # 6. RECOVERY (render-state-conservative)
        recovery = self._recovery_result(event)
        if recovery is not None:
            return recovery
        # 7. NO_ACTION — explicit negative class.
        return LabelResult(EventLabel.NO_ACTION, "no_action", None)

    def _is_sla_breach(self, event: ContinuityEvent, subject: str) -> bool:
        if event.kind == EventKind.SLA_STATE and event.sla_breaching:
            return True
        if event.ccs is None or event.ccs >= self._cfg.outage_threshold:
            return False
        first = self._first_below.get(subject)
        if first is None:
            return False
        sustained = (event.timestamp - first).total_seconds()
        return sustained >= self._cfg.sla_sustain_s

    def _is_rollback(self, event: ContinuityEvent) -> bool:
        if event.prev_action != DecisionAction.FAILOVER:
            return False
        if event.action not in (DecisionAction.STAY, DecisionAction.SHIFT):
            return False
        since = event.attributes.get("seconds_since_prev")
        if isinstance(since, int | float):
            return float(since) <= self._cfg.rollback_window_seconds
        return True  # timing unknown -> still treat a failover revert as rollback

    def _recovery_result(self, event: ContinuityEvent) -> LabelResult | None:
        is_recovery = event.kind in (
            EventKind.RECOVERY_VALIDATED,
            EventKind.ENDPOINT_RECOVERED,
        )
        if not is_recovery:
            return None
        if (
            self._cfg.render_state_conservative
            and event.attributes.get("required_depth") == ProbeDepth.RENDER_STATE.value
        ):
            return LabelResult(
                EventLabel.NO_ACTION,
                "recovery_withheld",
                "render-state recovery unverifiable; pending clinical input",
            )
        return LabelResult(EventLabel.RECOVERY, "recovery", event.kind.value)
