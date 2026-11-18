"""Shared time / group / text helpers used by non-game and 星盘 modules.

Extracted from the retired points package so the star game has no
dependency on the deleted gameplay code.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

# Zero-width / odd spaces WeChat inserts between command and arguments.
INVISIBLE_WS = r"[\s ​﻿]"
_INVISIBLE_RE = re.compile(r"[\s ​﻿]+")
_WCDB_PREFIX_RE = re.compile(r"^\s*[A-Za-z0-9_@.\-]{1,64}:\s+")


def local_day(ts: int | float | None = None) -> str:
    """Local calendar day as ``YYYY-MM-DD``."""
    return time.strftime("%Y-%m-%d", time.localtime(
        time.time() if ts is None else ts))


def local_hour_start(ts: int | float | None = None) -> int:
    """Unix timestamp of the start of the local clock hour containing *ts*."""
    moment = int(time.time() if ts is None else ts)
    local = time.localtime(moment)
    return moment - int(local.tm_min) * 60 - int(local.tm_sec)


def local_season(ts: int | float | None = None) -> str:
    """Local calendar month as ``YYYY-MM``."""
    return time.strftime("%Y-%m", time.localtime(
        time.time() if ts is None else ts))


def local_week(ts: int | float | None = None) -> str:
    """ISO week key ``YYYY-Www``."""
    moment = time.time() if ts is None else ts
    iso = date.fromtimestamp(moment).isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def days_until_week_end(ts: int | float | None = None) -> int:
    moment = time.time() if ts is None else ts
    return max(0, 7 - date.fromtimestamp(moment).isoweekday())


def strip_invisible(text: str) -> str:
    """Collapse whitespace (including zero-width chars) to single spaces."""
    return _INVISIBLE_RE.sub(" ", str(text or "")).strip()


def clean_command_text(content: str, bot_name: str = "",
                       aliases: tuple[str, ...] | list[str] = ()) -> str:
    """Strip WCDB's ``wxid_xxx:`` prefix and the bot's own @mention."""
    text = _WCDB_PREFIX_RE.sub("", str(content or ""))
    for name in (bot_name, *aliases):
        if name:
            text = re.sub(
                re.escape(f"@{name}") + r"[^\w]*", "", text,
            )
    return strip_invisible(text)


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


def load_group_members(chat_id: str,
                       path: Path | str | None = None
                       ) -> dict[str, str] | None:
    """Return the last known ``{wxid: display_name}`` snapshot for a group."""
    file_path = Path(path) if path else Path("data") / "group_members.json"
    try:
        if not file_path.exists():
            return None
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    members = raw.get(str(chat_id))
    if not isinstance(members, dict):
        return None
    return {
        str(wxid): str(name or wxid)
        for wxid, name in members.items()
        if str(wxid)
    }
