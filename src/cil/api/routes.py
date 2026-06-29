"""Core HTTP routes: liveness, status, and root."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from cil import __version__
from cil.api.metrics import render_metrics
from cil.audit.events import AuditRecord, ContinuityEvent, ScoreSample, TelemetryWindow
from cil.config import get_settings
from cil.storage.export import TrainingExporter
from cil.storage.interface import AuditStore, EventStore, ScoreStore, TelemetryStore, TrainingStore
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


@router.get("/events", tags=["data-platform"])
async def events(request: Request, limit: int = 50) -> list[ContinuityEvent]:
    """Recent continuity events from the immutable event spine (newest-first)."""
    store = getattr(request.app.state, "event_store", None)
    if not isinstance(store, EventStore):
        return []
    return await store.read_events(limit=limit)


@router.get("/events/{event_id}", tags=["data-platform"], response_model=None)
async def event_detail(request: Request, event_id: str) -> ContinuityEvent | JSONResponse:
    """A single continuity event by id."""
    store = getattr(request.app.state, "event_store", None)
    if isinstance(store, EventStore):
        event = await store.get_event(event_id)
        if event is not None:
            return event
    return JSONResponse({"detail": "event not found"}, status_code=404)


@router.get("/scores", tags=["data-platform"])
async def scores(request: Request, limit: int = 50) -> list[ScoreSample]:
    """Recent CQS/CCS score samples (newest-first)."""
    store = getattr(request.app.state, "score_store", None)
    if not isinstance(store, ScoreStore):
        return []
    return await store.read_scores(limit=limit)


@router.get("/audit", tags=["data-platform"])
async def audit(request: Request, limit: int = 50) -> list[AuditRecord]:
    """Recent audit records (newest-first)."""
    store = getattr(request.app.state, "audit_store", None)
    if not isinstance(store, AuditStore):
        return []
    return await store.read(limit=limit)


@router.get("/training/windows", tags=["data-platform"])
async def training_windows(request: Request, limit: int = 50) -> list[TelemetryWindow]:
    """Captured ±15-min training windows (the indefinite UC2 dataset headers)."""
    store = getattr(request.app.state, "training_store", None)
    if not isinstance(store, TrainingStore):
        return []
    return await store.list_windows(limit=limit)


@router.get("/training/windows/{window_id}", tags=["data-platform"], response_model=None)
async def training_window_export(request: Request, window_id: str) -> JSONResponse:
    """A single window's self-describing export (header + native rows + label)."""
    store = getattr(request.app.state, "training_store", None)
    if isinstance(store, TrainingStore):
        record = await TrainingExporter(store).export_window(window_id)
        if record is not None:
            return JSONResponse(record)
    return JSONResponse({"detail": "window not found"}, status_code=404)


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
