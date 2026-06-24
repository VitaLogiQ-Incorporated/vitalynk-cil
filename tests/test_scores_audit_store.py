"""Tests for the score-sample + audit stores (CIL-301)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from cil.audit.events import AuditRecord, ScoreKind, ScoreSample
from cil.storage.interface import AuditStore, ScoreStore
from cil.storage.memory import InMemoryAuditStore, InMemoryScoreStore
from cil.storage.sqlite_audit import SQLiteAuditStore
from cil.storage.sqlite_scores import SQLiteScoreStore
from cil.timeutil import to_us

BASE = datetime(2026, 1, 1, tzinfo=UTC)

SCORE_STORES: list[Callable[[], ScoreStore]] = [
    InMemoryScoreStore,
    lambda: SQLiteScoreStore(":memory:"),
]
AUDIT_STORES: list[Callable[[], AuditStore]] = [
    InMemoryAuditStore,
    lambda: SQLiteAuditStore(":memory:"),
]


def score(i: int, value: float) -> ScoreSample:
    return ScoreSample(
        timestamp=BASE + timedelta(seconds=i),
        scope="path",
        subject_id="modem-a",
        kind=ScoreKind.CCS,
        value=value,
    )


@pytest.mark.parametrize("make", SCORE_STORES)
async def test_score_roundtrip_and_range(make: Callable[[], ScoreStore]) -> None:
    store = make()
    await store.setup()
    assert isinstance(store, ScoreStore)
    scores = [score(i, 90 - i) for i in range(10)]
    assert await store.write_scores(scores) == 10
    assert await store.count() == 10

    # read_score_range is unbounded + ts-ascending
    rng = await store.read_score_range(
        start_us=to_us(BASE), end_us=to_us(BASE + timedelta(seconds=4))
    )
    assert [s.value for s in rng] == [90, 89, 88, 87, 86]
    await store.close()


@pytest.mark.parametrize("make", SCORE_STORES)
async def test_score_delete_older_than_with_exclusion(make: Callable[[], ScoreStore]) -> None:
    store = make()
    await store.setup()
    await store.write_scores([score(i, 50) for i in range(10)])
    cutoff = to_us(BASE + timedelta(seconds=5))
    # protect seconds 1..2 from the purge
    pinned = [(to_us(BASE + timedelta(seconds=1)), to_us(BASE + timedelta(seconds=2)))]
    deleted = await store.delete_older_than(cutoff, exclude_ranges=pinned)
    assert deleted == 3  # seconds 0,3,4 (1,2 pinned; >=5 kept)
    assert await store.count() == 7
    await store.close()


@pytest.mark.parametrize("make", AUDIT_STORES)
async def test_audit_append_read(make: Callable[[], AuditStore]) -> None:
    store = make()
    await store.setup()
    assert isinstance(store, AuditStore)
    for i in range(3):
        await store.append(
            AuditRecord(
                timestamp=BASE + timedelta(seconds=i),
                actor="labeler",
                action="label",
                event_id=f"evt_{i}",
                outcome="NO_ACTION",
            )
        )
    rows = await store.read(limit=100)
    assert [r.event_id for r in rows] == ["evt_0", "evt_1", "evt_2"]
    assert not hasattr(store, "delete_older_than")  # audit is never deleted
    await store.close()
