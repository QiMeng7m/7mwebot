"""Short-term per-user conversation memory.

Why this exists: WeChat delivers the bot's own replies to the poller, but
``MessageRouter.handle()`` drops them before persisting (to prevent reply
loops).  The messages DB therefore never contains the bot's side of a
conversation.  Without this store, when the bot asks "哪个群？" and the
user answers "摸鱼群", the model only sees "摸鱼群" and treats it as a
brand-new topic.

Keyed by ``(chat_id, sender_id)`` so one member's dialogue never leaks
into another's.  In-memory and TTL-bounded — restart clears it, which is
fine for a short follow-up window.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_TTL_SEC = 1800      # 30 minutes
DEFAULT_MAX_TURNS = 6       # 6 Q&A pairs = 12 messages
MAX_TRACKED_USERS = 2000    # global cap, oldest key evicted first
MAX_TURN_CHARS = 400        # per message, keeps prompts bounded


@dataclass
class Turn:
    user: str
    bot: str
    ts: float


class ConversationMemory:
    """Thread-safe short-term dialogue memory for @mention follow-ups."""

    def __init__(self, ttl_sec: float = DEFAULT_TTL_SEC,
                 max_turns: int = DEFAULT_MAX_TURNS):
        self._ttl = max(60.0, float(ttl_sec))
        self._max_turns = max(1, int(max_turns))
        self._lock = threading.Lock()
        self._turns: OrderedDict[tuple[str, str], list[Turn]] = OrderedDict()

    # ── Public API ───────────────────────────────────────────────

    def add_turn(self, chat_id: str, sender_id: str,
                 user_text: str, bot_text: str,
                 ts: float | None = None) -> None:
        """Record one user message and the bot's reply to it."""
        user_text = self._clip(user_text)
        bot_text = self._clip(bot_text)
        if not user_text or not bot_text:
            return

        key = (str(chat_id), str(sender_id))
        turn = Turn(user=user_text, bot=bot_text, ts=ts or time.time())

        with self._lock:
            turns = self._turns.get(key)
            if turns is None:
                turns = []
                self._turns[key] = turns
            turns.append(turn)
            if len(turns) > self._max_turns:
                del turns[:-self._max_turns]
            self._turns.move_to_end(key)

            while len(self._turns) > MAX_TRACKED_USERS:
                self._turns.popitem(last=False)

    def recent_turns(self, chat_id: str, sender_id: str,
                     limit: int | None = None) -> list[dict]:
        """Return non-expired turns, oldest first, as plain dicts."""
        key = (str(chat_id), str(sender_id))
        now = time.time()
        with self._lock:
            turns = list(self._turns.get(key, ()))
            self._turns[key] = [t for t in turns if now - t.ts <= self._ttl]
            if not self._turns[key]:
                self._turns.pop(key, None)
                return []
            self._turns.move_to_end(key)
            turns = self._turns[key]

        if limit is not None and limit > 0:
            turns = turns[-limit:]
        return [
            {"user": t.user, "bot": t.bot, "ts": t.ts}
            for t in turns
        ]

    def clear(self, chat_id: str, sender_id: str) -> None:
        with self._lock:
            self._turns.pop((str(chat_id), str(sender_id)), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

    # ── Internals ────────────────────────────────────────────────

    @staticmethod
    def _clip(text: str) -> str:
        text = " ".join(str(text or "").split())
        if len(text) > MAX_TURN_CHARS:
            text = text[:MAX_TURN_CHARS] + "…"
        return text
