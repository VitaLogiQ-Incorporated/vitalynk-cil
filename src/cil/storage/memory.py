"""In-memory telemetry store.

A dependency-free implementation of ``TelemetryStore`` — handy for tests and a
concrete demonstration that callers depend only on the interface, not on SQLite.
Not durable: data is lost on process exit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cil.telemetry.probes import EndpointHealth
    from cil.telemetry.schema import TelemetrySample


class InMemoryTelemetryStore:
    """Keeps samples in a list. Implements ``TelemetryStore``."""

    def __init__(self) -> None:
        self._rows: list[TelemetrySample] = []

    async def setup(self) -> None:
        return None

    async def write_sample(self, sample: TelemetrySample) -> None:
        self._rows.append(sample)

    async def write_samples(self, samples: Iterable[TelemetrySample]) -> int:
        count = 0
        for sample in samples:
            self._rows.append(sample)
            count += 1
        return count

    async def read_samples(
        self, *, path_id: str | None = None, limit: int = 100
    ) -> list[TelemetrySample]:
        rows = [r for r in self._rows if path_id is None or r.path_id == path_id]
        return rows[-limit:]

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class InMemoryApplicationHealthStore:
    """In-memory ``ApplicationHealthStore`` for tests and demos."""

    def __init__(self) -> None:
        self._rows: list[EndpointHealth] = []

    async def setup(self) -> None:
        return None

    async def write_health(self, health: EndpointHealth) -> None:
        self._rows.append(health)

    async def read_health(
        self, *, endpoint: str | None = None, limit: int = 100
    ) -> list[EndpointHealth]:
        rows = [r for r in self._rows if endpoint is None or r.endpoint == endpoint]
        return rows[-limit:]

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None
