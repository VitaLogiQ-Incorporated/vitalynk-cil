"""SQLite audit log (CIL-301/902) — truly append-only; never deleted.

Records every label decision and retention sweep so the system is auditable
(HIPAA-adjacent). There is no update or delete path by design.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime

from cil.audit.events import AuditRecord
from cil.storage._sqlite import connect
from cil.timeutil import to_us

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    ts_us     INTEGER NOT NULL,
    actor     TEXT    NOT NULL,
    action    TEXT    NOT NULL,
    event_id  TEXT,
    outcome   TEXT,
    detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts_us ON audit_log(ts_us);
"""

_INSERT = """
INSERT INTO audit_log (ts, ts_us, actor, action, event_id, outcome, detail)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def _from_row(r: sqlite3.Row) -> AuditRecord:
    return AuditRecord(
        timestamp=datetime.fromisoformat(str(r["ts"])),
        actor=str(r["actor"]),
        action=str(r["action"]),
        event_id=r["event_id"],
        outcome=r["outcome"],
        detail=r["detail"],
    )


class SQLiteAuditStore:
    """Append-only audit store. Implements ``AuditStore``."""

    def __init__(self, path: str = "data/telemetry.db") -> None:
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

    async def append(self, record: AuditRecord) -> None:
        row = (
            record.timestamp.isoformat(),
            to_us(record.timestamp),
            record.actor,
            record.action,
            record.event_id,
            record.outcome,
            record.detail,
        )
        async with self._lock:
            await asyncio.to_thread(self._insert, row)

    def _insert(self, row: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute(_INSERT, row)
        conn.commit()

    async def read(self, *, limit: int = 100) -> list[AuditRecord]:
        async with self._lock:
            rows = await asyncio.to_thread(self._read, limit)
        return [_from_row(r) for r in rows]

    def _read(self, limit: int) -> list[sqlite3.Row]:
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY ts_us DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        rows.reverse()
        return rows

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                lambda: int(
                    self._require_conn().execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
                )
            )

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
