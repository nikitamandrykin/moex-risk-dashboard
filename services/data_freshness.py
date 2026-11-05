"""Small, side-effect-free rules for keeping session snapshots current."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
SNAPSHOT_TTL_SECONDS = 300
AUTO_REFRESH_POLL_SECONDS = 60


def snapshot_expired(
    fetched_at: float | None,
    now: float,
    *,
    ttl_seconds: int = SNAPSHOT_TTL_SECONDS,
) -> bool:
    """A browser snapshot must not outlive the shared source cache TTL.

    A backwards clock adjustment or an unknown timestamp invalidates it,
    rather than allowing an indefinitely old snapshot to appear fresh.
    """
    if fetched_at is None:
        return True
    elapsed = now - fetched_at
    return elapsed < 0 or elapsed >= ttl_seconds


def fetched_at_label(fetched_at: float | None) -> str:
    """Time of the server fetch (not the time of the last trade)."""
    if fetched_at is None:
        return "—"
    return datetime.fromtimestamp(fetched_at, MOSCOW_TZ).strftime("%d.%m %H:%M:%S МСК")
