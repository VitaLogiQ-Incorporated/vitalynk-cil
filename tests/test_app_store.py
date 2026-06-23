"""Tests for the application-health stores: roundtrip + durability."""

from __future__ import annotations

from pathlib import Path

import pytest

from cil.storage.interface import ApplicationHealthStore
from cil.storage.memory import InMemoryApplicationHealthStore
from cil.storage.sqlite_app import SQLiteApplicationHealthStore
from cil.telemetry.probes import DEFAULT_CLINICAL_ENDPOINTS
from cil.telemetry.simprobe import SimulatedClinicalProbe


async def _health(n: int) -> list:
    probe = SimulatedClinicalProbe(seed=11)
    out = []
    for _ in range(n):
        for ep in DEFAULT_CLINICAL_ENDPOINTS:
            out.append(await probe.probe(ep))
    return out


async def test_memory_store_is_an_app_health_store() -> None:
    assert isinstance(InMemoryApplicationHealthStore(), ApplicationHealthStore)


async def test_sqlite_store_is_an_app_health_store() -> None:
    assert isinstance(SQLiteApplicationHealthStore(":memory:"), ApplicationHealthStore)


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_write_read_roundtrip(kind: str) -> None:
    store: ApplicationHealthStore = (
        InMemoryApplicationHealthStore()
        if kind == "memory"
        else SQLiteApplicationHealthStore(":memory:")
    )
    await store.setup()
    health = await _health(2)
    for h in health:
        await store.write_health(h)

    assert await store.count() == len(health)
    assert await store.read_health(limit=1000) == health
    await store.close()


async def test_sqlite_app_health_survives_reboot(tmp_path: Path) -> None:
    db = str(tmp_path / "op.db")
    health = await _health(1)

    store1 = SQLiteApplicationHealthStore(db)
    await store1.setup()
    for h in health:
        await store1.write_health(h)
    await store1.close()

    store2 = SQLiteApplicationHealthStore(db)
    await store2.setup()
    assert await store2.count() == len(health)
    assert await store2.read_health(limit=1000) == health
    await store2.close()
