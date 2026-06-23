"""The storage interface — callers depend on this, never on a concrete store.

Abstracting the store behind a Protocol is what lets UC1 ship on SQLite while
keeping the door open to a time-series DB later (CLAUDE.md §3/§5) with zero
changes to callers. ``@runtime_checkable`` so wiring code can guard with
``isinstance``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from cil.telemetry.probes import EndpointHealth
    from cil.telemetry.schema import TelemetrySample


@runtime_checkable
class TelemetryStore(Protocol):
    """Persistence for normalized telemetry samples."""

    async def setup(self) -> None:
        """Create/connect the backing store. Idempotent."""
        ...

    async def write_sample(self, sample: TelemetrySample) -> None:
        """Persist a single sample at native resolution (no downsampling)."""
        ...

    async def write_samples(self, samples: Iterable[TelemetrySample]) -> int:
        """Persist many samples; return the count written."""
        ...

    async def read_samples(
        self, *, path_id: str | None = None, limit: int = 100
    ) -> list[TelemetrySample]:
        """Return up to ``limit`` most-recent samples, oldest-first."""
        ...

    async def count(self) -> int:
        """Return the total number of stored samples."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class ApplicationHealthStore(Protocol):
    """Persistence for clinical endpoint health results (CIL-203)."""

    async def setup(self) -> None:
        """Create/connect the backing store. Idempotent."""
        ...

    async def write_health(self, health: EndpointHealth) -> None:
        """Persist a single endpoint health result."""
        ...

    async def read_health(
        self, *, endpoint: str | None = None, limit: int = 100
    ) -> list[EndpointHealth]:
        """Return up to ``limit`` most-recent results, oldest-first."""
        ...

    async def count(self) -> int:
        """Return the total number of stored results."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...
