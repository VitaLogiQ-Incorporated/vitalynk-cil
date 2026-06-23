"""Core HTTP routes: liveness, status, and root."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from cil import __version__
from cil.api.metrics import render_metrics
from cil.config import get_settings
from cil.storage.interface import TelemetryStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.probes import ClinicalEndpoint, EndpointHealth
from cil.telemetry.schema import TelemetrySample

router = APIRouter()

# Process start time, used to report uptime.
_STARTED_MONOTONIC = time.monotonic()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class StatusResponse(BaseModel):
    service: str
    version: str
    env: str
    uptime_seconds: float


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness probe. Returns 200 when the process is up and serving."""
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.app_name, version=__version__)


@router.get("/status", response_model=StatusResponse, tags=["ops"])
def status() -> StatusResponse:
    """Non-secret runtime status."""
    settings = get_settings()
    return StatusResponse(
        service=settings.app_name,
        version=__version__,
        env=settings.env,
        uptime_seconds=round(time.monotonic() - _STARTED_MONOTONIC, 3),
    )


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition for the default registry."""
    return render_metrics()


@router.get("/telemetry/latest", tags=["telemetry"], response_model=None)
def telemetry_latest(request: Request) -> TelemetrySample | JSONResponse:
    """The most recent ingested telemetry sample."""
    collector = getattr(request.app.state, "collector", None)
    if not isinstance(collector, TelemetryCollector) or collector.latest is None:
        return JSONResponse({"detail": "no samples yet"}, status_code=404)
    return collector.latest


@router.get("/telemetry/count", tags=["telemetry"])
async def telemetry_count(request: Request) -> dict[str, int]:
    """Total number of telemetry samples persisted so far."""
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, TelemetryStore):
        return {"count": 0}
    return {"count": await store.count()}


@router.get("/telemetry/recent", tags=["telemetry"])
async def telemetry_recent(request: Request, limit: int = 20) -> list[TelemetrySample]:
    """The most recent telemetry samples (oldest-first)."""
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, TelemetryStore):
        return []
    return await store.read_samples(limit=limit)


@router.get("/clinical/health", tags=["clinical"])
def clinical_health(request: Request) -> list[EndpointHealth]:
    """Latest health for each monitored clinical endpoint."""
    monitor = getattr(request.app.state, "app_monitor", None)
    if not isinstance(monitor, ApplicationMonitor):
        return []
    return list(monitor.latest.values())


@router.get("/clinical/endpoints", tags=["clinical"])
def clinical_endpoints(request: Request) -> list[ClinicalEndpoint]:
    """The configured clinical endpoints being monitored."""
    monitor = getattr(request.app.state, "app_monitor", None)
    if not isinstance(monitor, ApplicationMonitor):
        return []
    return monitor.endpoints


@router.get("/", tags=["ops"])
def root() -> dict[str, str]:
    """Service banner."""
    return {
        "service": get_settings().app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }
