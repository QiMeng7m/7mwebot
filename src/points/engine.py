"""Point awarding engine — rate limits, caps and message scoring."""

from __future__ import annotations

import logging
import time
from collections import deque

from . import rules
from .models import PointAward
from .store import PointStore
from .util import is_group_enabled, local_day

logger = logging.getLogger(__name__)

WINDOW_SEC = 60
DEDUP_HISTORY = 3
MIN_STEAL_TARGET_BALANCE = 2


class PointEngine:
    """Turns incoming group messages into point ledger rows."""

    def __init__(self, store: PointStore, config):
        self._store = store
        self._config = config
        # (chat_id, sender_id) -> deque[timestamp]
        self._recent: dict[tuple[str, str], deque] = {}
        # (chat_id, sender_id, day) -> points earned today
        self._day_cache: dict[tuple[str, str, str], int] = {}
        # (chat_id, sender_id) -> deque[normalized text]
        self._texts: dict[tuple[str, str], deque] = {}

    # ── Public API ────────────────────────────────────────────────

    def is_enabled(self, msg: dict) -> bool:
        cfg = self._config
        if not getattr(cfg, "points_enabled", False):
            return False
        return is_group_enabled(
            msg.get("chat_id", ""), msg.get("group_name", ""),
            getattr(cfg, "points_groups", ["*"]),
        )

    def award_for_message(self, msg: dict, *,
                          allow_stale: bool = False) -> int:
        """Award points for one stored message.

        Returns the points credited to the sender (reply bonuses to the
        quoted user are written but not included in the return value).
        """
        if not self.is_enabled(msg):
            return 0

        msg_type = int(msg.get("msg_type", rules.MSG_TEXT) or rules.MSG_TEXT)
        if msg_type == rules.MSG_SYSTEM:
            return 0

        sender_id = str(msg.get("sender_id") or "")
        sender_name = str(msg.get("sender_name") or "")
        chat_id = str(msg.get("chat_id") or "")
        message_id = str(msg.get("message_id") or "")
        if not sender_id or not chat_id or not message_id:
            return 0

        bot_name = str(getattr(self._config, "bot_display_name", "") or "")
        if bot_name and (sender_name == bot_name or bot_name in sender_name):
            return 0

        now = int(time.time())
        ts = int(msg.get("timestamp") or now)
        if not allow_stale:
            max_age = int(getattr(self._config, "points_max_age_sec", 300))
            if now - ts > max_age:
                logger.debug(
                    "Points skipped (stale message age=%ds id=%s)",
                    now - ts, message_id[:20],
                )
                return 0
        # Backfill uses the historical timestamp as the event clock.
        clock = ts

        sender_key = (chat_id, sender_id)
        if not self._rate_ok(sender_key, clock):
            return 0

        body = rules.strip_reply_prefix(msg.get("content", ""))
        normalized = rules.normalize_text(body)
        if self._is_repeated(sender_key, normalized):
            return 0
        self._remember_text(sender_key, normalized)

        day = local_day(clock)
        # Only an explicit 「签到」 earns the daily bonus.  Ordinary chat
        # messages no longer count as checking in — members were being
        # told "already signed" merely because they had spoken earlier.
        is_checkin = (
            rules.is_checkin_command(msg.get("content", ""), bot_name)
            and not self._store.has_reason_on_day(
                chat_id, sender_id, "daily_first", day,
            )
        )

        components = rules.score_components(
            msg.get("content", ""),
            msg_type,
            base_score=int(getattr(self._config, "points_base_score", 1)),
            length_min_chars=int(getattr(
                self._config, "points_length_min_chars", 20)),
            length_bonus=int(getattr(self._config, "points_length_bonus", 1)),
            long_length_min_chars=int(getattr(
                self._config, "points_long_length_min_chars", 100)),
            long_length_bonus=int(getattr(
                self._config, "points_long_length_bonus", 1)),
            media_bonus=int(getattr(self._config, "points_media_bonus", 1)),
            daily_first_bonus=int(getattr(
                self._config, "points_daily_first_bonus", 2)),
            per_message_cap=int(getattr(
                self._config, "points_per_message_cap", 5)),
            is_daily_first=is_checkin,
        )
        if not components:
            return 0

        # Daily cap: drop lowest-priority components until it fits.
        daily_cap = int(getattr(self._config, "points_daily_cap", 80))
        earned_today = self._cached_day_points(chat_id, sender_id, day)
        remaining = max(0, daily_cap - earned_today)
        components = self._trim_to(components, remaining)
        if not components:
            return 0

        awards = [
            PointAward(sender_id, sender_name, reason, points, "earn")
            for reason, points in components
        ]

        # Reply bonus goes to the quoted member.  It counts against their
        # own daily cap but not against the sender's per-message cap.
        reply_to = rules.reply_target(msg.get("content", ""), sender_id)
        if reply_to and reply_to != sender_id:
            target_name = self._store.member_name(chat_id, reply_to)
            if bot_name and (
                target_name == bot_name or bot_name in target_name
            ):
                reply_to = ""
        if reply_to and reply_to != sender_id:
            bonus = int(getattr(self._config, "points_reply_bonus", 2))
            target_earned = self._cached_day_points(chat_id, reply_to, day)
            target_remaining = max(0, daily_cap - target_earned)
            if bonus > 0 and target_remaining > 0:
                bonus = min(bonus, target_remaining)
                target_name = self._store.member_name(chat_id, reply_to)
                awards.append(PointAward(
                    reply_to, target_name, "reply", bonus, "earn",
                ))

        inserted = self._store.record_events(
            chat_id, message_id, awards, now_ts=clock,
        )
        if not inserted:
            return 0

        gained = sum(points for reason, points in components)
        self._add_day_points(chat_id, sender_id, day, gained)
        if reply_to and reply_to != sender_id:
            self._add_day_points(
                chat_id, reply_to, day,
                sum(a.points for a in awards
                    if a.sender_id == reply_to and a.reason == "reply"),
            )
        return gained

    # ── Explicit check-in ─────────────────────────────────────────

    def check_in(self, chat_id: str, sender_id: str, sender_name: str,
                 message_id: str, now_ts: int | None = None) -> int:
        """Award the daily check-in bonus if the member hasn't earned it.

        Used by the explicit 「签到」 command when the command message
        itself was not scored (rate limit / repeated text).  Returns the
        points actually awarded (0 when already checked in).
        """
        now = int(time.time()) if now_ts is None else int(now_ts)
        day = local_day(now)
        if self._store.has_reason_on_day(
            chat_id, sender_id, "daily_first", day,
        ):
            return 0
        bonus = int(getattr(self._config, "points_daily_first_bonus", 10))
        if bonus <= 0:
            return 0
        daily_cap = int(getattr(self._config, "points_daily_cap", 80))
        earned = self._store.day_earned(chat_id, sender_id, day)
        bonus = min(bonus, max(0, daily_cap - earned))
        if bonus <= 0:
            return 0
        inserted = self._store.record_events(
            chat_id, message_id,
            [PointAward(sender_id, sender_name, "daily_first", bonus, "earn")],
            now_ts=now,
        )
        if inserted:
            self._add_day_points(chat_id, sender_id, day, bonus)
            return bonus
        return 0

    # ── Backfill ──────────────────────────────────────────────────

    def rebuild(self, chat_id: str, days: int = 90) -> dict:
        """Recompute earned points for a chat from stored messages.

        Manual adjustments, shop spending and duel transfers are kept.
        """
        now = int(time.time())
        since = now - max(1, int(days)) * 86400
        self._recent.clear()
        self._texts.clear()
        self._day_cache.clear()

        removed = self._store.delete_earn_events(chat_id)
        messages = self._store.messages_since(chat_id, since)
        restored = 0
        for msg in messages:
            restored += self.award_for_message(msg, allow_stale=True)
        logger.info(
            "Points rebuild for %s: %d messages, removed %d rows, restored %d pts",
            chat_id, len(messages), removed, restored,
        )
        return {
            "messages": len(messages),
            "removed": removed,
            "restored": restored,
        }

    # ── Internals ─────────────────────────────────────────────────

    def _rate_ok(self, key: tuple[str, str], now: int) -> bool:
        limit = int(getattr(self._config, "points_rate_limit_per_min", 8))
        bucket = self._recent.setdefault(key, deque())
        while bucket and now - bucket[0] > WINDOW_SEC:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def _is_repeated(self, key: tuple[str, str], normalized: str) -> bool:
        if not normalized:
            return False
        return normalized in self._texts.get(key, ())

    def _remember_text(self, key: tuple[str, str], normalized: str) -> None:
        if not normalized:
            return
        bucket = self._texts.setdefault(key, deque(maxlen=DEDUP_HISTORY))
        bucket.append(normalized)

    def _cached_day_points(self, chat_id: str, sender_id: str,
                           day: str) -> int:
        key = (chat_id, sender_id, day)
        if key not in self._day_cache:
            self._day_cache[key] = self._store.day_earned(
                chat_id, sender_id, day,
            )
        return self._day_cache[key]

    def _add_day_points(self, chat_id: str, sender_id: str,
                        day: str, points: int) -> None:
        if points <= 0:
            return
        key = (chat_id, sender_id, day)
        self._day_cache[key] = self._cached_day_points(
            chat_id, sender_id, day,
        ) + points

    @staticmethod
    def _trim_to(components: list[tuple[str, int]],
                 remaining: int) -> list[tuple[str, int]]:
        """Drop lowest-priority components until the total fits."""
        if remaining <= 0:
            return []
        kept = list(components)
        while kept and sum(p for _, p in kept) > remaining:
            kept.pop()
        return kept
