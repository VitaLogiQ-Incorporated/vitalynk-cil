"""Window-capture integrity suite (CIL-302) — the un-retrofittable guarantees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cil.audit.events import ContinuityEvent, EventKind, EventSource, new_event_id, window_id_for
from cil.audit.window_capture import WindowCaptureService
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.storage.sqlite_app import SQLiteApplicationHealthStore
from cil.storage.sqlite_scores import SQLiteScoreStore
from cil.storage.sqlite_training import SQLiteTrainingStore
from cil.telemetry.schema import DeviceMetrics, NetworkMetrics, RadioMetrics, TelemetrySample
from cil.timeutil import to_us

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def sample_at(second: int) -> TelemetrySample:
    return TelemetrySample(
        timestamp=BASE + timedelta(seconds=second),
        path_id="modem-a",
        carrier="c",
        profile="p",
        radio=RadioMetrics(),
        network=NetworkMetrics(reachable=True),
        device=DeviceMetrics(),
    )


def event_at(second: int) -> ContinuityEvent:
    ts = BASE + timedelta(seconds=second)
    return ContinuityEvent(
        event_id=new_event_id(ts, EventKind.ENDPOINT_FROZEN, str(second)),
        timestamp=ts,
        kind=EventKind.ENDPOINT_FROZEN,
        source=EventSource.APP_MONITOR,
        path_id="modem-a",
        endpoint="epic-ehr",
    )


async def _stores() -> tuple:
    tel = SQLiteTelemetryStore(":memory:")
    app = SQLiteApplicationHealthStore(":memory:")
    sc = SQLiteScoreStore(":memory:")
    tr = SQLiteTrainingStore(":memory:")
    for s in (tel, app, sc, tr):
        await s.setup()
    return tel, app, sc, tr


def _svc(tel, app, sc, tr) -> WindowCaptureService:
    # small radius + no floor/warn so windows are tiny and test-friendly
    return WindowCaptureService(
        tel,
        app,
        sc,
        tr,
        before_s=5,
        after_s=5,
        min_radius_s=0,
        warn_below_s=0,
        sample_interval_s=1.0,
    )


async def test_capture_header_edges() -> None:
    tel, app, sc, tr = await _stores()
    svc = _svc(tel, app, sc, tr)
    win = await svc.capture(event_at(10))
    assert win.start_us == to_us(BASE + timedelta(seconds=5))
    assert win.end_us == to_us(BASE + timedelta(seconds=15))
    assert win.finalized_at is None  # phase 1 only


async def test_finalize_native_copy_no_downsampling() -> None:
    tel, app, sc, tr = await _stores()
    # insert OUT of order to prove ts-ordering, not insertion-ordering
    for i in (3, 12, 0, 10, 7, 15, 5):
        await tel.write_sample(sample_at(i))
    for i in (20, 1, 9, 11, 14):
        await tel.write_sample(sample_at(i))

    svc = _svc(tel, app, sc, tr)
    event = event_at(10)
    await svc.capture(event)
    win = await svc.finalize_window(
        await tr.get_window(window_id_for(event.event_id)), now=BASE + timedelta(seconds=15)
    )
    wid = window_id_for(event.event_id)

    source = await tel.read_range(start_us=win.start_us, end_us=win.end_us)
    captured = await tr.read_window_rows(wid)
    assert captured == source  # byte-identical, ts-ascending, no downsampling
    assert [s.timestamp.second for s in captured] == [5, 7, 9, 10, 11, 12, 14, 15]
    assert len(captured) == len(source)  # no downsampling: count matches source in-range
    assert win.finalized_at is not None  # two-phase finalize stamped


async def test_finalize_is_idempotent() -> None:
    tel, app, sc, tr = await _stores()
    for i in range(20):
        await tel.write_sample(sample_at(i))
    svc = _svc(tel, app, sc, tr)
    event = event_at(10)
    await svc.capture(event)
    wid = window_id_for(event.event_id)
    w1 = await svc.finalize_window(await tr.get_window(wid), now=BASE + timedelta(seconds=15))
    n1 = len(await tr.read_window_rows(wid))
    # second finalize must NOT duplicate rows (training store has no delete)
    await svc.finalize_window(w1, now=BASE + timedelta(seconds=15))
    assert len(await tr.read_window_rows(wid)) == n1


async def test_overlapping_windows_byte_identical_on_intersection() -> None:
    tel, app, sc, tr = await _stores()
    for i in range(25):
        await tel.write_sample(sample_at(i))
    svc = _svc(tel, app, sc, tr)
    a, b = event_at(10), event_at(12)  # windows [5,15] and [7,17] overlap on [7,15]
    for e in (a, b):
        await svc.capture(e)
        await svc.finalize_window(
            await tr.get_window(window_id_for(e.event_id)), now=BASE + timedelta(seconds=30)
        )
    rows_a = {
        s.timestamp.second: s
        for s in await tr.read_window_rows(window_id_for(a.event_id))
        if 7 <= s.timestamp.second <= 15
    }
    rows_b = {
        s.timestamp.second: s
        for s in await tr.read_window_rows(window_id_for(b.event_id))
        if 7 <= s.timestamp.second <= 15
    }
    assert rows_a == rows_b  # intersection identical across overlapping windows


async def test_short_window_is_flagged_not_silently_complete() -> None:
    tel, app, sc, tr = await _stores()
    # pre side fully present; post side missing most rows
    for i in range(0, 12):  # only up to second 11 (window post is [10,15])
        await tel.write_sample(sample_at(i))
    svc = _svc(tel, app, sc, tr)
    event = event_at(10)
    await svc.capture(event)
    win = await svc.finalize_window(
        await tr.get_window(window_id_for(event.event_id)), now=BASE + timedelta(seconds=15)
    )
    assert win.complete_post is False  # genuinely short, not silently "complete"
    assert win.resolution_note is not None and "short window" in win.resolution_note


async def test_finalize_due_finalizes_elapsed_windows() -> None:
    tel, app, sc, tr = await _stores()
    for i in range(20):
        await tel.write_sample(sample_at(i))
    svc = _svc(tel, app, sc, tr)
    await svc.capture(event_at(10))
    assert len(await tr.list_unfinalized()) == 1
    finalized = await svc.finalize_due(now=BASE + timedelta(seconds=100))
    assert finalized == 1
    assert await tr.list_unfinalized() == []
