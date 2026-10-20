"""Track new group members and remove those who stay silent.

A join event starts a deadline (default 10 minutes).  Any message from
that member in that group cancels it.  The backend's kick loop calls
``due()`` and removes expired members through the WeChat window.

State is persisted to ``data/pending_kicks.json`` so a bot restart does
not forget a member who joined a few minutes earlier.  Entries whose
deadline already passed while the bot was offline are dropped on load —
we don't kick people long after the fact.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_PATH = Path("data/pending_kicks.json")


class InactiveKickTracker:
    """Thread-safe set of pending "speak or be removed" deadlines."""

    def __init__(self, timeout_sec: int = 600,
                 path: Path | str = DEFAULT_STATE_PATH,
                 clock=time.time) -> None:
        self._timeout = int(timeout_sec)
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._load()

    # ── Public API ───────────────────────────────────────────────────

    @property
    def timeout_sec(self) -> int:
        return self._timeout

    def note_join(self, chat_id: str, group_name: str, wxid: str,
                  display_name: str,
                  joined_at: int | float | None = None) -> bool:
        """Start a countdown for a newly joined member."""
        if not chat_id or not wxid:
            return False
        now = self._clock()
        joined = int(joined_at or now)
        entry = {
            "chat_id": chat_id,
            "group_name": group_name,
            "wxid": wxid,
            "display_name": display_name,
            "joined_at": joined,
            "deadline": joined + self._timeout,
            "spoke": False,
        }
        with self._lock:
            self._entries[self._key(chat_id, wxid)] = entry
            self._save_locked()
        logger.info(
            "Kick timer started: %s (%s) in '%s' — deadline in %ds",
            display_name, wxid, group_name[:20],
            max(0, entry["deadline"] - int(now)),
        )
        return True

    def note_activity(self, chat_id: str, wxid: str) -> None:
        """Cancel the countdown when the member speaks."""
        if not chat_id or not wxid:
            return
        key = self._key(chat_id, wxid)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry["spoke"]:
                return
            entry["spoke"] = True
            self._save_locked()
            name = entry["display_name"]
        logger.info("Kick timer cancelled: %s spoke in the group", name)

    def due(self, now: float | None = None) -> list[dict]:
        """Return entries whose deadline has passed and who stayed silent."""
        now = self._clock() if now is None else now
        with self._lock:
            return [
                dict(entry) for entry in self._entries.values()
                if not entry["spoke"] and entry["deadline"] <= now
            ]

    def remove(self, chat_id: str, wxid: str) -> dict | None:
        """Drop an entry (after a successful kick or when giving up)."""
        with self._lock:
            entry = self._entries.pop(self._key(chat_id, wxid), None)
            if entry is not None:
                self._save_locked()
        return entry

    def has_spoken(self, chat_id: str, wxid: str) -> bool:
        with self._lock:
            entry = self._entries.get(self._key(chat_id, wxid))
            return bool(entry and entry["spoke"])

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._entries.values()]

    # ── Internals ────────────────────────────────────────────────────

    @staticmethod
    def _key(chat_id: str, wxid: str) -> str:
        return f"{chat_id}|{wxid}"

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.exception("Failed to load pending kicks from %s", self._path)
            return
        now = self._clock()
        entries: dict[str, dict] = {}
        for entry in raw.values() if isinstance(raw, dict) else raw:
            try:
                key = self._key(entry["chat_id"], entry["wxid"])
                # A deadline missed while offline is dropped: welcoming /
                # removing people long after the fact is worse than skipping.
                if entry.get("spoke") or entry.get("deadline", 0) <= now:
                    continue
                entries[key] = entry
            except (KeyError, TypeError):
                continue
        self._entries = entries
        if entries:
            logger.info("Loaded %d pending kick timer(s)", len(entries))

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            logger.exception("Failed to persist pending kicks to %s", self._path)
