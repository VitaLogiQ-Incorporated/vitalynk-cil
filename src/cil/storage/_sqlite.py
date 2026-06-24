"""Shared SQLite helpers for the operational + training stores.

Keeps the proven idiom (WAL, busy_timeout, the canonical ``ts_us`` key) in one
place so every store behaves identically. Table names passed here are internal
constants, never user input.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path

from cil.timeutil import to_us


def connect(path: str) -> sqlite3.Connection:
    """Open a reboot-safe SQLite connection (WAL + busy_timeout)."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_ts_us(conn: sqlite3.Connection, table: str, *, ts_col: str = "ts") -> None:
    """Idempotently add the canonical ``ts_us`` column + index to a legacy table,
    backfilling existing rows from their text timestamp."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "ts_us" not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN ts_us INTEGER")
        for rid, ts in conn.execute(f"SELECT id, {ts_col} FROM {table}").fetchall():
            conn.execute(
                f"UPDATE {table} SET ts_us = ? WHERE id = ?",
                (to_us(datetime.fromisoformat(str(ts))), rid),
            )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ts_us ON {table}(ts_us)")


def exclusion_clause(
    exclude_ranges: list[tuple[int, int]] | None,
) -> tuple[str, list[int]]:
    """Build an ``AND NOT (...)`` clause that protects rows inside any pinned range."""
    if not exclude_ranges:
        return "", []
    parts = ["(ts_us BETWEEN ? AND ?)" for _ in exclude_ranges]
    params = [p for rng in exclude_ranges for p in rng]
    return " AND NOT (" + " OR ".join(parts) + ")", params


def checkpoint(conn: sqlite3.Connection) -> None:
    """Reclaim WAL space after a bulk delete (best-effort)."""
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
