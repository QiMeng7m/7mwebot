"""Small time / group-whitelist helpers shared by the points modules."""

from __future__ import annotations

import time
from datetime import date


def local_day(ts: int | float | None = None) -> str:
    """Local calendar day as ``YYYY-MM-DD``."""
    return time.strftime("%Y-%m-%d", time.localtime(
        time.time() if ts is None else ts
    ))


def local_season(ts: int | float | None = None) -> str:
    """Local calendar month as ``YYYY-MM`` (the season key)."""
    return time.strftime("%Y-%m", time.localtime(
        time.time() if ts is None else ts
    ))


def previous_season(ts: int | float | None = None) -> str:
    """The season immediately before the one containing *ts*."""
    d = date.fromtimestamp(time.time() if ts is None else ts)
    year, month = d.year, d.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def season_end_day(season: str) -> str:
    """Last calendar day of a ``YYYY-MM`` season."""
    year, month = (int(part) for part in season.split("-"))
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    first_next = date(next_year, next_month, 1)
    return date.fromordinal(first_next.toordinal() - 1).isoformat()


def days_until_season_end(ts: int | float | None = None) -> int:
    """Days remaining in the current season (>= 0)."""
    today = date.fromtimestamp(time.time() if ts is None else ts)
    end = date.fromisoformat(season_end_day(local_season(ts)))
    return max(0, (end - today).days)


def is_group_enabled(chat_id: str, group_name: str,
                     groups: list[str] | tuple[str, ...] | None) -> bool:
    """Match a group against a whitelist (``*`` means every group)."""
    if not groups:
        return False
    if "*" in groups:
        return True
    keys = {str(chat_id or "").strip(), str(group_name or "").strip()}
    keys.discard("")
    return any(g in keys for g in groups)
