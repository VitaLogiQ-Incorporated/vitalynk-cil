"""SQLite training repository (CIL-302) — the indefinite UC2 dataset.

Lives in its OWN database file (``training_db_path``) that the retention sweeper
is never pointed at, and exposes NO delete path — so a sweep bug or a future
operational-DB swap can never destroy the un-recreatable dataset.

Telemetry/app-health rows are stored verbatim by reusing the operational stores'
exact ``_COLUMNS`` + ``_to_row``/``_from_row`` (schema-drift guard), so a captured
window round-trips byte-for-byte with the source.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from cil.audit.events import ContinuityEvent, LabeledEvent, ScoreSample, TelemetryWindow
from cil.storage._sqlite import connect
from cil.storage.sqlite import _COLUMNS as _TEL_COLS
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.storage.sqlite_app import _COLUMNS as _APP_COLS
from cil.storage.sqlite_app import SQLiteApplicationHealthStore
from cil.storage.sqlite_scores import _from_row as _score_from_row
from cil.telemetry.probes import EndpointHealth
from cil.telemetry.schema import TelemetrySample
from cil.timeutil import to_us

_TEL_COLS_SQL = ", ".join(_TEL_COLS)
_APP_COLS_SQL = ", ".join(_APP_COLS)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS training_windows (
    window_id      TEXT PRIMARY KEY,
    event_id       TEXT NOT NULL,
    center_ts      TEXT NOT NULL,
    start_ts       TEXT NOT NULL,
    end_ts         TEXT NOT NULL,
    start_us       INTEGER NOT NULL,
    end_us         INTEGER NOT NULL,
    before_s       REAL NOT NULL,
    after_s        REAL NOT NULL,
    sample_count   INTEGER NOT NULL,
    app_health_count INTEGER NOT NULL,
    score_count    INTEGER NOT NULL,
    expected_pre   INTEGER NOT NULL,
    actual_pre     INTEGER NOT NULL,
    expected_post  INTEGER NOT NULL,
    actual_post    INTEGER NOT NULL,
    complete_pre   INTEGER NOT NULL,
    complete_post  INTEGER NOT NULL,
    clock_source   TEXT NOT NULL,
    captured_at    TEXT,
    finalized_at   TEXT,
    archived_at    TEXT,
    resolution_note TEXT
);
CREATE TABLE IF NOT EXISTS training_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id TEXT NOT NULL,
    ts_us INTEGER NOT NULL,
    {_TEL_COLS_SQL}
);
CREATE INDEX IF NOT EXISTS idx_train_tel ON training_telemetry(window_id, ts_us);
CREATE TABLE IF NOT EXISTS training_app_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id TEXT NOT NULL,
    ts_us INTEGER NOT NULL,
    {_APP_COLS_SQL}
);
CREATE INDEX IF NOT EXISTS idx_train_app ON training_app_health(window_id, ts_us);
CREATE TABLE IF NOT EXISTS training_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id TEXT NOT NULL,
    ts_us INTEGER NOT NULL,
    ts TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    value REAL NOT NULL,
    tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_train_score ON training_scores(window_id, ts_us);
CREATE TABLE IF NOT EXISTS continuity_event_record (
    event_id     TEXT PRIMARY KEY,
    event_json   TEXT NOT NULL,
    label        TEXT,
    rule_id      TEXT,
    label_reason TEXT
);
"""

_TEL_INSERT = (
    f"INSERT INTO training_telemetry (window_id, ts_us, {_TEL_COLS_SQL}) "
    f"VALUES (?, ?, {', '.join(['?'] * len(_TEL_COLS))})"
)
_APP_INSERT = (
    f"INSERT INTO training_app_health (window_id, ts_us, {_APP_COLS_SQL}) "
    f"VALUES (?, ?, {', '.join(['?'] * len(_APP_COLS))})"
)
_SCORE_INSERT = (
    "INSERT INTO training_scores (window_id, ts_us, ts, scope, subject_id, kind, value, tier) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_WINDOW_INSERT = """
INSERT OR REPLACE INTO training_windows (
    window_id, event_id, center_ts, start_ts, end_ts, start_us, end_us, before_s, after_s,
    sample_count, app_health_count, score_count, expected_pre, actual_pre, expected_post,
    actual_post, complete_pre, complete_post, clock_source, captured_at, finalized_at,
    archived_at, resolution_note
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _iso_opt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_opt(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value is not None else None


def _window_to_row(w: TelemetryWindow) -> tuple[object, ...]:
    return (
        w.window_id,
        w.event_id,
        w.center_ts.isoformat(),
        w.start_ts.isoformat(),
        w.end_ts.isoformat(),
        w.start_us,
        w.end_us,
        w.before_s,
        w.after_s,
        w.sample_count,
        w.app_health_count,
        w.score_count,
        w.expected_pre,
        w.actual_pre,
        w.expected_post,
        w.actual_post,
        int(w.complete_pre),
        int(w.complete_post),
        w.clock_source,
        _iso_opt(w.captured_at),
        _iso_opt(w.finalized_at),
        _iso_opt(w.archived_at),
        w.resolution_note,
    )


def _window_from_row(r: sqlite3.Row) -> TelemetryWindow:
    return TelemetryWindow(
        window_id=str(r["window_id"]),
        event_id=str(r["event_id"]),
        center_ts=datetime.fromisoformat(str(r["center_ts"])),
        start_ts=datetime.fromisoformat(str(r["start_ts"])),
        end_ts=datetime.fromisoformat(str(r["end_ts"])),
        start_us=int(r["start_us"]),
        end_us=int(r["end_us"]),
        before_s=float(r["before_s"]),
        after_s=float(r["after_s"]),
        sample_count=int(r["sample_count"]),
        app_health_count=int(r["app_health_count"]),
        score_count=int(r["score_count"]),
        expected_pre=int(r["expected_pre"]),
        actual_pre=int(r["actual_pre"]),
        expected_post=int(r["expected_post"]),
        actual_post=int(r["actual_post"]),
        complete_pre=bool(r["complete_pre"]),
        complete_post=bool(r["complete_post"]),
        clock_source=str(r["clock_source"]),
        captured_at=_dt_opt(r["captured_at"]),
        finalized_at=_dt_opt(r["finalized_at"]),
        archived_at=_dt_opt(r["archived_at"]),
        resolution_note=r["resolution_note"],
    )


class SQLiteTrainingStore:
    """The indefinite training repository. Implements ``TrainingStore`` (no purge)."""

    def __init__(self, path: str = "data/training.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        await asyncio.to_thread(self._connect)

    def _connect(self) -> None:
        conn = connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not set up; call await store.setup() first")
        return self._conn

    async def write_window(self, window: TelemetryWindow) -> None:
        row = _window_to_row(window)
        async with self._lock:
            await asyncio.to_thread(self._exec_commit, _WINDOW_INSERT, row)

    def _exec_commit(self, sql: str, row: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute(sql, row)
        conn.commit()

    async def get_window(self, window_id: str) -> TelemetryWindow | None:
        async with self._lock:
            row = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute("SELECT * FROM training_windows WHERE window_id = ?", (window_id,))
                    .fetchone()
                )
            )
        return _window_from_row(row) if row is not None else None

    async def write_telemetry_rows(self, window_id: str, samples: Iterable[TelemetrySample]) -> int:
        rows: list[tuple[object, ...]] = [
            (window_id, to_us(s.timestamp), *SQLiteTelemetryStore._to_row(s)) for s in samples
        ]
        async with self._lock:
            await asyncio.to_thread(self._executemany, _TEL_INSERT, rows)
        return len(rows)

    async def write_health_rows(self, window_id: str, rows_in: Iterable[EndpointHealth]) -> int:
        rows: list[tuple[object, ...]] = [
            (window_id, to_us(h.timestamp), *SQLiteApplicationHealthStore._to_row(h))
            for h in rows_in
        ]
        async with self._lock:
            await asyncio.to_thread(self._executemany, _APP_INSERT, rows)
        return len(rows)

    async def write_score_rows(self, window_id: str, rows_in: Iterable[ScoreSample]) -> int:
        rows: list[tuple[object, ...]] = [
            (
                window_id,
                to_us(s.timestamp),
                s.timestamp.isoformat(),
                s.scope,
                s.subject_id,
                s.kind.value,
                s.value,
                s.tier,
            )
            for s in rows_in
        ]
        async with self._lock:
            await asyncio.to_thread(self._executemany, _SCORE_INSERT, rows)
        return len(rows)

    def _executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
        conn = self._require_conn()
        conn.executemany(sql, rows)
        conn.commit()

    async def write_event_record(self, event: ContinuityEvent, label: LabeledEvent | None) -> None:
        row = (
            event.event_id,
            event.model_dump_json(),
            label.label.value if label is not None else None,
            label.rule_id if label is not None else None,
            label.label_reason if label is not None else None,
        )
        async with self._lock:
            await asyncio.to_thread(
                self._exec_commit,
                "INSERT OR REPLACE INTO continuity_event_record "
                "(event_id, event_json, label, rule_id, label_reason) VALUES (?, ?, ?, ?, ?)",
                row,
            )

    async def read_window_rows(self, window_id: str) -> list[TelemetrySample]:
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute(
                        f"SELECT {_TEL_COLS_SQL} FROM training_telemetry "
                        "WHERE window_id = ? ORDER BY ts_us ASC, id ASC",
                        (window_id,),
                    )
                    .fetchall()
                )
            )
        return [SQLiteTelemetryStore._from_row(tuple(r)) for r in rows]

    async def read_window_health(self, window_id: str) -> list[EndpointHealth]:
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute(
                        f"SELECT {_APP_COLS_SQL} FROM training_app_health "
                        "WHERE window_id = ? ORDER BY ts_us ASC, id ASC",
                        (window_id,),
                    )
                    .fetchall()
                )
            )
        return [SQLiteApplicationHealthStore._from_row(tuple(r)) for r in rows]

    async def read_window_scores(self, window_id: str) -> list[ScoreSample]:
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute(
                        "SELECT * FROM training_scores WHERE window_id = ? "
                        "ORDER BY ts_us ASC, id ASC",
                        (window_id,),
                    )
                    .fetchall()
                )
            )
        return [_score_from_row(r) for r in rows]

    async def list_windows(self, *, limit: int = 100) -> list[TelemetryWindow]:
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute(
                        "SELECT * FROM training_windows ORDER BY start_us DESC LIMIT ?", (limit,)
                    )
                    .fetchall()
                )
            )
        return [_window_from_row(r) for r in rows]

    async def list_unfinalized(self) -> list[TelemetryWindow]:
        async with self._lock:
            rows = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute("SELECT * FROM training_windows WHERE complete_post = 0")
                    .fetchall()
                )
            )
        return [_window_from_row(r) for r in rows]

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                lambda: int(
                    self._require_conn()
                    .execute("SELECT COUNT(*) FROM training_windows")
                    .fetchone()[0]
                )
            )

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
