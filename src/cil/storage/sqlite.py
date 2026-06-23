"""SQLite telemetry store — the UC1 operational store (CIL-301).

Embedded, zero-ops, reboot-safe (file-backed, WAL) — the right fit for the
resource-constrained E400. Captures telemetry at **native resolution, no
downsampling** (the mandatory EPIC-03 capture rule). The richer event/window and
labeling schema (CIL-302/303) lands in Sprint 2; this is the telemetry table that
makes the Sprint-1 end-to-end loop real.

Implementation note: the stdlib ``sqlite3`` driver is synchronous, so calls run
in a worker thread via ``asyncio.to_thread`` and are serialized with a lock — the
async interface never blocks the event loop, and no extra dependency is needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from cil.telemetry.schema import (
    DeviceMetrics,
    NetworkMetrics,
    RadioMetrics,
    TelemetrySample,
)

_COLUMNS: tuple[str, ...] = (
    "ts",
    "path_id",
    "carrier",
    "profile",
    "rssi",
    "rsrp",
    "rsrq",
    "sinr",
    "latency_ms",
    "packet_loss_pct",
    "jitter_ms",
    "throughput_mbps",
    "dns_response_ms",
    "reachable",
    "cpu_pct",
    "mem_pct",
    "uptime_s",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    path_id         TEXT    NOT NULL,
    carrier         TEXT    NOT NULL,
    profile         TEXT    NOT NULL,
    rssi            REAL,
    rsrp            REAL,
    rsrq            REAL,
    sinr            REAL,
    latency_ms      REAL,
    packet_loss_pct REAL,
    jitter_ms       REAL,
    throughput_mbps REAL,
    dns_response_ms REAL,
    reachable       INTEGER NOT NULL,
    cpu_pct         REAL,
    mem_pct         REAL,
    uptime_s        REAL
);
CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(ts);
CREATE INDEX IF NOT EXISTS idx_telemetry_path_ts ON telemetry(path_id, ts);
"""

_INSERT = (
    f"INSERT INTO telemetry ({', '.join(_COLUMNS)}) VALUES ({', '.join(['?'] * len(_COLUMNS))})"
)


class SQLiteTelemetryStore:
    """Durable, native-resolution telemetry store. Implements ``TelemetryStore``."""

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
    def _to_row(s: TelemetrySample) -> tuple[object, ...]:
        return (
            s.timestamp.isoformat(),
            s.path_id,
            s.carrier,
            s.profile,
            s.radio.rssi,
            s.radio.rsrp,
            s.radio.rsrq,
            s.radio.sinr,
            s.network.latency_ms,
            s.network.packet_loss_pct,
            s.network.jitter_ms,
            s.network.throughput_mbps,
            s.network.dns_response_ms,
            int(s.network.reachable),
            s.device.cpu_pct,
            s.device.mem_pct,
            s.device.uptime_s,
        )

    @staticmethod
    def _from_row(r: tuple[object, ...]) -> TelemetrySample:
        return TelemetrySample(
            timestamp=datetime.fromisoformat(str(r[0])),
            path_id=str(r[1]),
            carrier=str(r[2]),
            profile=str(r[3]),
            radio=RadioMetrics(rssi=r[4], rsrp=r[5], rsrq=r[6], sinr=r[7]),  # type: ignore[arg-type]
            network=NetworkMetrics(
                latency_ms=r[8],  # type: ignore[arg-type]
                packet_loss_pct=r[9],  # type: ignore[arg-type]
                jitter_ms=r[10],  # type: ignore[arg-type]
                throughput_mbps=r[11],  # type: ignore[arg-type]
                dns_response_ms=r[12],  # type: ignore[arg-type]
                reachable=bool(r[13]),
            ),
            device=DeviceMetrics(cpu_pct=r[14], mem_pct=r[15], uptime_s=r[16]),  # type: ignore[arg-type]
        )

    async def write_sample(self, sample: TelemetrySample) -> None:
        await self.write_samples((sample,))

    async def write_samples(self, samples: Iterable[TelemetrySample]) -> int:
        rows = [self._to_row(s) for s in samples]
        async with self._lock:
            await asyncio.to_thread(self._insert, rows)
        return len(rows)

    def _insert(self, rows: list[tuple[object, ...]]) -> None:
        conn = self._require_conn()
        conn.executemany(_INSERT, rows)
        conn.commit()

    async def read_samples(
        self, *, path_id: str | None = None, limit: int = 100
    ) -> list[TelemetrySample]:
        async with self._lock:
            rows = await asyncio.to_thread(self._select, path_id, limit)
        return [self._from_row(r) for r in rows]

    def _select(self, path_id: str | None, limit: int) -> list[tuple[object, ...]]:
        conn = self._require_conn()
        sql = f"SELECT {', '.join(_COLUMNS)} FROM telemetry"
        params: list[object] = []
        if path_id is not None:
            sql += " WHERE path_id = ?"
            params.append(path_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        rows.reverse()  # oldest-first
        return rows

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._count)

    def _count(self) -> int:
        conn = self._require_conn()
        row = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()
        return int(row[0])

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
