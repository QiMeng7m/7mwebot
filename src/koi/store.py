"""SQLite persistence for the 星盘 (KOI) gameplay.

Shares the bot's single SQLite connection and lock so everything runs on
one serialized connection.  All writes are idempotent through
``koi_ledger``'s UNIQUE(chat_id, sender_id, ref_id, reason) constraint.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Optional

from src.game_utils import local_day, local_week

logger = logging.getLogger(__name__)

_DAILY_FIELDS = (
    "chat_id", "sender_id", "day", "sign_id", "sign_level", "luck",
    "yi", "streak_days", "rerolls", "release_used", "draws_used",
    "chat_merit", "pool_in", "observed", "duel_star_used", "extra_luck",
    "fortune_used", "hexagram_used", "hexagram_id",
)


class KoiStore:
    def __init__(self, conn: sqlite3.Connection,
                 lock: Optional[threading.Lock] = None):
        self.conn = conn
        self._lock = lock if lock is not None else threading.RLock()

    @property
    def lock(self):
        return self._lock

    # ── Ledger ────────────────────────────────────────────────────

    def has_member(self, chat_id: str, sender_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM koi_ledger
                   WHERE chat_id=? AND sender_id=? LIMIT 1""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return row is not None

    def grant(self, chat_id: str, sender_id: str, sender_name: str,
              kind: str, amount: int, reason: str,
              ref_id: str = "", now_ts: int | None = None,
              day: str | None = None) -> bool:
        """Write one ledger row. Returns True when it was newly inserted."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        day = day or local_day(now)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO koi_ledger
                   (chat_id, sender_id, sender_name, kind, amount, reason,
                    ref_id, day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 str(kind), int(amount), str(reason), str(ref_id or ""),
                 str(day), now),
            )
            return bool(cur.rowcount)

    def balance(self, chat_id: str, sender_id: str) -> int:
        """香火余额 = SUM(merit rows)."""
        return self._sum_kind(chat_id, sender_id, "merit")

    def fortune(self, chat_id: str, sender_id: str) -> int:
        """累计气运 = SUM(fortune rows)."""
        return self._sum_kind(chat_id, sender_id, "fortune")

    def _sum_kind(self, chat_id: str, sender_id: str, kind: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(amount), 0) AS total
                   FROM koi_ledger
                   WHERE chat_id=? AND sender_id=? AND kind=?""",
                (str(chat_id), str(sender_id), str(kind)),
            ).fetchone()
        return int(row["total"] or 0) if row is not None else 0

    def leaderboard(self, chat_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, MAX(sender_name) AS sender_name,
                          SUM(amount) AS fortune
                   FROM koi_ledger
                   WHERE chat_id=? AND kind='fortune'
                   GROUP BY sender_id
                   HAVING fortune > 0
                   ORDER BY fortune DESC, sender_id ASC
                   LIMIT ?""",
                (str(chat_id), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def chat_ids(self) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT DISTINCT chat_id FROM koi_ledger
                   ORDER BY chat_id ASC"""
            ).fetchall()
        return [str(row["chat_id"]) for row in rows]

    def member_summaries(self, chat_id: str,
                         limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, MAX(sender_name) AS sender_name,
                          SUM(CASE WHEN kind='merit' THEN amount ELSE 0 END)
                              AS merit,
                          SUM(CASE WHEN kind='fortune' THEN amount ELSE 0 END)
                              AS fortune
                   FROM koi_ledger
                   WHERE chat_id=?
                   GROUP BY sender_id
                   ORDER BY fortune DESC, merit DESC, sender_id ASC
                   LIMIT ?""",
                (str(chat_id), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def star_counts_all(self, chat_id: str) -> dict[str, dict[str, int]]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, quadrant, COUNT(*) AS n
                   FROM koi_stars
                   WHERE chat_id=?
                   GROUP BY sender_id, quadrant""",
                (str(chat_id),),
            ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for row in rows:
            sender = str(row["sender_id"])
            result.setdefault(sender, {})[str(row["quadrant"])] = int(row["n"])
        return result

    def signed_today(self, chat_id: str, day: str | None = None) -> int:
        day = day or local_day()
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS n FROM koi_daily
                   WHERE chat_id=? AND day=? AND sign_id>0""",
                (str(chat_id), str(day)),
            ).fetchone()
        return int(row["n"] or 0) if row else 0

    # ── Daily state ───────────────────────────────────────────────

    def get_daily(self, chat_id: str, sender_id: str,
                  day: str | None = None) -> dict:
        day = day or local_day()
        with self._lock:
            row = self.conn.execute(
                """SELECT * FROM koi_daily
                   WHERE chat_id=? AND sender_id=? AND day=?""",
                (str(chat_id), str(sender_id), str(day)),
            ).fetchone()
        return dict(row) if row is not None else {}

    def upsert_daily(self, row: dict) -> None:
        values = {name: row.get(name) for name in _DAILY_FIELDS}
        values["sign_id"] = int(values.get("sign_id") or 0)
        values["luck"] = int(values.get("luck") or 0)
        values["streak_days"] = int(values.get("streak_days") or 0)
        values["rerolls"] = int(values.get("rerolls") or 0)
        values["release_used"] = int(values.get("release_used") or 0)
        values["draws_used"] = int(values.get("draws_used") or 0)
        values["chat_merit"] = int(values.get("chat_merit") or 0)
        values["pool_in"] = int(values.get("pool_in") or 0)
        values["observed"] = int(values.get("observed") or 0)
        values["duel_star_used"] = int(values.get("duel_star_used") or 0)
        values["extra_luck"] = int(values.get("extra_luck") or 0)
        values["fortune_used"] = int(values.get("fortune_used") or 0)
        values["hexagram_used"] = int(values.get("hexagram_used") or 0)
        values["hexagram_id"] = int(values.get("hexagram_id") or 0)
        for key in ("chat_id", "sender_id", "day", "sign_level", "yi"):
            values[key] = str(values.get(key) or "")
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO koi_daily
                   (chat_id, sender_id, day, sign_id, sign_level, luck, yi,
                    streak_days, rerolls, release_used, draws_used,
                    chat_merit, pool_in, observed, duel_star_used,
                    extra_luck, fortune_used, hexagram_used, hexagram_id)
                   VALUES (:chat_id, :sender_id, :day, :sign_id,
                           :sign_level, :luck, :yi, :streak_days, :rerolls,
                           :release_used, :draws_used, :chat_merit,
                           :pool_in, :observed, :duel_star_used,
                           :extra_luck, :fortune_used, :hexagram_used,
                           :hexagram_id)
                   ON CONFLICT(chat_id, sender_id, day) DO UPDATE SET
                       sign_id=excluded.sign_id,
                       sign_level=excluded.sign_level,
                       luck=excluded.luck,
                       yi=excluded.yi,
                       streak_days=excluded.streak_days,
                       rerolls=excluded.rerolls,
                       release_used=excluded.release_used,
                       draws_used=excluded.draws_used,
                       chat_merit=excluded.chat_merit,
                       pool_in=excluded.pool_in,
                       observed=excluded.observed,
                       duel_star_used=excluded.duel_star_used,
                       extra_luck=excluded.extra_luck,
                       fortune_used=excluded.fortune_used,
                       hexagram_used=excluded.hexagram_used,
                       hexagram_id=excluded.hexagram_id""",
                values,
            )

    # ── Sign collection ───────────────────────────────────────────

    def collect_sign(self, chat_id: str, sender_id: str,
                     sign_id: int, now_ts: int | None = None) -> bool:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO koi_signs
                   (chat_id, sender_id, sign_id, first_at)
                   VALUES (?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), int(sign_id), now),
            )
            return bool(cur.rowcount)

    def sign_ids(self, chat_id: str, sender_id: str) -> list[int]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sign_id FROM koi_signs
                   WHERE chat_id=? AND sender_id=?
                   ORDER BY sign_id ASC""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [int(row["sign_id"]) for row in rows]

    def sign_count(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS n FROM koi_signs
                   WHERE chat_id=? AND sender_id=?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    # ── Profile ───────────────────────────────────────────────────

    def get_profile(self, chat_id: str, sender_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT * FROM koi_profile
                   WHERE chat_id=? AND sender_id=?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return dict(row) if row is not None else {}

    def save_profile(self, chat_id: str, sender_id: str, *,
                     sign: str | None = None,
                     mbti: str | None = None,
                     moon: str | None = None,
                     rising: str | None = None,
                     noble_star: str | None = None,
                     life_number: int | None = None,
                     awakening: str | None = None,
                     birth_year: int | None = None,
                     birth_month: int | None = None,
                     birth_day: int | None = None,
                     birth_hour: int | None = None,
                     sign_updated_at: int | None = None,
                     mbti_updated_at: int | None = None,
                     now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        current = self.get_profile(chat_id, sender_id)
        values = {
            "chat_id": str(chat_id),
            "sender_id": str(sender_id),
            "sign": current.get("sign", "") if sign is None else sign,
            "mbti": current.get("mbti", "") if mbti is None else mbti,
            "moon_sign": current.get("moon_sign", "") if moon is None
            else moon,
            "rising_sign": current.get("rising_sign", "") if rising is None
            else rising,
            "noble_star": current.get("noble_star", "") if noble_star is None
            else noble_star,
            "life_number": (
                current.get("life_number", 0) if life_number is None
                else life_number),
            "awakening": current.get("awakening", "") if awakening is None
            else awakening,
            "birth_year": (
                current.get("birth_year", 0) if birth_year is None
                else birth_year),
            "birth_month": (
                current.get("birth_month", 0) if birth_month is None
                else birth_month),
            "birth_day": (
                current.get("birth_day", 0) if birth_day is None
                else birth_day),
            "birth_hour": (
                current.get("birth_hour", -1) if birth_hour is None
                else birth_hour),
            "title": current.get("title", ""),
            "sign_updated_at": (
                current.get("sign_updated_at", 0) if sign_updated_at is None
                else sign_updated_at),
            "mbti_updated_at": (
                current.get("mbti_updated_at", 0) if mbti_updated_at is None
                else mbti_updated_at),
            "updated_at": now,
        }
        values["life_number"] = int(values["life_number"] or 0)
        values["birth_year"] = int(values["birth_year"] or 0)
        values["birth_month"] = int(values["birth_month"] or 0)
        values["birth_day"] = int(values["birth_day"] or 0)
        values["birth_hour"] = int(values["birth_hour"])
        values["sign_updated_at"] = int(values["sign_updated_at"] or 0)
        values["mbti_updated_at"] = int(values["mbti_updated_at"] or 0)
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO koi_profile
                   (chat_id, sender_id, sign, mbti, moon_sign, rising_sign,
                    title, noble_star, life_number, awakening,
                    birth_year, birth_month, birth_day, birth_hour,
                    sign_updated_at, mbti_updated_at, updated_at)
                   VALUES (:chat_id, :sender_id, :sign, :mbti, :moon_sign,
                           :rising_sign, :title, :noble_star, :life_number,
                           :awakening, :birth_year, :birth_month, :birth_day,
                           :birth_hour, :sign_updated_at, :mbti_updated_at,
                           :updated_at)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                       sign=excluded.sign,
                       mbti=excluded.mbti,
                       moon_sign=excluded.moon_sign,
                       rising_sign=excluded.rising_sign,
                       title=excluded.title,
                       noble_star=excluded.noble_star,
                       life_number=excluded.life_number,
                       awakening=excluded.awakening,
                       birth_year=excluded.birth_year,
                       birth_month=excluded.birth_month,
                       birth_day=excluded.birth_day,
                       birth_hour=excluded.birth_hour,
                       sign_updated_at=excluded.sign_updated_at,
                       mbti_updated_at=excluded.mbti_updated_at,
                       updated_at=excluded.updated_at""",
                values,
            )

    # ── Counts / collection ───────────────────────────────────────

    def count_reason(self, chat_id: str, sender_id: str, reason: str,
                     since_ts: int | None = None) -> int:
        sql = ("SELECT COUNT(*) AS n FROM koi_ledger "
               "WHERE chat_id=? AND sender_id=? AND reason=?")
        params: list = [str(chat_id), str(sender_id), str(reason)]
        if since_ts is not None:
            sql += " AND created_at>=?"
            params.append(int(since_ts))
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    def collection_add(self, chat_id: str, sender_id: str, kind: str,
                       item_id: int, now_ts: int | None = None) -> bool:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO koi_collection
                   (chat_id, sender_id, kind, item_id, first_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(kind), int(item_id), now),
            )
            return bool(cur.rowcount)

    def collection_ids(self, chat_id: str, sender_id: str,
                       kind: str) -> list[int]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT item_id FROM koi_collection
                   WHERE chat_id=? AND sender_id=? AND kind=?
                   ORDER BY item_id ASC""",
                (str(chat_id), str(sender_id), str(kind)),
            ).fetchall()
        return [int(row["item_id"]) for row in rows]

    def collection_count(self, chat_id: str, sender_id: str,
                         kind: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS n FROM koi_collection
                   WHERE chat_id=? AND sender_id=? AND kind=?""",
                (str(chat_id), str(sender_id), str(kind)),
            ).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    def all_profiles(self, chat_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM koi_profile WHERE chat_id=?",
                (str(chat_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Buffs ─────────────────────────────────────────────────────

    def buff_add(self, chat_id: str, sender_id: str, kind: str,
                 value: float, uses_left: int,
                 expires_at: int = 0, now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """UPDATE koi_buffs SET active=0
                   WHERE chat_id=? AND sender_id=? AND expires_at>0
                     AND expires_at<?""",
                (str(chat_id), str(sender_id), now),
            )
            rows = self.conn.execute(
                """SELECT id FROM koi_buffs
                   WHERE chat_id=? AND sender_id=? AND active=1
                   ORDER BY id ASC""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
            if len(rows) >= 3:
                self.conn.execute(
                    "UPDATE koi_buffs SET active=0 WHERE id=?", (rows[0]["id"],))
            self.conn.execute(
                """INSERT INTO koi_buffs
                   (chat_id, sender_id, kind, value, uses_left, expires_at,
                    created_at, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (str(chat_id), str(sender_id), str(kind), float(value),
                 int(uses_left), int(expires_at), now),
            )

    def buffs_active(self, chat_id: str, sender_id: str,
                     now_ts: int | None = None) -> list[dict]:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """UPDATE koi_buffs SET active=0
                   WHERE chat_id=? AND sender_id=? AND active=1
                     AND expires_at>0 AND expires_at<?""",
                (str(chat_id), str(sender_id), now),
            )
            rows = self.conn.execute(
                """SELECT * FROM koi_buffs
                   WHERE chat_id=? AND sender_id=? AND active=1
                     AND uses_left>0
                   ORDER BY id ASC""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def consume_buff(self, chat_id: str, sender_id: str,
                     kind: str, uses: int = 1) -> dict | None:
        uses = max(1, int(uses))
        with self._lock, self.conn:
            row = self.conn.execute(
                """SELECT * FROM koi_buffs
                   WHERE chat_id=? AND sender_id=? AND kind=?
                     AND active=1 AND uses_left>0
                   ORDER BY id ASC LIMIT 1""",
                (str(chat_id), str(sender_id), str(kind)),
            ).fetchone()
            if row is None:
                return None
            left = int(row["uses_left"]) - uses
            self.conn.execute(
                "UPDATE koi_buffs SET uses_left=?, active=? WHERE id=?",
                (max(0, left), 1 if left > 0 else 0, row["id"]),
            )
        result = dict(row)
        result["uses_left"] = max(0, left)
        return result

    # ── Pity ──────────────────────────────────────────────────────

    def pity_get(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT draws_since_legendary FROM koi_pity
                   WHERE chat_id=? AND sender_id=?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row["draws_since_legendary"] or 0) if row else 0

    def pity_set(self, chat_id: str, sender_id: str, value: int,
                 now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO koi_pity
                   (chat_id, sender_id, draws_since_legendary, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                       draws_since_legendary=excluded.draws_since_legendary,
                       updated_at=excluded.updated_at""",
                (str(chat_id), str(sender_id), max(0, int(value)), now),
            )

    # ── 锦鲤池 ────────────────────────────────────────────────────

    def pool_get(self, chat_id: str, day: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM koi_pool WHERE chat_id=? AND day=?",
                (str(chat_id), str(day)),
            ).fetchone()
        return dict(row) if row is not None else {}

    def pool_add(self, chat_id: str, day: str, sender_id: str,
                 sender_name: str, amount: int, luck: int,
                 now_ts: int | None = None) -> int:
        now = int(time.time()) if now_ts is None else int(now_ts)
        amount = int(amount)
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT status, total FROM koi_pool WHERE chat_id=? AND day=?",
                (str(chat_id), str(day)),
            ).fetchone()
            if row is not None and str(row["status"]) != "open":
                return -1
            if row is None:
                self.conn.execute(
                    """INSERT INTO koi_pool (chat_id, day, total, status)
                       VALUES (?, ?, ?, 'open')""",
                    (str(chat_id), str(day), amount),
                )
            else:
                self.conn.execute(
                    """UPDATE koi_pool SET total=total+?
                       WHERE chat_id=? AND day=?""",
                    (amount, str(chat_id), str(day)),
                )
            self.conn.execute(
                """INSERT INTO koi_pool_entries
                   (chat_id, day, sender_id, sender_name, amount, luck,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(day), str(sender_id),
                 str(sender_name or ""), amount, int(luck), now),
            )
            row = self.conn.execute(
                "SELECT total FROM koi_pool WHERE chat_id=? AND day=?",
                (str(chat_id), str(day)),
            ).fetchone()
        return int(row["total"] or 0) if row else 0

    def pool_entries(self, chat_id: str, day: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id,
                          MAX(sender_name) AS sender_name,
                          SUM(amount) AS amount,
                          MAX(luck) AS luck
                   FROM koi_pool_entries
                   WHERE chat_id=? AND day=?
                   GROUP BY sender_id
                   ORDER BY amount DESC, sender_id ASC""",
                (str(chat_id), str(day)),
            ).fetchall()
        return [dict(row) for row in rows]

    def open_pool_chats(self, day: str | None = None) -> list[str]:
        day = day or local_day()
        with self._lock:
            rows = self.conn.execute(
                """SELECT DISTINCT chat_id FROM koi_pool
                   WHERE day=? AND status='open'""",
                (str(day),),
            ).fetchall()
        return [str(row["chat_id"]) for row in rows]

    def open_pools(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT chat_id, day FROM koi_pool
                   WHERE status='open' ORDER BY day ASC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def pool_settle(self, chat_id: str, day: str, winner_id: str,
                    winner_name: str, payout: int,
                    now_ts: int | None = None) -> bool:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """UPDATE koi_pool
                   SET status='drawn', winner_id=?, winner_name=?,
                       payout=?, drawn_at=?
                   WHERE chat_id=? AND day=? AND status='open'""",
                (str(winner_id), str(winner_name or ""), int(payout), now,
                 str(chat_id), str(day)),
            )
            return bool(cur.rowcount)

    def pool_refund(self, chat_id: str, day: str,
                    now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """UPDATE koi_pool SET status='refunded', drawn_at=?
                   WHERE chat_id=? AND day=? AND status='open'""",
                (now, str(chat_id), str(day)),
            )

    # ── 星盘 ──────────────────────────────────────────────────────

    def stars(self, chat_id: str, sender_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT quadrant, star_index, lit_at FROM koi_stars
                   WHERE chat_id=? AND sender_id=?
                   ORDER BY quadrant, star_index""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def star_counts(self, chat_id: str, sender_id: str) -> dict[str, int]:
        counts = {key: 0 for key in
                  ("qinglong", "baihu", "zhuque", "xuanwu", "outer")}
        for row in self.stars(chat_id, sender_id):
            key = str(row["quadrant"])
            counts[key] = counts.get(key, 0) + 1
        return counts

    def star_total(self, chat_id: str, sender_id: str,
                   include_outer: bool = False) -> int:
        counts = self.star_counts(chat_id, sender_id)
        total = sum(counts.get(k, 0) for k in
                    ("qinglong", "baihu", "zhuque", "xuanwu"))
        if include_outer:
            total += counts.get("outer", 0)
        return total

    def add_star(self, chat_id: str, sender_id: str, quadrant: str,
                 star_index: int, now_ts: int | None = None) -> bool:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO koi_stars
                   (chat_id, sender_id, quadrant, star_index, lit_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(quadrant),
                 int(star_index), now),
            )
            return bool(cur.rowcount)

    def stars_since_by_member(self, chat_id: str,
                              since_ts: int) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, quadrant, COUNT(*) AS n
                   FROM koi_stars
                   WHERE chat_id=? AND lit_at>=?
                   GROUP BY sender_id, quadrant""",
                (str(chat_id), int(since_ts)),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_members_since(self, chat_id: str,
                             since_ts: int) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT DISTINCT sender_id FROM koi_ledger
                   WHERE chat_id=? AND created_at>=?""",
                (str(chat_id), int(since_ts)),
            ).fetchall()
        return [str(row["sender_id"]) for row in rows]

    def faction_week_get(self, chat_id: str, week: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT * FROM koi_faction_week
                   WHERE chat_id=? AND week=?""",
                (str(chat_id), str(week)),
            ).fetchone()
        return dict(row) if row is not None else {}

    def faction_week_set(self, chat_id: str, week: str, winner: str,
                         now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO koi_faction_week
                   (chat_id, week, winner, settled_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, week) DO UPDATE SET
                       winner=excluded.winner,
                       settled_at=excluded.settled_at""",
                (str(chat_id), str(week), str(winner or ""), now),
            )

    # ── 周运 ──────────────────────────────────────────────────────

    def weekly_get(self, chat_id: str, sender_id: str, week: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT * FROM koi_weekly
                   WHERE chat_id=? AND sender_id=? AND week=?""",
                (str(chat_id), str(sender_id), str(week)),
            ).fetchone()
        return dict(row) if row is not None else {}

    def weekly_save(self, chat_id: str, sender_id: str, week: str,
                    goals_json: str, claimed: int = 0) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO koi_weekly
                   (chat_id, sender_id, week, goals_json, claimed)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id, week) DO UPDATE SET
                       goals_json=excluded.goals_json,
                       claimed=excluded.claimed""",
                (str(chat_id), str(sender_id), str(week),
                 str(goals_json), int(claimed)),
            )

    def weekly_claim(self, chat_id: str, sender_id: str, week: str) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute(
                """UPDATE koi_weekly SET claimed=1
                   WHERE chat_id=? AND sender_id=? AND week=? AND claimed=0""",
                (str(chat_id), str(sender_id), str(week)),
            )
            return bool(cur.rowcount)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def week_key() -> str:
        return local_week()

    @staticmethod
    def today() -> str:
        return local_day()
