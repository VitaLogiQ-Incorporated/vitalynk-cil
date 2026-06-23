"""The ``TelemetryAdapter`` interface — the swappable telemetry seam.

Both the simulator (now) and the live Ericsson NetCloud adapter (later, co-owned
with the integration owner under EPIC-07) implement this Protocol. Because the
brain depends only on this interface, the live binding drops in without touching
scoring, policy, decision, or storage.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cil.telemetry.schema import TelemetrySample


@runtime_checkable
class TelemetryAdapter(Protocol):
    """A source of normalized telemetry samples."""

    async def sample(self) -> TelemetrySample:
        """Return the latest normalized sample for the currently active path."""
        ...

    async def list_paths(self) -> list[str]:
        """Return the available WAN path identifiers (e.g. the two modems)."""
        ...
