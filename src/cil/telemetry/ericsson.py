"""Live Ericsson NetCloud adapter — SHELL ONLY (live binding deferred).

This implements the same ``TelemetryAdapter`` contract as the simulator, but the
live NetCloud/E400 calls are intentionally not implemented yet. Per EPIC-02/07,
the live binding is co-owned with the integration owner and lands once NetCloud
access / a device is available.

When implemented, ``sample()`` will fetch raw NetCloud telemetry and pass it
through :func:`cil.telemetry.normalize.normalize` — the *same* normalization the
simulator uses — so the rest of the system is unaffected by the swap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cil.telemetry.schema import TelemetrySample


@dataclass
class EricssonNetCloudAdapter:
    """Placeholder for the live Ericsson telemetry binding (not yet wired)."""

    base_url: str | None = None
    api_key: str | None = None
    path_ids: list[str] = field(default_factory=list)

    async def sample(self) -> TelemetrySample:
        raise NotImplementedError(
            "Live Ericsson NetCloud binding is pending (EPIC-07 / integration owner). "
            "It will fetch raw NetCloud telemetry and call cil.telemetry.normalize."
        )

    async def list_paths(self) -> list[str]:
        if not self.path_ids:
            raise NotImplementedError(
                "Live Ericsson path discovery is pending (EPIC-07 / integration owner)."
            )
        return list(self.path_ids)
