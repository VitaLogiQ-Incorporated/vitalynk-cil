"""PolicyEvaluator (EPIC-05): dwell-gated SLA breach from live CCS history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cil.audit.events import DecisionAction, ScoreKind, ScoreSample
from cil.policy.engine import PolicyEngine
from cil.policy.loader import DEFAULT_LIBRARY
from cil.policy.service import PolicyEvaluator
from cil.storage.memory import InMemoryScoreStore

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def _store_with_ccs(values: list[tuple[float, str]]) -> InMemoryScoreStore:
    """values: list of (ccs_value, tier) at 1s cadence starting at BASE."""
    store = InMemoryScoreStore()
    await store.setup()
    for i, (val, tier) in enumerate(values):
        await store.write_score(
            ScoreSample(
                timestamp=BASE + timedelta(seconds=i),
                scope="site",
                subject_id="site",
                kind=ScoreKind.CCS,
                value=val,
                tier=tier,
            )
        )
    return store


def _evaluator(store: InMemoryScoreStore) -> PolicyEvaluator:
    return PolicyEvaluator(
        PolicyEngine(DEFAULT_LIBRARY), store, outage_threshold=40.0, sla_sustain_s=5.0
    )


async def test_no_scores_yet_returns_none() -> None:
    store = InMemoryScoreStore()
    await store.setup()
    assert await _evaluator(store).evaluate_latest() is None


async def test_healthy_recommends_stay() -> None:
    store = await _store_with_ccs([(97.0, "Protected")] * 3)
    r = await _evaluator(store).evaluate_latest()
    assert r is not None and r.recommended_action is DecisionAction.STAY


async def test_sustained_outage_is_breaching_and_recommends_failover() -> None:
    # 6 consecutive sub-40 samples spanning 5s -> dwell met -> failover
    store = await _store_with_ccs([(20.0, "OUTAGE")] * 6)
    ev = _evaluator(store)
    ctx = await ev.latest_context()
    assert ctx is not None and ctx.sla_breaching is True and ctx.sustained_s >= 5.0
    r = await ev.evaluate_latest()
    assert r is not None and r.recommended_action is DecisionAction.FAILOVER


async def test_transient_dip_is_not_breaching_no_failover() -> None:
    # healthy, then a single OUTAGE sample as the newest -> dwell span 0 -> not breaching
    store = await _store_with_ccs(
        [(97.0, "Protected"), (97.0, "Protected"), (97.0, "Protected"), (20.0, "OUTAGE")]
    )
    ev = _evaluator(store)
    ctx = await ev.latest_context()
    assert ctx is not None and ctx.sla_breaching is False  # not sustained
    r = await ev.evaluate_latest()
    # latest tier is OUTAGE but the breach isn't sustained -> no failover (default stay)
    assert r is not None and r.recommended_action is DecisionAction.STAY


async def test_short_outage_under_dwell_not_breaching() -> None:
    # 3s of outage (< 5s sustain) -> not breaching yet
    store = await _store_with_ccs([(20.0, "OUTAGE")] * 3)
    ctx = await _evaluator(store).latest_context()
    assert ctx is not None and ctx.sla_breaching is False and ctx.sustained_s < 5.0
