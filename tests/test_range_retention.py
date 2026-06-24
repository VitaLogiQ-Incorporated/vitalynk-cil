"""ts_us range reads + retention purge — the critical id-order≠ts-order fix."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from cil.storage.interface import TelemetryStore
from cil.storage.memory import InMemoryTelemetryStore
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample
from cil.timeutil import to_us

BASE = datetime(2026, 1, 1, tzinfo=UTC)

STORES: list[Callable[[], TelemetryStore]] = [
    InMemoryTelemetryStore,
    lambda: SQLiteTelemetryStore(":memory:"),
]


def sample_at(i: int) -> TelemetrySample:
    return TelemetrySample(
        timestamp=BASE + timedelta(seconds=i),
        path_id="modem-a",
        carrier="c",
        profile="p",
        radio=RadioMetrics(),
        network=NetworkMetrics(reachable=True),
        device=DeviceMetrics(),
    )


@pytest.mark.parametrize("make", STORES)
async def test_read_range_is_ts_ascending_despite_insertion_order(
    make: Callable[[], TelemetryStore],
) -> None:
    store = make()
    await store.setup()
    # Insert OUT of time order (id order != ts order — the critical bug class).
    for i in (3, 0, 2, 1, 4):
        await store.write_sample(sample_at(i))

    rows = await store.read_range(start_us=to_us(BASE), end_us=to_us(BASE + timedelta(seconds=10)))
    seconds = [r.timestamp.second for r in rows]
    assert seconds == [0, 1, 2, 3, 4]  # ts-ascending, not insertion order
    await store.close()


@pytest.mark.parametrize("make", STORES)
async def test_read_range_bounds_inclusive(make: Callable[[], TelemetryStore]) -> None:
    store = make()
    await store.setup()
    for i in range(10):
        await store.write_sample(sample_at(i))
    rows = await store.read_range(
        start_us=to_us(BASE + timedelta(seconds=2)),
        end_us=to_us(BASE + timedelta(seconds=5)),
    )
    assert [r.timestamp.second for r in rows] == [2, 3, 4, 5]
    await store.close()


@pytest.mark.parametrize("make", STORES)
async def test_delete_older_than_protects_excluded_ranges(
    make: Callable[[], TelemetryStore],
) -> None:
    store = make()
    await store.setup()
    for i in range(6):
        await store.write_sample(sample_at(i))
    cutoff = to_us(BASE + timedelta(seconds=4))
    pinned = [(to_us(BASE + timedelta(seconds=1)), to_us(BASE + timedelta(seconds=2)))]
    deleted = await store.delete_older_than(cutoff, exclude_ranges=pinned)
    assert deleted == 2  # seconds 0 and 3 (1,2 pinned; 4,5 newer than cutoff)
    remaining = await store.read_range(start_us=0, end_us=to_us(BASE + timedelta(seconds=10)))
    assert [r.timestamp.second for r in remaining] == [1, 2, 4, 5]
    await store.close()
