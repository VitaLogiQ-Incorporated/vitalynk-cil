"""Export seam (EPIC-03) — operational JSONL + self-describing training windows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cil.audit.events import (
    AuditRecord,
    ContinuityEvent,
    EventKind,
    EventSource,
    ScoreKind,
    ScoreSample,
    TelemetryWindow,
    new_event_id,
    window_id_for,
)
from cil.storage.export import OperationalExporter, TrainingExporter
from cil.storage.memory import (
    InMemoryAuditStore,
    InMemoryEventStore,
    InMemoryScoreStore,
    InMemoryTrainingStore,
)
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


async def test_operational_exporter_emits_one_json_object_per_line() -> None:
    events = InMemoryEventStore()
    scores = InMemoryScoreStore()
    audit = InMemoryAuditStore()
    for s in (events, scores, audit):
        await s.setup()
    for i in range(3):
        ts = BASE + timedelta(seconds=i)
        await events.write_event(
            ContinuityEvent(
                event_id=new_event_id(ts, EventKind.DECISION, str(i)),
                timestamp=ts,
                kind=EventKind.DECISION,
                source=EventSource.SYNTHETIC,
                path_id="modem-a",
            )
        )
    await audit.append(AuditRecord(timestamp=BASE, actor="x", action="y"))

    jsonl = await OperationalExporter(events, scores, audit).events_jsonl()
    lines = jsonl.splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]  # every line is valid JSON
    assert all(rec["kind"] == "decision" for rec in parsed)
    assert (await OperationalExporter(events, scores, audit).audit_jsonl()).count("\n") == 0


async def test_training_window_export_is_self_describing() -> None:
    training = InMemoryTrainingStore()
    await training.setup()
    ts = BASE
    ev_id = new_event_id(ts, EventKind.ENDPOINT_FROZEN, "epic")
    wid = window_id_for(ev_id)
    await training.write_window(
        TelemetryWindow(
            window_id=wid,
            event_id=ev_id,
            center_ts=ts,
            start_ts=ts - timedelta(seconds=1),
            end_ts=ts + timedelta(seconds=1),
            start_us=0,
            end_us=0,
            before_s=1,
            after_s=1,
        )
    )
    await training.write_telemetry_rows(
        wid,
        [
            TelemetrySample(
                timestamp=ts,
                path_id="modem-a",
                carrier="c",
                profile="p",
                radio=RadioMetrics(),
                network=NetworkMetrics(reachable=True),
                device=DeviceMetrics(),
            )
        ],
    )
    await training.write_score_rows(
        wid,
        [
            ScoreSample(
                timestamp=ts, scope="path", subject_id="modem-a", kind=ScoreKind.CCS, value=80.0
            )
        ],
    )

    record = await TrainingExporter(training).export_window(wid)
    assert record is not None
    assert record["window"]["window_id"] == wid  # header
    assert len(record["telemetry"]) == 1 and record["telemetry"][0]["path_id"] == "modem-a"
    assert len(record["scores"]) == 1 and record["scores"][0]["kind"] == "CCS"

    all_jsonl = await TrainingExporter(training).export_all_jsonl()
    assert json.loads(all_jsonl)["window"]["window_id"] == wid  # one line, parseable


async def test_export_missing_window_is_none() -> None:
    training = InMemoryTrainingStore()
    await training.setup()
    assert await TrainingExporter(training).export_window("w_nope") is None
