"""Live Ericsson NetCloud telemetry adapter (CIL-201).

The brain-side adapter is complete: it fetches raw NetCloud telemetry via an
injected ``NetCloudClient`` and runs it through the shared
:func:`cil.telemetry.normalize.normalize` — the *same* normalization the simulator
uses, so the rest of the system is unaffected by the swap.

The only piece left to ops/EPIC-07 (CIL-701/702) is the ``NetCloudClient``
implementation — the actual NetCloud/E400 HTTP transport. Until one is injected,
``sample()`` raises a clear error pointing there. This keeps the seam owned here
(intelligence) and the transport owned there (integration).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from cil.logging import get_logger
from cil.telemetry.normalize import normalize
from cil.telemetry.schema import TelemetrySample


@runtime_checkable
class NetCloudClient(Protocol):
    """Raw Ericsson NetCloud transport — implemented by ops/EPIC-07.

    Returns raw telemetry in the shape ``normalize`` expects (see
    ``cil.telemetry.normalize.RAW_FIELDS`` for the contract).
    """

    async def fetch_raw(self) -> Mapping[str, Any]:
        """Fetch one raw telemetry reading for the active path."""
        ...

    async def list_paths(self) -> list[str]:
        """List available WAN path identifiers."""
        ...


class EricssonNetCloudAdapter:
    """Live Ericsson telemetry adapter. Implements ``TelemetryAdapter``."""

    def __init__(self, client: NetCloudClient | None = None) -> None:
        self._client = client
        self._log = get_logger("cil.telemetry.ericsson")

    async def sample(self) -> TelemetrySample:
        client = self._require_client()
        raw = await client.fetch_raw()
        return normalize(raw, logger=self._log)

    async def list_paths(self) -> list[str]:
        client = self._require_client()
        return await client.list_paths()

    def _require_client(self) -> NetCloudClient:
        if self._client is None:
            raise NotImplementedError(
                "No NetCloudClient injected. The live NetCloud/E400 transport is "
                "provided by ops/EPIC-07 (CIL-701/702); construct the adapter as "
                "EricssonNetCloudAdapter(client=<NetCloudClient>). The adapter then "
                "normalizes raw NetCloud telemetry via cil.telemetry.normalize."
            )
        return self._client
