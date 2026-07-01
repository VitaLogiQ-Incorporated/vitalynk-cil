"""Scoring loop (EPIC-04 wiring) — persists scores, feeds labeler, raises SLA_STATE."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from structlog.testing import capture_logs

from cil.audit.bus import EventBus
from cil.audit.events import EventKind, EventLabel, ScoreKind
from cil.audit.labeler import EventLabeler, LabelingConfig
from cil.audit.pipeline import LabelingPipeline
from cil.audit.window_capture import WindowCaptureService
from cil.scoring.ccs import CCSEngine
from cil.scoring.cqs import CQSEngine
from cil.scoring.service import ScoringService
from cil.storage.memory import (
    InMemoryApplicationHealthStore,
    InMemoryAuditStore,
    InMemoryEventStore,
    InMemoryLabelStore,
    InMemoryScoreStore,
    InMemoryTelemetryStore,
    InMemoryTrainingStore,
)
from cil.telemetry.probes import EndpointHealth, ProbeDepth
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def tele(reachable: bool = True) -> TelemetrySample:
    return TelemetrySample(
        timestamp=BASE,
        path_id="modem-a",
        carrier="Verizon",
        profile="primary",
        radio=RadioMetrics(sinr=25.0, rsrp=-80.0, rsrq=-8.0),
        network=NetworkMetrics(
            latency_ms=20.0,
            packet_loss_pct=0.0,
            jitter_ms=1.0,
            throughput_mbps=100.0,
            reachable=reachable,
        ),
        device=DeviceMetrics(),
    )


def health(
    name: str, *, reachable: bool = True, live: bool = True, healthy: bool = True
) -> EndpointHealth:
    return EndpointHealth(
        timestamp=BASE,
        endpoint=name,
        system=name,
        reachable=reachable,
        live=live,
        healthy=healthy,
        depth_achieved=ProbeDepth.APP_RESPONSE if healthy else None,
        required_depth=ProbeDepth.APP_RESPONSE,
    )


class Wired(NamedTuple):
    svc: ScoringService
    events: InMemoryEventStore
    labels: InMemoryLabelStore
    scores: InMemoryScoreStore


async def _wire(
    healths: list[EndpointHealth], *, reachable: bool = True, telemetry: bool = True
) -> Wired:
    events, labels, scores = InMemoryEventStore(), InMemoryLabelStore(), InMemoryScoreStore()
    tel, app, training, audit = (
        InMemoryTelemetryStore(),
        InMemoryApplicationHealthStore(),
        InMemoryTrainingStore(),
        InMemoryAuditStore(),
    )
    for s in (events, labels, scores, tel, app, training, audit):
        await s.setup()
    capture = WindowCaptureService(
        tel, app, scores, training, audit, before_s=5, after_s=5, min_radius_s=0, warn_below_s=0
    )
    pipeline = LabelingPipeline(
        event_store=events,
        label_store=labels,
        score_store=scores,
        capture=capture,
        labeler=EventLabeler(LabelingConfig(outage_threshold=40.0, sla_sustain_s=5.0)),
        audit=audit,
        training=training,
    )
    bus = EventBus(events)
    bus.subscribe(pipeline)
    svc = ScoringService(
        cqs=CQSEngine(),
        ccs=CCSEngine(),
        score_store=scores,
        bus=bus,
        telemetry_provider=lambda: tele(reachable=reachable) if telemetry else None,
        health_provider=lambda: healths,
        outage_threshold=40.0,
        sla_sustain_s=5.0,
    )
    return Wired(svc, events, labels, scores)


async def test_tick_persists_cqs_and_ccs_scores() -> None:
    w = await _wire([health("epic"), health("pacs")])
    out = await w.svc.tick(BASE)
    assert out["tier"] == "Protected"
    kinds = {s.kind for s in await w.scores.read_scores(limit=10)}
    assert kinds == {ScoreKind.CQS, ScoreKind.CCS}  # both persisted -> /scores


async def test_score_sample_event_is_emitted_persist_only() -> None:
    w = await _wire([health("epic")])
    await w.svc.tick(BASE)
    evs = await w.events.read_events(limit=10)
    assert any(e.kind is EventKind.SCORE_SAMPLE and e.ccs is not None for e in evs)
    assert await w.labels.count() == 0  # SCORE_SAMPLE is persist-only (no label/window)


async def test_sustained_outage_emits_sla_state_labelled_breach() -> None:
    # all clinical systems down -> CCS 0 -> OUTAGE; sustained 5s -> SLA_BREACH
    down = [health("epic", reachable=False, live=False, healthy=False)]
    w = await _wire(down, reachable=False)
    # tick once per second; breach should be raised once the 5s dwell is met
    for i in range(7):
        await w.svc.tick(BASE + timedelta(seconds=i))
    sla = [e for e in await w.events.read_events(limit=50) if e.kind is EventKind.SLA_STATE]
    assert len(sla) == 1  # exactly one SLA_STATE per breach episode (edge-triggered)
    assert sla[0].sla_breaching is True
    breach_label = await w.labels.get_label(sla[0].event_id)
    assert breach_label is not None and breach_label.label is EventLabel.SLA_BREACH


async def test_no_sla_state_when_healthy() -> None:
    w = await _wire([health("epic")])
    for i in range(7):
        await w.svc.tick(BASE + timedelta(seconds=i))
    assert not any(e.kind is EventKind.SLA_STATE for e in await w.events.read_events(limit=50))


async def test_transient_dip_under_5s_does_not_breach() -> None:
    # down for 3s then recovers -> below dwell -> no SLA_STATE
    healths_down = [health("epic", reachable=False, live=False, healthy=False)]
    w = await _wire(healths_down, reachable=False)
    for i in range(3):  # only 3s of outage
        await w.svc.tick(BASE + timedelta(seconds=i))
    assert not any(e.kind is EventKind.SLA_STATE for e in await w.events.read_events(limit=50))


async def test_missing_telemetry_scores_clinical_only_not_healthy() -> None:
    # telemetry gone but a clinical system is frozen -> CCS reflects clinical, not a
    # phantom-healthy carrier (regression guard for the sample-None inflation bug)
    w = await _wire([health("epic", live=False, healthy=False)], telemetry=False)
    out = await w.svc.tick(BASE)
    assert out["cqs"] is None
    assert out["ccs"] == 30.0  # clinical-only (frozen=30), not lifted toward healthy
    assert out["tier"] == "OUTAGE"


async def test_no_signal_skips_tick_and_emits_nothing() -> None:
    # no telemetry AND no clinical health -> skip; never emit a false "healthy" score
    w = await _wire([], telemetry=False)
    out = await w.svc.tick(BASE)
    assert out.get("skipped") is True
    assert await w.scores.read_scores(limit=10) == []
    assert await w.events.count() == 0


async def test_no_signal_warns_once_per_episode() -> None:
    # repeated no-signal ticks must warn once, not spam a warning every tick
    w = await _wire([], telemetry=False)
    with capture_logs() as logs:
        for i in range(4):
            await w.svc.tick(BASE + timedelta(seconds=i))
    assert len([e for e in logs if e["event"] == "scoring.no_signal"]) == 1


async def test_sla_rebreach_after_recovery_emits_second_breach() -> None:
    # down -> (breach) -> recover (resets edge) -> down again -> second breach.
    # `healths` is mutated in place; the provider reads it live.
    healths = [health("epic", reachable=False, live=False, healthy=False)]
    w = await _wire(healths)  # carrier healthy; CCS driven by clinical
    for i in range(6):  # sustained outage -> breach #1
        await w.svc.tick(BASE + timedelta(seconds=i))
    healths[:] = [health("epic")]  # recover
    for i in range(6, 9):
        await w.svc.tick(BASE + timedelta(seconds=i))
    healths[:] = [health("epic", reachable=False, live=False, healthy=False)]  # down again
    for i in range(9, 16):  # sustained outage -> breach #2
        await w.svc.tick(BASE + timedelta(seconds=i))
    sla = [e for e in await w.events.read_events(limit=100) if e.kind is EventKind.SLA_STATE]
    assert len(sla) == 2  # the edge detector re-arms after recovery
