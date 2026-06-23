"""Application monitoring contracts (CIL-203): clinical endpoint liveness.

The differentiator: a path can be link-healthy while the clinical endpoint is
frozen (the "frozen OR screen still shows healthy" problem). So a probe reports
both ``reachable`` (link/IP up) and ``live`` (the application actually responded
at the application layer). CCS will key off application-level liveness, not link.

Probe depth (link / IP / app-response / render-state) is an **open question**
(needs clinical/ops input). We model all four levels but only verify up to
``APP_RESPONSE`` for now; an endpoint that requires ``RENDER_STATE`` is assessed
against APP_RESPONSE and flagged in ``detail`` until that decision lands.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ProbeDepth(StrEnum):
    """How deep a reachability check goes."""

    LINK = "link"  # physical/L2 link up
    IP = "ip"  # host reachable (ping/TCP)
    APP_RESPONSE = "app_response"  # application answered (HTTP/health)
    RENDER_STATE = "render_state"  # UI actually rendering (deepest; pending input)


# Deepest level we can actually verify today (render-state is pending clinical input).
MAX_SUPPORTED_DEPTH = ProbeDepth.APP_RESPONSE

_DEPTH_ORDER: dict[ProbeDepth, int] = {
    ProbeDepth.LINK: 1,
    ProbeDepth.IP: 2,
    ProbeDepth.APP_RESPONSE: 3,
    ProbeDepth.RENDER_STATE: 4,
}


def _ord(depth: ProbeDepth | None) -> int:
    return _DEPTH_ORDER[depth] if depth is not None else 0


def assess(
    depth_achieved: ProbeDepth | None, required_depth: ProbeDepth
) -> tuple[bool, bool, bool]:
    """Return (reachable, live, healthy) for an achieved vs required depth."""
    effective_required = (
        required_depth if _ord(required_depth) <= _ord(MAX_SUPPORTED_DEPTH) else MAX_SUPPORTED_DEPTH
    )
    reachable = _ord(depth_achieved) >= _ord(ProbeDepth.IP)
    live = _ord(depth_achieved) >= _ord(ProbeDepth.APP_RESPONSE)
    healthy = _ord(depth_achieved) >= _ord(effective_required)
    return reachable, live, healthy


class ClinicalEndpoint(BaseModel):
    """A protected clinical system to monitor (Epic, Cerner, PACS, RIS, OR…)."""

    model_config = ConfigDict(frozen=True)

    name: str
    system: str
    target: str  # host or URL the real probe will check
    required_depth: ProbeDepth = ProbeDepth.APP_RESPONSE
    critical: bool = True


class EndpointHealth(BaseModel):
    """The result of probing a clinical endpoint."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    endpoint: str
    system: str
    reachable: bool  # link/IP reachable
    live: bool  # application-level liveness (frozen-screen differentiator)
    healthy: bool  # meets the endpoint's required (verifiable) depth
    depth_achieved: ProbeDepth | None
    required_depth: ProbeDepth
    latency_ms: float | None = None
    detail: str | None = None


@runtime_checkable
class ApplicationProbe(Protocol):
    """Probes a clinical endpoint for reachability + application liveness."""

    async def probe(self, endpoint: ClinicalEndpoint) -> EndpointHealth:
        """Return the health of ``endpoint``."""
        ...


# A sensible default fleet. App-to-tier mapping is a controlled doc (CCS-APP-001),
# so this will become config-driven; defined here for the simulator-first build.
DEFAULT_CLINICAL_ENDPOINTS: tuple[ClinicalEndpoint, ...] = (
    ClinicalEndpoint(name="epic-ehr", system="Epic", target="https://epic.local/health"),
    ClinicalEndpoint(name="cerner", system="Cerner", target="https://cerner.local/health"),
    ClinicalEndpoint(name="pacs", system="PACS", target="https://pacs.local/health"),
    ClinicalEndpoint(name="ris", system="RIS", target="https://ris.local/health"),
    ClinicalEndpoint(
        name="or-systems",
        system="OR",
        target="https://or.local/health",
        required_depth=ProbeDepth.RENDER_STATE,  # the frozen-screen concern
    ),
)
