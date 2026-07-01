"""FastAPI application factory + lifespan wiring.

The single deployable surface for the modular monolith. On startup it brings up,
per config: the WAN telemetry ingest loop, the clinical application monitor, and
the **data platform** (event bus → labeling pipeline → ±15-min window capture →
retention sweeper). The ApplicationMonitor publishes endpoint state-changes onto
the bus, and a periodic NO_ACTION sampler supplies the training-set negative class.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

from cil import __version__
from cil.api.metrics import prometheus_middleware
from cil.api.routes import router
from cil.audit.bus import EventBus
from cil.audit.events import (
    ContinuityEvent,
    EventKind,
    EventSource,
    new_event_id,
)
from cil.audit.labeler import EventLabeler, load_labeling_config
from cil.audit.pipeline import LabelingPipeline
from cil.audit.window_capture import WindowCaptureService
from cil.config import Settings, get_settings
from cil.logging import configure_logging, get_logger
from cil.scoring.ccs import CCSEngine, load_ccs_config, load_ccs_tiers
from cil.scoring.cqs import CQSEngine, load_cqs_config
from cil.scoring.service import ScoringService
from cil.storage.retention import RetentionSweeper
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.storage.sqlite_app import SQLiteApplicationHealthStore
from cil.storage.sqlite_audit import SQLiteAuditStore
from cil.storage.sqlite_events import SQLiteEventStore, SQLiteLabelStore
from cil.storage.sqlite_scores import SQLiteScoreStore
from cil.storage.sqlite_training import SQLiteTrainingStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.probes import EndpointHealth, load_clinical_endpoints
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
            app_monitoring_enabled=settings.app_monitoring_enabled,
            data_platform_enabled=settings.data_platform_enabled,
        )

        stores: list[Any] = []
        tasks: list[asyncio.Task[object]] = []
        op_db = settings.telemetry_db_path

        # Telemetry + app-health stores are shared by the monitors AND the data
        # platform's window capture, so create them if either side needs them.
        need_telemetry = settings.telemetry_enabled or settings.data_platform_enabled
        need_app = settings.app_monitoring_enabled or settings.data_platform_enabled

        store: SQLiteTelemetryStore | None = None
        app_store: SQLiteApplicationHealthStore | None = None
        score_store: SQLiteScoreStore | None = None
        collector: TelemetryCollector | None = None
        app_monitor: ApplicationMonitor | None = None
        if need_telemetry:
            store = SQLiteTelemetryStore(op_db)
            await store.setup()
            stores.append(store)
            app.state.store = store
        if need_app:
            app_store = SQLiteApplicationHealthStore(op_db)
            await app_store.setup()
            stores.append(app_store)
            app.state.app_store = app_store

        # ---- Data platform (EPIC-03) ----
        bus: EventBus | None = None
        event_sink = None
        if settings.data_platform_enabled and store is not None and app_store is not None:
            event_store = SQLiteEventStore(op_db)
            label_store = SQLiteLabelStore(op_db)
            score_store = SQLiteScoreStore(op_db)
            audit_store = SQLiteAuditStore(op_db)
            training_store = SQLiteTrainingStore(settings.training_db_path)
            for s in (event_store, label_store, score_store, audit_store, training_store):
                await s.setup()
                stores.append(s)

            labeler = EventLabeler(
                load_labeling_config(settings.ccs_tiers_path, settings.labeling_config_path)
            )
            capture = WindowCaptureService(
                store,
                app_store,
                score_store,
                training_store,
                audit_store,
                before_s=settings.window_before_s,
                after_s=settings.window_after_s,
                min_radius_s=settings.window_min_radius_s,
                sample_interval_s=settings.telemetry_interval_s,
            )
            pipeline = LabelingPipeline(
                event_store=event_store,
                label_store=label_store,
                score_store=score_store,
                capture=capture,
                labeler=labeler,
                audit=audit_store,
                training=training_store,
            )
            bus = EventBus(event_store)
            bus.subscribe(pipeline)
            sweeper = RetentionSweeper(
                store,
                app_store,
                score_store,
                training_store,
                audit_store,
                retention_days=settings.operational_retention_days,
            )

            app.state.event_store = event_store
            app.state.label_store = label_store
            app.state.score_store = score_store
            app.state.audit_store = audit_store
            app.state.training_store = training_store
            app.state.event_bus = bus

            # Rebuild SLA dwell state from the score timeline before serving.
            with contextlib.suppress(Exception):
                await pipeline.replay_sla(now=datetime.now(UTC), horizon_s=3600)

            async def _publish(health: EndpointHealth, kind: str) -> None:
                ekind = EventKind[kind]  # "ENDPOINT_FROZEN" -> EventKind.ENDPOINT_FROZEN
                assert bus is not None
                await bus.emit(
                    ContinuityEvent(
                        event_id=new_event_id(health.timestamp, ekind, health.endpoint),
                        timestamp=health.timestamp,
                        kind=ekind,
                        source=EventSource.APP_MONITOR,
                        endpoint=health.endpoint,
                        system=health.system,
                        reachable=health.reachable,
                        live=health.live,
                        attributes={"required_depth": health.required_depth.value},
                        detail=health.detail,
                    )
                )

            event_sink = _publish

            async def _retention_loop() -> None:
                while True:
                    await asyncio.sleep(settings.retention_sweep_interval_s)
                    if settings.retention_enabled:
                        await sweeper.sweep_once(datetime.now(UTC))

            async def _finalizer_loop() -> None:
                while True:
                    await asyncio.sleep(max(settings.window_after_s, 1.0))
                    await capture.finalize_due(datetime.now(UTC))

            async def _no_action_sampler() -> None:
                seq = 0
                while True:
                    await asyncio.sleep(settings.no_action_sample_interval_s)
                    seq += 1
                    now = datetime.now(UTC)
                    assert bus is not None
                    await bus.emit(
                        ContinuityEvent(
                            event_id=new_event_id(now, EventKind.NO_ACTION_SAMPLE, str(seq)),
                            timestamp=now,
                            kind=EventKind.NO_ACTION_SAMPLE,
                            source=EventSource.SYNTHETIC,
                            path_id=settings.scoring_primary_path,
                        )
                    )

            tasks.append(asyncio.create_task(_retention_loop()))
            tasks.append(asyncio.create_task(_finalizer_loop()))
            tasks.append(asyncio.create_task(_no_action_sampler()))

        # ---- Monitors (producers) ----
        if settings.telemetry_enabled and store is not None:
            collector = TelemetryCollector(
                SimulatorAdapter(), store, interval_s=settings.telemetry_interval_s
            )
            app.state.collector = collector
            tasks.append(asyncio.create_task(collector.run()))

        if settings.app_monitoring_enabled and app_store is not None:
            app_monitor = ApplicationMonitor(
                SimulatedClinicalProbe(),
                load_clinical_endpoints(settings.clinical_endpoints_path),
                app_store,
                interval_s=settings.app_monitoring_interval_s,
                event_sink=event_sink,
            )
            app.state.app_monitor = app_monitor
            tasks.append(asyncio.create_task(app_monitor.run()))

        # ---- Scoring loop (EPIC-04: CQS + CCS) ----
        if settings.scoring_enabled and bus is not None and score_store is not None:
            tiers = load_ccs_tiers(settings.ccs_tiers_path)
            scoring = ScoringService(
                cqs=CQSEngine(load_cqs_config(settings.cqs_config_path)),
                ccs=CCSEngine(load_ccs_config(settings.ccs_config_path), tiers),
                score_store=score_store,
                telemetry_provider=lambda: collector.latest if collector is not None else None,
                health_provider=lambda: (
                    list(app_monitor.latest.values()) if app_monitor is not None else []
                ),
                bus=bus,
                outage_threshold=tiers.outage_threshold,
                sla_sustain_s=tiers.sla_sustain_s,
                site_id=settings.scoring_site_id,
                primary_path=settings.scoring_primary_path,
            )
            app.state.scoring = scoring
            tasks.append(asyncio.create_task(scoring.run(interval_s=settings.scoring_interval_s)))
        elif settings.scoring_enabled:
            # scoring needs the event bus + score store, which only the data platform
            # creates — surface the silent no-op instead of doing nothing quietly.
            log.warning(
                "scoring.disabled",
                reason="scoring_enabled=True but needs data_platform_enabled (bus + score store)",
            )

        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            for resource in stores:
                await resource.close()
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
