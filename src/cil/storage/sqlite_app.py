"""SQLite store for clinical endpoint health (CIL-203).

Sibling of the telemetry store, persisting ``EndpointHealth`` into an
``application_health`` table in the same operational database. Same approach:
synchronous ``sqlite3`` driven via ``asyncio.to_thread`` + a lock, so the async
interface never blocks the event loop. (Sprint 2 consolidates the operational
tables behind one store.)
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from cil.telemetry.probes import EndpointHealth, ProbeDepth

_COLUMNS: tuple[str, ...] = (
    "ts",
    "endpoint",
    "system",
    "reachable",
    "live",
    "healthy",
    "depth_achieved",
    "required_depth",
    "latency_ms",
    "detail",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_health (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT    NOT NULL,
    endpoint       TEXT    NOT NULL,
    system         TEXT    NOT NULL,
    reachable      INTEGER NOT NULL,
    live           INTEGER NOT NULL,
    healthy        INTEGER NOT NULL,
    depth_achieved TEXT,
    required_depth TEXT    NOT NULL,
    latency_ms     REAL,
    detail         TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_health_ts ON application_health(ts);
CREATE INDEX IF NOT EXISTS idx_app_health_ep_ts ON application_health(endpoint, ts);
"""

_INSERT = (
    f"INSERT INTO application_health ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join(['?'] * len(_COLUMNS))})"
)


class SQLiteApplicationHealthStore:
    """Durable endpoint-health store. Implements ``ApplicationHealthStore``."""

    def __init__(self, path: str = "data/telemetry.db") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def setup(self) -> None:
        await asyncio.to_thread(self._connect)

    def _connect(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not set up; call await store.setup() first")
        return self._conn

    @staticmethod
    def _to_row(h: EndpointHealth) -> tuple[object, ...]:
        return (
            h.timestamp.isoformat(),
            h.endpoint,
            h.system,
            int(h.reachable),
            int(h.live),
            int(h.healthy),
            h.depth_achieved.value if h.depth_achieved is not None else None,
            h.required_depth.value,
            h.latency_ms,
            h.detail,
        )

    @staticmethod
    def _from_row(r: tuple[object, ...]) -> EndpointHealth:
        depth = ProbeDepth(str(r[6])) if r[6] is not None else None
        return EndpointHealth(
            timestamp=datetime.fromisoformat(str(r[0])),
            endpoint=str(r[1]),
            system=str(r[2]),
            reachable=bool(r[3]),
            live=bool(r[4]),
            healthy=bool(r[5]),
            depth_achieved=depth,
            required_depth=ProbeDepth(str(r[7])),
            latency_ms=r[8],  # type: ignore[arg-type]
            detail=r[9],  # type: ignore[arg-type]
        )

    async def write_health(self, health: EndpointHealth) -> None:
        row = self._to_row(health)
        async with self._lock:
            await asyncio.to_thread(self._insert, row)

    def _insert(self, row: tuple[object, ...]) -> None:
        conn = self._require_conn()
        conn.execute(_INSERT, row)
        conn.commit()

    async def read_health(
        self, *, endpoint: str | None = None, limit: int = 100
    ) -> list[EndpointHealth]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select, endpoint, limit)
        return [self._from_row(r) for r in rows]

    def _select(self, endpoint: str | None, limit: int) -> list[tuple[object, ...]]:
        conn = self._require_conn()
        sql = f"SELECT {', '.join(_COLUMNS)} FROM application_health"
        params: list[object] = []
        if endpoint is not None:
            sql += " WHERE endpoint = ?"
            params.append(endpoint)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        rows.reverse()
        return rows

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._count)

    def _count(self) -> int:
        conn = self._require_conn()
        row = conn.execute("SELECT COUNT(*) FROM application_health").fetchone()
        return int(row[0])

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
