"""Simulated clinical probe — a mock ``ApplicationProbe`` (CIL-203 / CIL-204).

Lets the whole application-monitoring path be built and tested without real
clinical systems. Each endpoint can be put into a condition, including the
critical **FROZEN** case: reachable at the IP layer but not responding at the
application layer — exactly the "frozen OR screen" failure CCS must catch.

Deterministic: timestamps come from a tick counter, noise from a seeded RNG.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from random import Random

from cil.telemetry.probes import (
    MAX_SUPPORTED_DEPTH,
    ClinicalEndpoint,
    EndpointHealth,
    ProbeDepth,
    assess,
)

# Deterministic clock origin for generated timestamps.
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class EndpointCondition(StrEnum):
    HEALTHY = "healthy"
    SLOW = "slow"
    FROZEN = "frozen"  # reachable but application not responding
    UNREACHABLE = "unreachable"


def _capped_depth(required: ProbeDepth) -> ProbeDepth:
    order = {
        ProbeDepth.LINK: 1,
        ProbeDepth.IP: 2,
        ProbeDepth.APP_RESPONSE: 3,
        ProbeDepth.RENDER_STATE: 4,
    }
    return required if order[required] <= order[MAX_SUPPORTED_DEPTH] else MAX_SUPPORTED_DEPTH


class SimulatedClinicalProbe:
    """A mock ``ApplicationProbe`` with injectable per-endpoint conditions."""

    def __init__(
        self,
        *,
        seed: int = 0,
        default_condition: EndpointCondition = EndpointCondition.HEALTHY,
    ) -> None:
        self._rng = Random(seed)
        self._default = default_condition
        self._conditions: dict[str, EndpointCondition] = {}
        self._tick = 0

    def set_condition(self, endpoint_name: str, condition: EndpointCondition) -> None:
        self._conditions[endpoint_name] = condition

    async def probe(self, endpoint: ClinicalEndpoint) -> EndpointHealth:
        condition = self._conditions.get(endpoint.name, self._default)
        timestamp = _EPOCH + timedelta(seconds=self._tick)
        self._tick += 1

        depth_achieved: ProbeDepth | None
        latency_ms: float | None
        detail: str | None = None

        if condition == EndpointCondition.UNREACHABLE:
            depth_achieved, latency_ms = None, None
            detail = "host unreachable"
        elif condition == EndpointCondition.FROZEN:
            depth_achieved, latency_ms = ProbeDepth.IP, None
            detail = "reachable but application not responding (frozen)"
        elif condition == EndpointCondition.SLOW:
            depth_achieved = _capped_depth(endpoint.required_depth)
            latency_ms = 900.0 + self._rng.random() * 600.0
            detail = "degraded: high application latency"
        else:  # HEALTHY
            depth_achieved = _capped_depth(endpoint.required_depth)
            latency_ms = 20.0 + self._rng.random() * 25.0

        if endpoint.required_depth == ProbeDepth.RENDER_STATE and depth_achieved is not None:
            note = "render-state probing not yet available (pending clinical input)"
            detail = f"{detail}; {note}" if detail else note

        reachable, live, healthy = assess(depth_achieved, endpoint.required_depth)
        return EndpointHealth(
            timestamp=timestamp,
            endpoint=endpoint.name,
            system=endpoint.system,
            reachable=reachable,
            live=live,
            healthy=healthy,
            depth_achieved=depth_achieved,
            required_depth=endpoint.required_depth,
            latency_ms=latency_ms,
            detail=detail,
        )
