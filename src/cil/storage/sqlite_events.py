"""SQLite stores for continuity events + their automated labels (CIL-301/303/902).

Both live in the shared operational DB. The event spine and labels are the join
backbone of the data platform and are **never pruned** — they are tiny next to
telemetry, and dangling references would corrupt the training set.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime

from cil.audit.events import (
    ContinuityEvent,
    DecisionAction,
    EventKind,
    EventLabel,
    EventSource,
    LabeledEvent,
)
from cil.storage._sqlite import connect
from cil.timeutil import to_us

_EVENT_COLUMNS: tuple[str, ...] = (
    "event_id",
    "ts",
    "ts_us",
    "clock_source",
    "kind",
    "source",
    "path_id",
    "carrier",
    "profile",
    "endpoint",
    "system",
    "action",
    "prev_action",
    "cqs",
    "ccs",
    "ccs_tier",
    "reachable",
    "live",
    "sla_breaching",
    "sustained_s",
    "policy_id",
    "rule_id",
    "emitted_action",
    "input_digest",
    "detail",
    "attributes",
    "telemetry_window_id",
)

_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS continuity_events (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id             TEXT    NOT NULL UNIQUE,
    ts                   TEXT    NOT NULL,
    ts_us                INTEGER NOT NULL,
    clock_source         TEXT    NOT NULL,
    kind                 TEXT    NOT NULL,
    source               TEXT    NOT NULL,
    path_id              TEXT,
    carrier              TEXT,
    profile              TEXT,
    endpoint             TEXT,
    system               TEXT,
    action               TEXT,
    prev_action          TEXT,
    cqs                  REAL,
    ccs                  REAL,
    ccs_tier             TEXT,
    reachable            INTEGER,
    live                 INTEGER,
    sla_breaching        INTEGER,
    sustained_s          REAL,
    policy_id            TEXT,
    rule_id              TEXT,
    emitted_action       TEXT,
    input_digest         TEXT,
    detail               TEXT,
    attributes           TEXT,
    telemetry_window_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts_us ON continuity_events(ts_us);
CREATE INDEX IF NOT EXISTS idx_events_kind ON continuity_events(kind);
CREATE INDEX IF NOT EXISTS idx_events_path ON continuity_events(path_id);
"""

_EVENT_INSERT = (
    f"INSERT OR IGNORE INTO continuity_events ({', '.join(_EVENT_COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(_EVENT_COLUMNS))})"
)


def _opt_bool(value: object) -> bool | None:
    return None if value is None else bool(value)


def _event_to_row(e: ContinuityEvent) -> tuple[object, ...]:
    return (
        e.event_id,
        e.timestamp.isoformat(),
        to_us(e.timestamp),
        e.clock_source,
        e.kind.value,
        e.source.value,
        e.path_id,
        e.carrier,
        e.profile,
        e.endpoint,
        e.system,
        e.action.value if e.action is not None else None,
        e.prev_action.value if e.prev_action is not None else None,
        e.cqs,
        e.ccs,
        e.ccs_tier,
        None if e.reachable is None else int(e.reachable),
        None if e.live is None else int(e.live),
        None if e.sla_breaching is None else int(e.sla_breaching),
        e.sustained_s,
        e.policy_id,
        e.rule_id,
        e.emitted_action.value if e.emitted_action is not None else None,
        e.input_digest,
        e.detail,
        json.dumps(e.attributes),
        e.telemetry_window_id,
    )


def _event_from_row(r: sqlite3.Row) -> ContinuityEvent:
    action = r["action"]
    prev_action = r["prev_action"]
    emitted = r["emitted_action"]
    attrs = r["attributes"]
    return ContinuityEvent(
        event_id=str(r["event_id"]),
        timestamp=datetime.fromisoformat(str(r["ts"])),
        clock_source=str(r["clock_source"]),
        kind=EventKind(str(r["kind"])),
        source=EventSource(str(r["source"])),
        path_id=r["path_id"],
        carrier=r["carrier"],
        profile=r["profile"],
        endpoint=r["endpoint"],
        system=r["system"],
        action=DecisionAction(str(action)) if action is not None else None,
        prev_action=DecisionAction(str(prev_action)) if prev_action is not None else None,
        cqs=r["cqs"],
        ccs=r["ccs"],
        ccs_tier=r["ccs_tier"],
        reachable=_opt_bool(r["reachable"]),
        live=_opt_bool(r["live"]),
        sla_breaching=_opt_bool(r["sla_breaching"]),
        sustained_s=r["sustained_s"],
        policy_id=r["policy_id"],
        rule_id=r["rule_id"],
        emitted_action=DecisionAction(str(emitted)) if emitted is not None else None,
        input_digest=r["input_digest"],
        detail=r["detail"],
        attributes=json.loads(attrs) if attrs else {},
        telemetry_window_id=r["telemetry_window_id"],
    )


class SQLiteEventStore:
    """Append-only continuity-event store (set_window is the only mutation)."""

    def __init__(self, path: str = "data/telemetry.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        await asyncio.to_thread(self._connect)

    def _connect(self) -> None:
        conn = connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_EVENTS_SCHEMA)
        conn.commit()
        self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not set up; call await store.setup() first")
        return self._conn

    async def write_event(self, event: ContinuityEvent) -> None:
        row = _event_to_row(event)
        async with self._lock:
            await asyncio.to_thread(self._insert, row)

    def _insert(self, row: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute(_EVENT_INSERT, row)
        conn.commit()

    async def read_events(
        self,
        *,
        kind: EventKind | None = None,
        source: EventSource | None = None,
        path_id: str | None = None,
        endpoint: str | None = None,
        since_us: int | None = None,
        until_us: int | None = None,
        limit: int = 100,
    ) -> list[ContinuityEvent]:
        async with self._lock:
            rows = await asyncio.to_thread(
                self._read_events, kind, source, path_id, endpoint, since_us, until_us, limit
            )
        return [_event_from_row(r) for r in rows]

    def _read_events(
        self,
        kind: EventKind | None,
        source: EventSource | None,
        path_id: str | None,
        endpoint: str | None,
        since_us: int | None,
        until_us: int | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        conn = self._require_conn()
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if source is not None:
            clauses.append("source = ?")
            params.append(source.value)
        if path_id is not None:
            clauses.append("path_id = ?")
            params.append(path_id)
        if endpoint is not None:
            clauses.append("endpoint = ?")
            params.append(endpoint)
        if since_us is not None:
            clauses.append("ts_us >= ?")
            params.append(since_us)
        if until_us is not None:
            clauses.append("ts_us <= ?")
            params.append(until_us)
        sql = "SELECT * FROM continuity_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts_us DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        rows.reverse()  # oldest-first
        return rows

    async def get_event(self, event_id: str) -> ContinuityEvent | None:
        async with self._lock:
            row = await asyncio.to_thread(self._get_event, event_id)
        return _event_from_row(row) if row is not None else None

    def _get_event(self, event_id: str) -> sqlite3.Row | None:
        conn = self._require_conn()
        row: sqlite3.Row | None = conn.execute(
            "SELECT * FROM continuity_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row

    async def set_window(self, event_id: str, window_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_window, event_id, window_id)

    def _set_window(self, event_id: str, window_id: str) -> None:
        conn = self._require_conn()
        conn.execute(
            "UPDATE continuity_events SET telemetry_window_id = ? WHERE event_id = ?",
            (window_id, event_id),
        )
        conn.commit()

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._count)

    def _count(self) -> int:
        conn = self._require_conn()
        return int(conn.execute("SELECT COUNT(*) FROM continuity_events").fetchone()[0])

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)


_LABELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_labels (
    event_id            TEXT    PRIMARY KEY,
    label               TEXT    NOT NULL,
    ts                  TEXT    NOT NULL,
    ts_us               INTEGER NOT NULL,
    telemetry_window_id TEXT,
    rule_id             TEXT,
    label_reason        TEXT
);
CREATE INDEX IF NOT EXISTS idx_labels_label ON event_labels(label);
CREATE INDEX IF NOT EXISTS idx_labels_ts_us ON event_labels(ts_us);
"""

_LABEL_INSERT = """
INSERT OR REPLACE INTO event_labels
    (event_id, label, ts, ts_us, telemetry_window_id, rule_id, label_reason)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def _label_from_row(r: sqlite3.Row) -> LabeledEvent:
    return LabeledEvent(
        event_id=str(r["event_id"]),
        label=EventLabel(str(r["label"])),
        timestamp=__import__("datetime").datetime.fromisoformat(str(r["ts"])),
        telemetry_window_id=r["telemetry_window_id"],
        rule_id=r["rule_id"],
        label_reason=r["label_reason"],
    )


class SQLiteLabelStore:
    """Upsert-by-event_id label store; never pruned."""

    def __init__(self, path: str = "data/telemetry.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        await asyncio.to_thread(self._connect)

    def _connect(self) -> None:
        conn = connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_LABELS_SCHEMA)
        conn.commit()
        self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not set up; call await store.setup() first")
        return self._conn

    async def write_label(self, label: LabeledEvent) -> None:
        row = (
            label.event_id,
            label.label.value,
            label.timestamp.isoformat(),
            to_us(label.timestamp),
            label.telemetry_window_id,
            label.rule_id,
            label.label_reason,
        )
        async with self._lock:
            await asyncio.to_thread(self._insert, row)

    def _insert(self, row: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute(_LABEL_INSERT, row)
        conn.commit()

    async def get_label(self, event_id: str) -> LabeledEvent | None:
        async with self._lock:
            row = await asyncio.to_thread(
                lambda: (
                    self._require_conn()
                    .execute("SELECT * FROM event_labels WHERE event_id = ?", (event_id,))
                    .fetchone()
                )
            )
        return _label_from_row(row) if row is not None else None

    async def read_labels(
        self, *, label: str | None = None, limit: int = 100
    ) -> list[LabeledEvent]:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_labels, label, limit)
        return [_label_from_row(r) for r in rows]

    def _read_labels(self, label: str | None, limit: int) -> list[sqlite3.Row]:
        conn = self._require_conn()
        sql = "SELECT * FROM event_labels"
        params: list[object] = []
        if label is not None:
            sql += " WHERE label = ?"
            params.append(label)
        sql += " ORDER BY ts_us DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        rows.reverse()
        return rows

    async def set_window(self, event_id: str, window_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._set_window, event_id, window_id)

    def _set_window(self, event_id: str, window_id: str) -> None:
        conn = self._require_conn()
        conn.execute(
            "UPDATE event_labels SET telemetry_window_id = ? WHERE event_id = ?",
            (window_id, event_id),
        )
        conn.commit()

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                lambda: int(
                    self._require_conn().execute("SELECT COUNT(*) FROM event_labels").fetchone()[0]
                )
            )

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
