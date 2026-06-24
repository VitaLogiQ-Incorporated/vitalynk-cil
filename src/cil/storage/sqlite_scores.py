"""SQLite store for CQS/CCS score samples (CIL-301).

Native-cadence score timeline; feeds the SLA labeler and is pruned at the
operational horizon (24 months). Producers (the scoring engine) land in Sprint 3;
until then the synthetic source / tests populate it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable
from datetime import datetime

from cil.audit.events import ScoreKind, ScoreSample
from cil.storage._sqlite import checkpoint, connect, exclusion_clause
from cil.timeutil import to_us

_SCHEMA = """
CREATE TABLE IF NOT EXISTS score_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    ts_us      INTEGER NOT NULL,
    scope      TEXT    NOT NULL,
    subject_id TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    value      REAL    NOT NULL,
    tier       TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_ts_us ON score_samples(ts_us);
CREATE INDEX IF NOT EXISTS idx_scores_subject ON score_samples(subject_id, kind, ts_us);
"""

_INSERT = """
INSERT INTO score_samples (ts, ts_us, scope, subject_id, kind, value, tier)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def _to_row(s: ScoreSample) -> tuple[object, ...]:
    return (
        s.timestamp.isoformat(),
        to_us(s.timestamp),
        s.scope,
        s.subject_id,
        s.kind.value,
        s.value,
        s.tier,
    )


def _from_row(r: sqlite3.Row) -> ScoreSample:
    return ScoreSample(
        timestamp=datetime.fromisoformat(str(r["ts"])),
        scope=str(r["scope"]),
        subject_id=str(r["subject_id"]),
        kind=ScoreKind(str(r["kind"])),
        value=float(r["value"]),
        tier=r["tier"],
    )


class SQLiteScoreStore:
    """Score-sample store. Implements ``ScoreStore``."""

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

    async def write_score(self, score: ScoreSample) -> None:
        await self.write_scores((score,))

    async def write_scores(self, scores: Iterable[ScoreSample]) -> int:
        rows = [_to_row(s) for s in scores]
        async with self._lock:
            await asyncio.to_thread(self._insert, rows)
        return len(rows)

    def _insert(self, rows: list[tuple[object, ...]]) -> None:
        conn = self._require_conn()
        conn.executemany(_INSERT, rows)
        conn.commit()

    async def read_scores(
        self, *, subject_id: str | None = None, kind: ScoreKind | None = None, limit: int = 100
    ) -> list[ScoreSample]:
        async with self._lock:
            rows = await asyncio.to_thread(self._read_scores, subject_id, kind, limit)
        return [_from_row(r) for r in rows]

    def _read_scores(
        self, subject_id: str | None, kind: ScoreKind | None, limit: int
    ) -> list[sqlite3.Row]:
        conn = self._require_conn()
        clauses: list[str] = []
        params: list[object] = []
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        sql = "SELECT * FROM score_samples"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts_us DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        rows.reverse()
        return rows

    async def read_score_range(
        self,
        *,
        start_us: int,
        end_us: int,
        subject_id: str | None = None,
        kind: ScoreKind | None = None,
    ) -> list[ScoreSample]:
        async with self._lock:
            rows = await asyncio.to_thread(
                self._read_score_range, start_us, end_us, subject_id, kind
            )
        return [_from_row(r) for r in rows]

    def _read_score_range(
        self, start_us: int, end_us: int, subject_id: str | None, kind: ScoreKind | None
    ) -> list[sqlite3.Row]:
        conn = self._require_conn()
        clauses = ["ts_us BETWEEN ? AND ?"]
        params: list[object] = [start_us, end_us]
        if subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(subject_id)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        sql = "SELECT * FROM score_samples WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts_us ASC, id ASC"  # UNBOUNDED
        return conn.execute(sql, params).fetchall()

    async def delete_older_than(
        self, cutoff_us: int, *, exclude_ranges: list[tuple[int, int]] | None = None
    ) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._delete_older_than, cutoff_us, exclude_ranges)

    def _delete_older_than(
        self, cutoff_us: int, exclude_ranges: list[tuple[int, int]] | None
    ) -> int:
        conn = self._require_conn()
        clause, ex_params = exclusion_clause(exclude_ranges)
        cur = conn.execute(
            f"DELETE FROM score_samples WHERE ts_us < ?{clause}", [cutoff_us, *ex_params]
        )
        deleted = cur.rowcount
        conn.commit()
        checkpoint(conn)
        return int(deleted)

    async def count(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(
                lambda: int(
                    self._require_conn().execute("SELECT COUNT(*) FROM score_samples").fetchone()[0]
                )
            )

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                conn = self._conn
                self._conn = None
                await asyncio.to_thread(conn.close)
