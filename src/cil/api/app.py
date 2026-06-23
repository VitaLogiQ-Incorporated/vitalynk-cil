"""FastAPI application factory + lifespan wiring.

The single deployable surface for the modular monolith. Mounts the core ops and
telemetry routes and the Prometheus ``/metrics`` endpoint, configures structured
logging, and — when enabled — runs the telemetry ingest loop
(simulator -> normalize -> store) as a background task for the process lifetime.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from cil import __version__
from cil.api.metrics import prometheus_middleware
from cil.api.routes import router
from cil.config import Settings, get_settings
from cil.logging import configure_logging, get_logger
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.storage.sqlite_app import SQLiteApplicationHealthStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.probes import DEFAULT_CLINICAL_ENDPOINTS
from cil.telemetry.simprobe import SimulatedClinicalProbe
from cil.telemetry.simulator import SimulatorAdapter


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the CIL FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    log = get_logger("cil.api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info(
            "cil.startup",
            service=settings.app_name,
            version=__version__,
            env=settings.env,
            telemetry_enabled=settings.telemetry_enabled,
        )

        store: SQLiteTelemetryStore | None = None
        app_store: SQLiteApplicationHealthStore | None = None
        tasks: list[asyncio.Task[int]] = []

        if settings.telemetry_enabled:
            store = SQLiteTelemetryStore(settings.telemetry_db_path)
            await store.setup()
            collector = TelemetryCollector(
                SimulatorAdapter(), store, interval_s=settings.telemetry_interval_s
            )
            app.state.store = store
            app.state.collector = collector
            tasks.append(asyncio.create_task(collector.run()))

        if settings.app_monitoring_enabled:
            app_store = SQLiteApplicationHealthStore(settings.telemetry_db_path)
            await app_store.setup()
            app_monitor = ApplicationMonitor(
                SimulatedClinicalProbe(),
                DEFAULT_CLINICAL_ENDPOINTS,
                app_store,
                interval_s=settings.app_monitoring_interval_s,
            )
            app.state.app_store = app_store
            app.state.app_monitor = app_monitor
            tasks.append(asyncio.create_task(app_monitor.run()))

        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            if store is not None:
                await store.close()
            if app_store is not None:
                await app_store.close()
            log.info("cil.shutdown", service=settings.app_name)

    app = FastAPI(
        title="VitaLynk CIL (UC1)",
        version=__version__,
        summary="Carrier Intelligence Layer — deterministic clinical network continuity.",
        lifespan=lifespan,
    )

    app.add_middleware(BaseHTTPMiddleware, dispatch=prometheus_middleware)
    app.include_router(router)

    return app
