"""Storage: a storage interface with a SQLite implementation for UC1.

Holds the operational data store (CIL-301) and the training dataset repository
(CIL-302). Callers depend only on the interface so the backing store can be
swapped (e.g. a time-series DB) without touching them. Capture is
native-resolution with no downsampling (see CLAUDE.md §6).
"""

from cil.storage.interface import ApplicationHealthStore, TelemetryStore
from cil.storage.memory import InMemoryApplicationHealthStore, InMemoryTelemetryStore
from cil.storage.sqlite import SQLiteTelemetryStore
from cil.storage.sqlite_app import SQLiteApplicationHealthStore

__all__ = [
    "ApplicationHealthStore",
    "InMemoryApplicationHealthStore",
    "InMemoryTelemetryStore",
    "SQLiteApplicationHealthStore",
    "SQLiteTelemetryStore",
    "TelemetryStore",
]
