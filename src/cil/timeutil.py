"""Canonical time handling for the CIL (EPIC-03 critique #1).

All persisted timestamps are timezone-aware UTC, and **all** range/order math is
done on integer epoch-microseconds (``ts_us``) — never on lexical text. Naive
datetimes are rejected at the boundary so a missing tzinfo can never silently
corrupt a window's range query.
"""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as tz-aware UTC; raise on a naive datetime."""
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware (UTC); got a naive datetime")
    return value.astimezone(UTC)


def ensure_utc_opt(value: datetime | None) -> datetime | None:
    """``ensure_utc`` that passes ``None`` through (for optional fields)."""
    return None if value is None else ensure_utc(value)


def coerce_utc(value: datetime) -> tuple[datetime, bool]:
    """Best-effort UTC for *raw external* timestamps: convert aware datetimes to UTC
    (via ``astimezone``) and assume naive datetimes are UTC. Returns ``(utc, was_naive)``
    so the caller can log when it had to assume — unlike :func:`ensure_utc`, which raises.
    Internal/persisted timestamps must still go through ``ensure_utc``."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC), True
    return value.astimezone(UTC), False


def to_us(value: datetime) -> int:
    """Epoch microseconds (UTC) — the canonical sort/range key."""
    return int(ensure_utc(value).timestamp() * 1_000_000)


def from_us(us: int) -> datetime:
    """Inverse of :func:`to_us` — a tz-aware UTC datetime."""
    return datetime.fromtimestamp(us / 1_000_000, tz=UTC)
