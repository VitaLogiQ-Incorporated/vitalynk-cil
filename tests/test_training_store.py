"""Tests for the training store (CIL-302): verbatim roundtrip + no-purge guarantee."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cil.audit.events import TelemetryWindow, window_id_for
from cil.storage.interface import TrainingStore
from cil.storage.memory import InMemoryTrainingStore
from cil.storage.sqlite_training import SQLiteTrainingStore
from cil.telemetry.simulator import SimulatorAdapter
from cil.timeutil import to_us

BASE = datetime(2026, 1, 1, tzinfo=UTC)

TRAINING_STORES: list[Callable[[], TrainingStore]] = [
    InMemoryTrainingStore,
    lambda: SQLiteTrainingStore(":memory:"),
]


def header(event_id: str = "evt_0", n: int = 6) -> TelemetryWindow:
    return TelemetryWindow(
        window_id=window_id_for(event_id),
        event_id=event_id,
        center_ts=BASE,
        start_ts=BASE - timedelta(seconds=n),
        end_ts=BASE + timedelta(seconds=n),
        start_us=to_us(BASE - timedelta(seconds=n)),
        end_us=to_us(BASE + timedelta(seconds=n)),
        before_s=float(n),
        after_s=float(n),
    )


async def _samples(n: int) -> list:
    sim = SimulatorAdapter(seed=5)
    return [await sim.sample() for _ in range(n)]


@pytest.mark.parametrize("make", TRAINING_STORES)
async def test_training_telemetry_roundtrip_byte_identical(
    make: Callable[[], TrainingStore],
) -> None:
    store = make()
    await store.setup()
    assert isinstance(store, TrainingStore)
    wid = window_id_for("evt_0")
    await store.write_window(header())
    samples = await _samples(6)
    assert await store.write_telemetry_rows(wid, samples) == 6

    read_back = await store.read_window_rows(wid)
    assert read_back == samples  # schema-drift guard: verbatim, no downsampling
    await store.close()


@pytest.mark.parametrize("make", TRAINING_STORES)
async def test_training_has_no_purge_path(make: Callable[[], TrainingStore]) -> None:
    store = make()
    await store.setup()
    # The absence of a delete path IS the indefinite-retention guarantee.
    assert not hasattr(store, "delete_older_than")
    assert not hasattr(store, "purge")
    await store.close()


@pytest.mark.parametrize("make", TRAINING_STORES)
async def test_list_unfinalized(make: Callable[[], TrainingStore]) -> None:
    store = make()
    await store.setup()
    await store.write_window(header("evt_open"))  # complete_post defaults False
    finalized = header("evt_done").model_copy(update={"complete_post": True})
    await store.write_window(finalized)

    unfinalized = await store.list_unfinalized()
    assert [w.event_id for w in unfinalized] == ["evt_open"]
    await store.close()


async def test_training_survives_reboot(tmp_path: Path) -> None:
    db = str(tmp_path / "training.db")
    s1 = SQLiteTrainingStore(db)
    await s1.setup()
    await s1.write_window(header())
    await s1.write_telemetry_rows(window_id_for("evt_0"), await _samples(3))
    await s1.close()

    s2 = SQLiteTrainingStore(db)
    await s2.setup()
    assert await s2.count() == 1
    assert len(await s2.read_window_rows(window_id_for("evt_0"))) == 3
    await s2.close()
