"""SQLite persistence for points, equipment, shields and duels.

The store shares the bot's SQLite connection (and, when provided, its
lock) so that everything runs in one serialized connection — the same
approach used by :class:`src.db.store.MessageStore`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from datetime import date, timedelta
from typing import Iterable, Optional

from .models import MemberStats, PointAward
from .util import local_day, local_season

logger = logging.getLogger(__name__)

EARN_CATEGORIES = ("earn", "adjust")


class PointStore:
    """All point-system database access."""

    def __init__(self, conn: sqlite3.Connection,
                 lock: Optional[threading.Lock] = None):
        self.conn = conn
        self._lock = lock if lock is not None else threading.RLock()

    # ── Ledger writes ─────────────────────────────────────────────

    def record_events(self, chat_id: str, message_id: str,
                      awards: Iterable[PointAward],
                      now_ts: int | None = None) -> int:
        """Insert awards idempotently. Returns the number actually written."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        day = local_day(now)
        season = local_season(now)
        inserted = 0
        with self._lock, self.conn:
            for award in awards:
                cur = self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(chat_id), str(award.sender_id),
                     str(award.sender_name or ""), str(message_id),
                     award.reason, award.category, int(award.points),
                     day, season, now),
                )
                inserted += cur.rowcount
        return inserted

    def record_event(self, chat_id: str, sender_id: str, sender_name: str,
                     message_id: str, reason: str, points: int,
                     category: str = "earn",
                     now_ts: int | None = None) -> bool:
        awarded = self.record_events(
            chat_id, message_id,
            [PointAward(sender_id, sender_name, reason, points, category)],
            now_ts=now_ts,
        )
        return bool(awarded)

    def adjust(self, chat_id: str, sender_id: str, sender_name: str,
               delta: int, note: str = "",
               now_ts: int | None = None) -> int:
        """Admin adjustment. Returns the member's new balance."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        message_id = f"manual:{uuid.uuid4().hex}"
        self.record_event(
            chat_id, sender_id, sender_name, message_id,
            "manual", int(delta), category="adjust", now_ts=now,
        )
        return self.balance_of(chat_id, sender_id)

    # ── Balances and boards ───────────────────────────────────────

    def balance_of(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def earned_of(self, chat_id: str, sender_id: str) -> int:
        """Cumulative earned points (ranking / level basis)."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                     AND category IN ('earn', 'adjust')""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def balances(self, chat_id: str) -> dict[str, int]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, COALESCE(SUM(points), 0) AS balance
                   FROM point_events WHERE chat_id = ?
                   GROUP BY sender_id""",
                (str(chat_id),),
            ).fetchall()
        return {row["sender_id"]: int(row["balance"]) for row in rows}

    def _board_rows(self, chat_id: str, scope: str, limit: int | None,
                    now_ts: int | None) -> list[dict]:
        now = int(time.time()) if now_ts is None else int(now_ts)
        where = "chat_id = ? AND category IN ('earn', 'adjust')"
        params: list = [str(chat_id)]
        if scope == "season":
            where += " AND season = ?"
            params.append(local_season(now))
        elif scope == "day":
            where += " AND day = ?"
            params.append(local_day(now))
        sql = f"""SELECT sender_id,
                        MAX(sender_name) AS sender_name,
                        SUM(points)      AS points,
                        MAX(created_at)  AS last_at
                 FROM point_events
                 WHERE {where}
                 GROUP BY sender_id
                 ORDER BY points DESC, last_at DESC, sender_id ASC"""
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))

        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()

        result = []
        rank = 0
        last_points = None
        for index, row in enumerate(rows, 1):
            points = int(row["points"])
            if points != last_points:
                rank = index
                last_points = points
            result.append({
                "sender_id": row["sender_id"],
                "sender_name": row["sender_name"] or row["sender_id"],
                "points": points,
                "last_at": int(row["last_at"] or 0),
                "rank": rank,
            })
        return result

    def board(self, chat_id: str, scope: str = "total", limit: int = 10,
              now_ts: int | None = None) -> list[dict]:
        """Ranking rows with the member's current balance attached."""
        rows = self._board_rows(chat_id, scope, limit, now_ts)
        balance_map = self.balances(chat_id)
        for row in rows:
            row["balance"] = balance_map.get(row["sender_id"], 0)
        return rows

    def participants(self, chat_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(DISTINCT sender_id) FROM point_events
                   WHERE chat_id = ? AND category IN ('earn', 'adjust')""",
                (str(chat_id),),
            ).fetchone()
        return int(row[0]) if row else 0

    def day_earned(self, chat_id: str, sender_id: str, day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ? AND day = ?
                     AND category = 'earn'""",
                (str(chat_id), str(sender_id), day),
            ).fetchone()
        return int(row[0]) if row else 0

    def day_total(self, chat_id: str, day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND day = ? AND category = 'earn'""",
                (str(chat_id), day),
            ).fetchone()
        return int(row[0]) if row else 0

    def daily_series(self, chat_id: str, days: int = 14,
                     now_ts: int | None = None) -> list[dict]:
        """Earned points per day for the last *days* days (including zeros)."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        days = max(1, int(days))
        start = local_day(now - (days - 1) * 86400)
        with self._lock:
            rows = self.conn.execute(
                """SELECT day, COALESCE(SUM(points), 0) AS points
                   FROM point_events
                   WHERE chat_id = ? AND category IN ('earn', 'adjust')
                     AND day >= ?
                   GROUP BY day ORDER BY day ASC""",
                (str(chat_id), start),
            ).fetchall()
        found = {row["day"]: int(row["points"]) for row in rows}

        series: list[dict] = []
        today = date.fromtimestamp(now)
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            series.append({"day": day, "points": found.get(day, 0)})
        return series

    def has_earned_on_day(self, chat_id: str, sender_id: str, day: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM point_events
                   WHERE chat_id = ? AND sender_id = ? AND day = ?
                     AND category = 'earn' LIMIT 1""",
                (str(chat_id), str(sender_id), day),
            ).fetchone()
        return row is not None

    def event_points(self, chat_id: str, message_id: str,
                     reason: str) -> int:
        """Points awarded for one message and reason (0 when absent)."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND message_id = ? AND reason = ?
                     AND category = 'earn'""",
                (str(chat_id), str(message_id), reason),
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def has_reason_on_day(self, chat_id: str, sender_id: str,
                          reason: str, day: str) -> bool:
        """True when this member already earned *reason* on *day*."""
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM point_events
                   WHERE chat_id = ? AND sender_id = ? AND reason = ?
                     AND day = ? AND category = 'earn' LIMIT 1""",
                (str(chat_id), str(sender_id), reason, day),
            ).fetchone()
        return row is not None

    def recent_events(self, chat_id: str, sender_id: str,
                      limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT reason, category, points, day, season, created_at
                   FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (str(chat_id), str(sender_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def member_stats(self, chat_id: str, sender_id: str,
                     sender_name: str = "",
                     now_ts: int | None = None) -> MemberStats:
        now = int(time.time()) if now_ts is None else int(now_ts)
        season = local_season(now)
        day = local_day(now)

        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(CASE WHEN category IN ('earn','adjust')
                                            THEN points ELSE 0 END), 0) AS earned,
                          COALESCE(SUM(points), 0) AS balance,
                          COALESCE(SUM(CASE WHEN category IN ('earn','adjust')
                                             AND season = ? THEN points ELSE 0 END), 0)
                              AS season_points,
                          COALESCE(SUM(CASE WHEN category IN ('earn','adjust')
                                             AND day = ? THEN points ELSE 0 END), 0)
                              AS day_points,
                          COALESCE(MAX(created_at), 0) AS last_at
                   FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (season, day, str(chat_id), str(sender_id)),
            ).fetchone()
            name_row = self.conn.execute(
                """SELECT sender_name FROM user_last_message
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()

        stats = MemberStats(
            sender_id=str(sender_id),
            sender_name=(sender_name or (name_row[0] if name_row else "")
                         or str(sender_id)),
            earned=int(row["earned"] or 0),
            balance=int(row["balance"] or 0),
            season_points=int(row["season_points"] or 0),
            day_points=int(row["day_points"] or 0),
            last_at=int(row["last_at"] or 0),
        )

        total_rows = self._board_rows(chat_id, "total", None, now)
        season_rows = self._board_rows(chat_id, "season", None, now)
        stats.total_rank = self._rank_of(total_rows, sender_id)
        stats.season_rank = self._rank_of(season_rows, sender_id)
        stats.streak_days = self._streak_days(chat_id, sender_id, now)
        return stats

    @staticmethod
    def _rank_of(rows: list[dict], sender_id: str) -> int:
        for row in rows:
            if row["sender_id"] == sender_id:
                return int(row["rank"])
        return 0

    def _streak_days(self, chat_id: str, sender_id: str,
                     now_ts: int) -> int:
        with self._lock:
            rows = self.conn.execute(
                """SELECT DISTINCT day FROM point_events
                   WHERE chat_id = ? AND sender_id = ?
                     AND category IN ('earn', 'adjust') AND points > 0
                   ORDER BY day DESC LIMIT 400""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        days = {row[0] for row in rows}
        if not days:
            return 0

        today = date.fromtimestamp(now_ts)
        cursor = today if today.isoformat() in days else today - timedelta(days=1)
        if cursor.isoformat() not in days:
            return 0

        streak = 0
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak

    # ── Seasons ───────────────────────────────────────────────────

    def close_season(self, chat_id: str, season: str,
                     now_ts: int | None = None) -> dict | None:
        """Record the winner of *season* once. Returns the winner row."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            exists = self.conn.execute(
                """SELECT 1 FROM point_seasons
                   WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
            if not exists:
                row = self.conn.execute(
                    """SELECT sender_id, MAX(sender_name) AS sender_name,
                              SUM(points) AS points
                       FROM point_events
                       WHERE chat_id = ? AND season = ?
                         AND category IN ('earn', 'adjust')
                       GROUP BY sender_id
                       ORDER BY points DESC, MAX(created_at) DESC
                       LIMIT 1""",
                    (str(chat_id), str(season)),
                ).fetchone()
                winner_id = row["sender_id"] if row else ""
                winner_name = (row["sender_name"] if row else "") or winner_id
                winner_points = int(row["points"]) if row else 0
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_seasons
                       (chat_id, season, winner_id, winner_name,
                        winner_points, closed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(chat_id), str(season), winner_id, winner_name,
                     winner_points, now),
                )
            stored = self.conn.execute(
                """SELECT * FROM point_seasons
                   WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()

        if not stored or not stored["winner_id"]:
            return None
        return {
            "season": stored["season"],
            "winner_id": stored["winner_id"],
            "winner_name": stored["winner_name"],
            "winner_points": int(stored["winner_points"]),
        }

    def season_winner(self, chat_id: str, season: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                """SELECT season, winner_id, winner_name, winner_points
                   FROM point_seasons WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
        if not row or not row["winner_id"]:
            return None
        return {
            "season": row["season"],
            "winner_id": row["winner_id"],
            "winner_name": row["winner_name"],
            "winner_points": int(row["winner_points"]),
        }

    # ── Members ───────────────────────────────────────────────────

    def members(self, chat_id: str) -> list[dict]:
        """Everyone who has spoken in this group (name + id)."""
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, sender_name FROM user_last_message
                   WHERE chat_id = ?""",
                (str(chat_id),),
            ).fetchall()
        return [{"sender_id": row[0], "sender_name": row[1] or row[0]}
                for row in rows]

    def has_member(self, chat_id: str, sender_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM user_last_message
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return row is not None

    def member_name(self, chat_id: str, sender_id: str) -> str:
        with self._lock:
            row = self.conn.execute(
                """SELECT sender_name FROM user_last_message
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return (row[0] if row and row[0] else str(sender_id))

    # ── Equipment ─────────────────────────────────────────────────

    def owned_items(self, chat_id: str, sender_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT item_id, purchased_at, equipped, enhance_level
                   FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ?
                   ORDER BY purchased_at ASC""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def owns(self, chat_id: str, sender_id: str, item_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(item_id)),
            ).fetchone()
        return row is not None

    def _slot_occupied_locked(self, chat_id: str, sender_id: str,
                              same_slot_ids: list[str]) -> bool:
        if not same_slot_ids:
            return False
        placeholders = ",".join("?" for _ in same_slot_ids)
        row = self.conn.execute(
            f"""SELECT 1 FROM equipment_owned
                WHERE chat_id = ? AND sender_id = ? AND equipped = 1
                  AND item_id IN ({placeholders}) LIMIT 1""",
            [str(chat_id), str(sender_id), *same_slot_ids],
        ).fetchone()
        return row is not None

    def buy_item(self, chat_id: str, sender_id: str, sender_name: str,
                 item: dict, same_slot_ids: list[str],
                 now_ts: int | None = None) -> tuple[bool, str, int, bool]:
        """Buy an item. Returns ``(ok, code, balance_after, equipped)``."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        price = int(item["price"])
        item_id = str(item["id"])
        with self._lock, self.conn:
            balance_row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            balance = int(balance_row[0]) if balance_row else 0
            if balance < price:
                return False, "insufficient", balance, False

            owned = self.conn.execute(
                """SELECT 1 FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), item_id),
            ).fetchone()
            if owned:
                return False, "owned", balance, False

            equipped = not self._slot_occupied_locked(
                chat_id, sender_id, same_slot_ids
            )

            cur = self.conn.execute(
                """INSERT INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, 'buy_item', 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0) FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"buy:{uuid.uuid4().hex}", -price,
                 local_day(now), local_season(now), now,
                 str(chat_id), str(sender_id), price),
            )
            if cur.rowcount == 0:
                return False, "insufficient", balance, False
            self.conn.execute(
                """INSERT INTO equipment_owned
                   (chat_id, sender_id, item_id, purchased_at, equipped)
                   VALUES (?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), item_id, now,
                 1 if equipped else 0),
            )
        return True, "ok", balance - price, equipped

    def equip_item(self, chat_id: str, sender_id: str, item_id: str,
                   same_slot_ids: list[str], equip: bool = True) -> bool:
        """Equip/unequip an owned item. Returns False if not owned."""
        with self._lock, self.conn:
            owned = self.conn.execute(
                """SELECT 1 FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(item_id)),
            ).fetchone()
            if not owned:
                return False
            if equip and same_slot_ids:
                placeholders = ",".join("?" for _ in same_slot_ids)
                self.conn.execute(
                    f"""UPDATE equipment_owned SET equipped = 0
                        WHERE chat_id = ? AND sender_id = ?
                          AND item_id IN ({placeholders})""",
                    [str(chat_id), str(sender_id), *same_slot_ids],
                )
            self.conn.execute(
                """UPDATE equipment_owned SET equipped = ?
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (1 if equip else 0, str(chat_id), str(sender_id),
                 str(item_id)),
            )
        return True

    def item_enhance_level(self, chat_id: str, sender_id: str,
                           item_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT enhance_level FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(item_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def max_enhance_level(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(MAX(enhance_level), 0)
                   FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def enhance_item(self, chat_id: str, sender_id: str,
                     sender_name: str, item_id: str, cost: int,
                     max_level: int, message_id: str,
                     now_ts: int | None = None,
                     ) -> tuple[bool, str, int, int]:
        """Spend balance to raise one item's enhance level by 1.

        Returns ``(ok, code, new_level, balance_after)``.  ``code`` is
        ``ok`` / ``not_owned`` / ``max`` / ``insufficient`` / ``duplicate``.
        """
        now = int(time.time()) if now_ts is None else int(now_ts)
        cost = max(0, int(cost))
        with self._lock, self.conn:
            row = self.conn.execute(
                """SELECT enhance_level FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(item_id)),
            ).fetchone()
            if not row:
                return False, "not_owned", 0, self._balance_locked(
                    chat_id, sender_id)
            level = int(row[0])
            if level >= int(max_level):
                return False, "max", level, self._balance_locked(
                    chat_id, sender_id)

            balance = self._balance_locked(chat_id, sender_id)
            if balance < cost:
                return False, "insufficient", level, balance

            if cost > 0:
                cur = self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       SELECT ?, ?, ?, ?, 'enhance', 'spend', ?, ?, ?, ?
                       WHERE (SELECT COALESCE(SUM(points), 0)
                              FROM point_events
                              WHERE chat_id = ? AND sender_id = ?) >= ?""",
                    (str(chat_id), str(sender_id), str(sender_name or ""),
                     str(message_id), -cost, local_day(now),
                     local_season(now), now, str(chat_id), str(sender_id),
                     cost),
                )
                if cur.rowcount == 0:
                    return False, "duplicate", level, self._balance_locked(
                        chat_id, sender_id)

            self.conn.execute(
                """UPDATE equipment_owned
                   SET enhance_level = enhance_level + 1
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(item_id)),
            )
            new_level = level + 1
            balance = self._balance_locked(chat_id, sender_id)
        return True, "ok", new_level, balance

    def _balance_locked(self, chat_id: str, sender_id: str) -> int:
        """Balance helper that assumes the caller already holds the lock."""
        row = self.conn.execute(
            """SELECT COALESCE(SUM(points), 0) FROM point_events
               WHERE chat_id = ? AND sender_id = ?""",
            (str(chat_id), str(sender_id)),
        ).fetchone()
        return int(row[0]) if row else 0

    # ── Shields ───────────────────────────────────────────────────

    def shield_expires_at(self, chat_id: str, sender_id: str,
                          now_ts: int | None = None) -> int:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock:
            row = self.conn.execute(
                """SELECT expires_at FROM duel_shields
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        if not row:
            return 0
        expires = int(row[0])
        return expires if expires > now else 0

    def buy_shield(self, chat_id: str, sender_id: str, sender_name: str,
                   price: int, duration_sec: int, max_sec: int,
                   now_ts: int | None = None) -> tuple[bool, str, int, int]:
        """Buy/extend a shield. Returns ``(ok, code, expiry, balance)``."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            balance_row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            balance = int(balance_row[0]) if balance_row else 0
            if balance < price:
                return False, "insufficient", 0, balance

            row = self.conn.execute(
                """SELECT expires_at FROM duel_shields
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            current = int(row[0]) if row else 0
            base = max(now, current)
            expires = min(base + int(duration_sec), now + int(max_sec))

            cur = self.conn.execute(
                """INSERT INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, 'buy_shield', 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0) FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"shield:{uuid.uuid4().hex}", -int(price),
                 local_day(now), local_season(now), now,
                 str(chat_id), str(sender_id), int(price)),
            )
            if cur.rowcount == 0:
                return False, "insufficient", 0, balance
            self.conn.execute(
                """INSERT INTO duel_shields (chat_id, sender_id, expires_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     expires_at = excluded.expires_at""",
                (str(chat_id), str(sender_id), expires),
            )
        return True, "ok", expires, balance - int(price)

    def cleanup_shields(self, now_ts: int | None = None) -> int:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                "DELETE FROM duel_shields WHERE expires_at < ?",
                (now - 7 * 86400,),
            )
        return cur.rowcount

    # ── Duels ─────────────────────────────────────────────────────

    def duel_attempts(self, chat_id: str, sender_id: str, day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM duels
                   WHERE chat_id = ? AND attacker_id = ? AND day = ?
                     AND shield_blocked = 0""",
                (str(chat_id), str(sender_id), day),
            ).fetchone()
        return int(row[0]) if row else 0

    def duel_stolen(self, chat_id: str, sender_id: str, day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points_moved), 0) FROM duels
                   WHERE chat_id = ? AND attacker_id = ? AND day = ?
                     AND points_moved > 0""",
                (str(chat_id), str(sender_id), day),
            ).fetchone()
        return int(row[0]) if row else 0

    def defender_received(self, chat_id: str, defender_id: str,
                          day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM duels
                   WHERE chat_id = ? AND defender_id = ? AND day = ?
                     AND shield_blocked = 0""",
                (str(chat_id), str(defender_id), day),
            ).fetchone()
        return int(row[0]) if row else 0

    def pair_duels(self, chat_id: str, attacker_id: str,
                   defender_id: str, day: str) -> int:
        """Number of duels between one attacker and one defender today."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM duels
                   WHERE chat_id = ? AND attacker_id = ? AND defender_id = ?
                     AND day = ? AND shield_blocked = 0""",
                (str(chat_id), str(attacker_id), str(defender_id), str(day)),
            ).fetchone()
        return int(row[0]) if row else 0

    def last_pair_at(self, chat_id: str, first: str, second: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(MAX(created_at), 0) FROM duels
                   WHERE chat_id = ? AND shield_blocked = 0
                     AND ((attacker_id = ? AND defender_id = ?)
                       OR (attacker_id = ? AND defender_id = ?))""",
                (str(chat_id), str(first), str(second),
                 str(second), str(first)),
            ).fetchone()
        return int(row[0]) if row else 0

    def last_loss_at(self, chat_id: str, attacker_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(MAX(created_at), 0) FROM duels
                   WHERE chat_id = ? AND attacker_id = ? AND attacker_win = 0
                     AND shield_blocked = 0""",
                (str(chat_id), str(attacker_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def record_duel(self, chat_id: str, message_id: str,
                    attacker_id: str, attacker_name: str,
                    defender_id: str, defender_name: str,
                    attacker_power: int, defender_power: int,
                    win_prob: float, attacker_win: bool,
                    points_moved: int, shield_blocked: bool = False,
                    now_ts: int | None = None) -> bool:
        """Persist a duel and its point transfer atomically."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        moved = max(0, int(points_moved))
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO duels
                   (chat_id, message_id, attacker_id, attacker_name,
                    defender_id, defender_name, attacker_power,
                    defender_power, win_prob, attacker_win, points_moved,
                    shield_blocked, created_at, day)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(message_id), str(attacker_id),
                 str(attacker_name or ""), str(defender_id),
                 str(defender_name or ""), int(attacker_power),
                 int(defender_power), float(win_prob),
                 1 if attacker_win else 0, moved,
                 1 if shield_blocked else 0, now, local_day(now)),
            )
            if cur.rowcount == 0:
                return False
            if attacker_win and moved > 0:
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       VALUES (?, ?, ?, ?, 'steal_in', 'transfer', ?, ?, ?, ?)""",
                    (str(chat_id), str(attacker_id),
                     str(attacker_name or ""), str(message_id), moved,
                     local_day(now), local_season(now), now),
                )
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       VALUES (?, ?, ?, ?, 'steal_out', 'transfer', ?, ?, ?, ?)""",
                    (str(chat_id), str(defender_id),
                     str(defender_name or ""), str(message_id), -moved,
                     local_day(now), local_season(now), now),
                )
        return True

    def recent_duels(self, chat_id: str, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT attacker_id, attacker_name, defender_id,
                          defender_name, attacker_power, defender_power,
                          win_prob, attacker_win, points_moved,
                          shield_blocked, created_at
                   FROM duels WHERE chat_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (str(chat_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def duel_record(self, chat_id: str, sender_id: str) -> dict:
        """Career duels initiated by one member (shields excluded)."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS total,
                          COALESCE(SUM(attacker_win), 0) AS wins,
                          COALESCE(SUM(points_moved), 0) AS stolen
                   FROM duels
                   WHERE chat_id = ? AND attacker_id = ?
                     AND shield_blocked = 0""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return dict(row) if row else {"total": 0, "wins": 0, "stolen": 0}

    def duel_leaderboard(self, chat_id: str, limit: int = 10) -> list[dict]:
        """Career win leaderboard for one group."""
        with self._lock:
            rows = self.conn.execute(
                """SELECT attacker_id, MAX(attacker_name) AS sender_name,
                          COUNT(*) AS total,
                          COALESCE(SUM(attacker_win), 0) AS wins,
                          COALESCE(SUM(points_moved), 0) AS stolen
                   FROM duels
                   WHERE chat_id = ? AND shield_blocked = 0
                   GROUP BY attacker_id
                   ORDER BY wins DESC, stolen DESC, attacker_id ASC
                   LIMIT ?""",
                (str(chat_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── 境界 (realm) ──────────────────────────────────────────────

    def realm_of(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT realm FROM member_realms
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def realm_leaderboard(self, chat_id: str, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT r.sender_id,
                          COALESCE(u.sender_name, r.sender_id) AS sender_name,
                          r.realm
                   FROM member_realms r
                   LEFT JOIN user_last_message u
                     ON u.chat_id = r.chat_id AND u.sender_id = r.sender_id
                   WHERE r.chat_id = ? AND r.realm > 0
                   ORDER BY r.realm DESC, r.updated_at ASC
                   LIMIT ?""",
                (str(chat_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def advance_realm(self, chat_id: str, sender_id: str,
                      sender_name: str, max_realm: int,
                      message_id: str,
                      now_ts: int | None = None,
                      ) -> tuple[bool, str, int]:
        """Advance one realm.  Points are never deducted.

        The caller checks the cumulative-point threshold.  A zero-point
        ledger row keyed by ``(message_id, 'breakthrough')`` keeps
        replayed messages idempotent.

        Returns ``(ok, code, new_realm)``.
        """
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            row = self.conn.execute(
                """SELECT realm FROM member_realms
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            realm = int(row[0]) if row else 0
            if realm >= int(max_realm):
                return False, "max", realm

            cur = self.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   VALUES (?, ?, ?, ?, 'breakthrough', 'spend', 0, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 str(message_id), local_day(now), local_season(now), now),
            )
            if cur.rowcount == 0:
                return False, "duplicate", realm

            new_realm = realm + 1
            self.conn.execute(
                """INSERT INTO member_realms
                   (chat_id, sender_id, realm, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     realm = excluded.realm,
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), new_realm, now),
            )
        return True, "ok", new_realm

    # ── Fishing ───────────────────────────────────────────────────

    def fishing_attempts_since(self, chat_id: str, sender_id: str,
                               since_ts: int) -> int:
        """Casts made by one member at or after *since_ts* (misses included).

        Used for the per-hour attempt limit: pass the start of the current
        local hour so the counter resets when the clock hour changes.
        """
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ? AND created_at >= ?""",
                (str(chat_id), str(sender_id), int(since_ts)),
            ).fetchone()
        return int(row[0]) if row else 0

    def fishing_last_cast_at(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(MAX(created_at), 0) FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def fishing_earned_on_day(self, chat_id: str, sender_id: str,
                              day: str) -> int:
        """Balance gained from fishing today (category='fish')."""
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ? AND day = ?
                     AND category = 'fish'""",
                (str(chat_id), str(sender_id), str(day)),
            ).fetchone()
        return int(row[0]) if row else 0

    def fishing_totals(self, chat_id: str, sender_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) AS catches,
                          COALESCE(SUM(amount), 0) AS value,
                          COALESCE(MAX(weight_g), 0) AS biggest
                   FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ?
                     AND species_id != ''""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return dict(row) if row else {"catches": 0, "value": 0, "biggest": 0}

    def fishing_inventory(self, chat_id: str,
                          sender_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT kind, item_id, quantity, equipped, purchased_at
                   FROM fishing_inventory
                   WHERE chat_id = ? AND sender_id = ?
                   ORDER BY kind ASC, item_id ASC""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def fishing_equipped_rod(self, chat_id: str, sender_id: str) -> str:
        with self._lock:
            row = self.conn.execute(
                """SELECT item_id FROM fishing_inventory
                   WHERE chat_id = ? AND sender_id = ?
                     AND kind = 'rod' AND equipped = 1 LIMIT 1""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return str(row[0]) if row else ""

    def owns_fishing_item(self, chat_id: str, sender_id: str,
                          kind: str, item_id: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM fishing_inventory
                   WHERE chat_id = ? AND sender_id = ?
                     AND kind = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), str(kind), str(item_id)),
            ).fetchone()
        return row is not None

    def buy_fishing_rod(self, chat_id: str, sender_id: str,
                        sender_name: str, rod: dict,
                        now_ts: int | None = None,
                        ) -> tuple[bool, str, int]:
        """Buy and auto-equip a rod. Returns ``(ok, code, balance)``."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        price = int(rod["price"])
        rod_id = str(rod["id"])
        with self._lock, self.conn:
            balance_row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            balance = int(balance_row[0]) if balance_row else 0
            if balance < price:
                return False, "insufficient", balance

            owned = self.conn.execute(
                """SELECT 1 FROM fishing_inventory
                   WHERE chat_id = ? AND sender_id = ?
                     AND kind = 'rod' AND item_id = ?""",
                (str(chat_id), str(sender_id), rod_id),
            ).fetchone()
            if owned:
                return False, "owned", balance

            if price > 0:
                cur = self.conn.execute(
                    """INSERT INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       SELECT ?, ?, ?, ?, 'buy_rod', 'spend', ?, ?, ?, ?
                       WHERE (SELECT COALESCE(SUM(points), 0)
                              FROM point_events
                              WHERE chat_id = ? AND sender_id = ?) >= ?""",
                    (str(chat_id), str(sender_id), str(sender_name or ""),
                     f"rod:{uuid.uuid4().hex}", -price,
                     local_day(now), local_season(now), now,
                     str(chat_id), str(sender_id), price),
                )
                if cur.rowcount == 0:
                    return False, "insufficient", balance

            self.conn.execute(
                """UPDATE fishing_inventory SET equipped = 0
                   WHERE chat_id = ? AND sender_id = ? AND kind = 'rod'""",
                (str(chat_id), str(sender_id)),
            )
            self.conn.execute(
                """INSERT INTO fishing_inventory
                   (chat_id, sender_id, kind, item_id, quantity, equipped,
                    purchased_at)
                   VALUES (?, ?, 'rod', ?, 1, 1, ?)
                   ON CONFLICT(chat_id, sender_id, kind, item_id)
                   DO UPDATE SET quantity = 1, equipped = 1,
                                 purchased_at = excluded.purchased_at""",
                (str(chat_id), str(sender_id), rod_id, now),
            )
        return True, "ok", balance - price

    def buy_fishing_bait(self, chat_id: str, sender_id: str,
                         sender_name: str, bait: dict, quantity: int = 1,
                         now_ts: int | None = None,
                         ) -> tuple[bool, str, int]:
        """Buy a bait stack. Returns ``(ok, code, balance)``."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        quantity = max(1, int(quantity))
        total = int(bait["price"]) * quantity
        bait_id = str(bait["id"])
        with self._lock, self.conn:
            balance_row = self.conn.execute(
                """SELECT COALESCE(SUM(points), 0) FROM point_events
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            balance = int(balance_row[0]) if balance_row else 0
            if balance < total:
                return False, "insufficient", balance

            cur = self.conn.execute(
                """INSERT INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, 'buy_bait', 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0) FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 f"bait:{uuid.uuid4().hex}", -total,
                 local_day(now), local_season(now), now,
                 str(chat_id), str(sender_id), total),
            )
            if cur.rowcount == 0:
                return False, "insufficient", balance

            self.conn.execute(
                """INSERT INTO fishing_inventory
                   (chat_id, sender_id, kind, item_id, quantity, equipped,
                    purchased_at)
                   VALUES (?, ?, 'bait', ?, ?, 0, ?)
                   ON CONFLICT(chat_id, sender_id, kind, item_id)
                   DO UPDATE SET quantity = quantity + excluded.quantity""",
                (str(chat_id), str(sender_id), bait_id, quantity, now),
            )
        return True, "ok", balance - total

    def equip_fishing_rod(self, chat_id: str, sender_id: str,
                          rod_id: str) -> bool:
        """Equip an owned rod; an empty id resets to the default rod."""
        with self._lock, self.conn:
            if rod_id:
                owned = self.conn.execute(
                    """SELECT 1 FROM fishing_inventory
                       WHERE chat_id = ? AND sender_id = ?
                         AND kind = 'rod' AND item_id = ?""",
                    (str(chat_id), str(sender_id), str(rod_id)),
                ).fetchone()
                if not owned:
                    return False
            self.conn.execute(
                """UPDATE fishing_inventory SET equipped = 0
                   WHERE chat_id = ? AND sender_id = ? AND kind = 'rod'""",
                (str(chat_id), str(sender_id)),
            )
            if rod_id:
                self.conn.execute(
                    """UPDATE fishing_inventory SET equipped = 1
                       WHERE chat_id = ? AND sender_id = ?
                         AND kind = 'rod' AND item_id = ?""",
                    (str(chat_id), str(sender_id), str(rod_id)),
                )
        return True

    def commit_fishing_cast(self, *, chat_id: str, sender_id: str,
                            sender_name: str, message_id: str,
                            cast_index: int, rod_id: str, bait_id: str,
                            species_id: str, species_name: str,
                            rarity: str, weight_g: int, amount: int,
                            day: str, now_ts: int) -> tuple[bool, str]:
        """Persist one cast + its ledger row + bait consumption atomically.

        Returns ``(inserted, bait_actually_consumed)``.  ``inserted`` is
        False when the same ``(message_id, cast_index)`` was already
        recorded, making command replay harmless.
        """
        now = int(now_ts)
        amount = max(0, int(amount))
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO fishing_casts
                   (chat_id, sender_id, sender_name, message_id, cast_index,
                    rod_id, bait_id, species_id, species_name, rarity,
                    weight_g, amount, day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 str(message_id), int(cast_index), str(rod_id),
                 str(bait_id), str(species_id), str(species_name),
                 str(rarity), int(weight_g), amount, str(day), now),
            )
            if cur.rowcount == 0:
                return False, ""

            bait_used = ""
            if bait_id:
                row = self.conn.execute(
                    """SELECT quantity FROM fishing_inventory
                       WHERE chat_id = ? AND sender_id = ?
                         AND kind = 'bait' AND item_id = ?""",
                    (str(chat_id), str(sender_id), str(bait_id)),
                ).fetchone()
                if row and int(row[0]) > 0:
                    self.conn.execute(
                        """UPDATE fishing_inventory
                           SET quantity = quantity - 1
                           WHERE chat_id = ? AND sender_id = ?
                             AND kind = 'bait' AND item_id = ?""",
                        (str(chat_id), str(sender_id), str(bait_id)),
                    )
                    bait_used = str(bait_id)

            if amount > 0:
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       VALUES (?, ?, ?, ?, 'fish_catch', 'fish', ?, ?, ?, ?)""",
                    (str(chat_id), str(sender_id), str(sender_name or ""),
                     f"fish:{message_id}:{int(cast_index)}", amount,
                     str(day), local_season(now), now),
                )
        return True, bait_used

    def fishing_recent(self, chat_id: str, sender_id: str = "",
                       limit: int = 20) -> list[dict]:
        where = "chat_id = ? AND species_id != ''"
        params: list = [str(chat_id)]
        if sender_id:
            where += " AND sender_id = ?"
            params.append(str(sender_id))
        params.append(int(limit))
        with self._lock:
            rows = self.conn.execute(
                f"""SELECT sender_id, sender_name, species_id,
                           species_name, rarity, weight_g, amount, rod_id,
                           bait_id, created_at
                    FROM fishing_casts
                    WHERE {where}
                    ORDER BY created_at DESC, id DESC LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def fishing_board(self, chat_id: str, scope: str = "value",
                      day: str | None = None,
                      limit: int = 10) -> list[dict]:
        """Leaderboard rows: total value, catch count or biggest fish."""
        params: list = [str(chat_id)]
        where = "chat_id = ? AND species_id != ''"
        if day:
            where += " AND day = ?"
            params.append(str(day))

        if scope == "biggest":
            params.append(int(limit))
            with self._lock:
                rows = self.conn.execute(
                    f"""SELECT sender_id, sender_name, species_name, rarity,
                               weight_g, amount, created_at
                        FROM fishing_casts
                        WHERE {where}
                        ORDER BY weight_g DESC, created_at DESC LIMIT ?""",
                    params,
                ).fetchall()
            return [dict(row) for row in rows]

        order = "catches DESC" if scope == "count" else "value DESC"
        params.append(int(limit))
        with self._lock:
            rows = self.conn.execute(
                f"""SELECT sender_id, MAX(sender_name) AS sender_name,
                           COALESCE(SUM(amount), 0) AS value,
                           COUNT(*) AS catches,
                           COALESCE(MAX(weight_g), 0) AS biggest
                    FROM fishing_casts
                    WHERE {where}
                    GROUP BY sender_id
                    ORDER BY {order}, biggest DESC, sender_id ASC LIMIT ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def fishing_collection(self, chat_id: str,
                           sender_id: str) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT DISTINCT species_id FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ?
                     AND species_id != ''""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def has_rarity_catch(self, chat_id: str, sender_id: str,
                         rarity: str) -> bool:
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM fishing_casts
                   WHERE chat_id = ? AND sender_id = ? AND rarity = ?
                     AND species_id != '' LIMIT 1""",
                (str(chat_id), str(sender_id), str(rarity)),
            ).fetchone()
        return row is not None

    # ── World boss ────────────────────────────────────────────────

    def boss_state(self, chat_id: str, season: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                """SELECT chat_id, season, boss_name, hp_total, hp_left,
                          killed_at, created_at
                   FROM boss_state WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
        return dict(row) if row else None

    def ensure_boss_state(self, chat_id: str, season: str,
                          boss_name: str, hp_total: int,
                          now_ts: int) -> dict:
        """Insert the week's boss row once, then return it."""
        hp_total = max(1, int(hp_total))
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO boss_state
                   (chat_id, season, boss_name, hp_total, hp_left,
                    killed_at, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (str(chat_id), str(season), str(boss_name), hp_total,
                 hp_total, int(now_ts)),
            )
            row = self.conn.execute(
                """SELECT chat_id, season, boss_name, hp_total, hp_left,
                          killed_at, created_at
                   FROM boss_state WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
        return dict(row)

    def boss_attempts(self, chat_id: str, season: str, sender_id: str,
                      day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM boss_attacks
                   WHERE chat_id = ? AND season = ? AND sender_id = ?
                     AND day = ?""",
                (str(chat_id), str(season), str(sender_id), str(day)),
            ).fetchone()
        return int(row[0]) if row else 0

    def boss_energy_of(self, chat_id: str, season: str,
                       sender_id: str) -> tuple[int, int] | None:
        """Stored boss charges for one member: ``(attempts, regen_at)``."""
        with self._lock:
            row = self.conn.execute(
                """SELECT attempts, regen_at FROM boss_energy
                   WHERE chat_id = ? AND season = ? AND sender_id = ?""",
                (str(chat_id), str(season), str(sender_id)),
            ).fetchone()
        if row is None:
            return None
        return int(row[0]), int(row[1])

    def save_boss_energy(self, chat_id: str, season: str,
                         sender_id: str, attempts: int,
                         regen_at: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO boss_energy
                   (chat_id, season, sender_id, attempts, regen_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_id, season, sender_id) DO UPDATE SET
                     attempts = excluded.attempts,
                     regen_at = excluded.regen_at""",
                (str(chat_id), str(season), str(sender_id),
                 int(attempts), int(regen_at)),
            )

    def boss_attack_recorded(self, chat_id: str, season: str,
                             message_id: str) -> bool:
        """True when this exact message already dealt boss damage."""
        if not message_id:
            return False
        with self._lock:
            row = self.conn.execute(
                """SELECT 1 FROM boss_attacks
                   WHERE chat_id = ? AND season = ? AND message_id = ?
                   LIMIT 1""",
                (str(chat_id), str(season), str(message_id)),
            ).fetchone()
        return row is not None

    def boss_attack(self, chat_id: str, season: str, message_id: str,
                    sender_id: str, sender_name: str, damage: int,
                    crit: bool, day: str,
                    now_ts: int) -> tuple[bool, int, int]:
        """Record one hit and reduce boss HP atomically.

        Returns ``(inserted, hp_left, damage)``.
        """
        damage = max(0, int(damage))
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO boss_attacks
                   (chat_id, season, message_id, sender_id, sender_name,
                    damage, crit, day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(season), str(message_id),
                 str(sender_id), str(sender_name or ""), damage,
                 1 if crit else 0, str(day), int(now_ts)),
            )
            if cur.rowcount == 0:
                row = self.conn.execute(
                    """SELECT hp_left FROM boss_state
                       WHERE chat_id = ? AND season = ?""",
                    (str(chat_id), str(season)),
                ).fetchone()
                return False, int(row[0]) if row else 0, 0

            self.conn.execute(
                """UPDATE boss_state
                   SET hp_left = MAX(0, hp_left - ?)
                   WHERE chat_id = ? AND season = ?""",
                (damage, str(chat_id), str(season)),
            )
            row = self.conn.execute(
                """SELECT hp_left FROM boss_state
                   WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
        return True, int(row[0]) if row else 0, damage

    def boss_damage_board(self, chat_id: str, season: str,
                          limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT sender_id, MAX(sender_name) AS sender_name,
                          COALESCE(SUM(damage), 0) AS damage,
                          COUNT(*) AS attacks,
                          COALESCE(MAX(crit), 0) AS crits
                   FROM boss_attacks
                   WHERE chat_id = ? AND season = ?
                   GROUP BY sender_id
                   ORDER BY damage DESC, sender_id ASC
                   LIMIT ?""",
                (str(chat_id), str(season), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def boss_totals(self, chat_id: str, season: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(damage), 0) AS damage,
                          COUNT(*) AS attacks,
                          COUNT(DISTINCT sender_id) AS participants
                   FROM boss_attacks
                   WHERE chat_id = ? AND season = ?""",
                (str(chat_id), str(season)),
            ).fetchone()
        return dict(row) if row else {
            "damage": 0, "attacks": 0, "participants": 0}

    def boss_member_damage(self, chat_id: str, season: str,
                           sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(damage), 0) FROM boss_attacks
                   WHERE chat_id = ? AND season = ? AND sender_id = ?""",
                (str(chat_id), str(season), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def boss_total_damage(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COALESCE(SUM(damage), 0) FROM boss_attacks
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def boss_kill(self, chat_id: str, season: str, now_ts: int) -> bool:
        """Mark the boss dead once.  True means this call killed it."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                """UPDATE boss_state SET killed_at = ?
                   WHERE chat_id = ? AND season = ? AND killed_at = 0""",
                (int(now_ts), str(chat_id), str(season)),
            )
        return cur.rowcount > 0

    def boss_distribute_rewards(self, chat_id: str, season: str,
                                pool: int, now_ts: int) -> list[dict]:
        """Split *pool* balance across participants by damage share.

        Rewards are ledger rows with ``category='boss'`` (balance only).
        """
        pool = max(0, int(pool))
        rows = self.boss_damage_board(
            chat_id, season, limit=1_000_000)
        total_damage = sum(int(row["damage"]) for row in rows)
        if pool <= 0 or total_damage <= 0 or not rows:
            return []

        remaining = pool
        rewards: list[dict] = []
        with self._lock, self.conn:
            for index, row in enumerate(rows):
                if remaining <= 0:
                    break
                if index == len(rows) - 1:
                    share = remaining
                else:
                    share = max(1, int(
                        row["damage"] * pool / total_damage))
                    share = min(share, remaining)
                remaining -= share
                if share <= 0:
                    continue
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       VALUES (?, ?, ?, ?, 'boss_reward', 'boss', ?, ?, ?, ?)""",
                    (str(chat_id), str(row["sender_id"]),
                     str(row.get("sender_name") or ""),
                     f"bossreward:{chat_id}:{season}:{row['sender_id']}",
                     share, local_day(now_ts), local_season(now_ts),
                     int(now_ts)),
                )
                rewards.append({
                    "sender_id": row["sender_id"],
                    "sender_name": row.get("sender_name")
                    or row["sender_id"],
                    "damage": int(row["damage"]),
                    "amount": share,
                })
        return rewards

    # ── Achievements / titles ─────────────────────────────────────

    def equipped_title(self, chat_id: str, sender_id: str) -> str:
        with self._lock:
            row = self.conn.execute(
                """SELECT title FROM member_profiles
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return str(row[0] or "") if row else ""

    def set_equipped_title(self, chat_id: str, sender_id: str,
                           title: str, now_ts: int | None = None) -> None:
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT INTO member_profiles
                   (chat_id, sender_id, title, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     title = excluded.title,
                     updated_at = excluded.updated_at""",
                (str(chat_id), str(sender_id), str(title or ""), now),
            )

    def achievements_of(self, chat_id: str, sender_id: str) -> set[str]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT achievement_id FROM achievements
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchall()
        return {str(row[0]) for row in rows}

    def unlock_achievement(self, chat_id: str, sender_id: str,
                           achievement_id: str, reward: int,
                           now_ts: int | None = None) -> bool:
        """Record an unlock once and credit its one-time balance reward."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        with self._lock, self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO achievements
                   (chat_id, sender_id, achievement_id, unlocked_at)
                   VALUES (?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(achievement_id), now),
            )
            if cur.rowcount == 0:
                return False
            reward = max(0, int(reward))
            if reward:
                self.conn.execute(
                    """INSERT OR IGNORE INTO point_events
                       (chat_id, sender_id, sender_name, message_id, reason,
                        category, points, day, season, created_at)
                       SELECT ?, ?, COALESCE(
                                  (SELECT sender_name
                                   FROM user_last_message
                                   WHERE chat_id = ? AND sender_id = ?), ''),
                              ?, 'achievement', 'achievement', ?, ?, ?, ?""",
                    (str(chat_id), str(sender_id), str(chat_id),
                     str(sender_id),
                     f"achv:{chat_id}:{sender_id}:{achievement_id}",
                     reward, local_day(now), local_season(now), now),
                )
        return True

    def equipment_count(self, chat_id: str, sender_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def streak_days(self, chat_id: str, sender_id: str,
                    now_ts: int | None = None) -> int:
        """Public wrapper around the private streak calculation."""
        now = int(time.time()) if now_ts is None else int(now_ts)
        return self._streak_days(chat_id, sender_id, now)

    # ── Daily lottery ─────────────────────────────────────────────

    def lottery_draws_today(self, chat_id: str, sender_id: str,
                            day: str) -> int:
        with self._lock:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM lottery_draws
                   WHERE chat_id = ? AND sender_id = ? AND day = ?""",
                (str(chat_id), str(sender_id), str(day)),
            ).fetchone()
        return int(row[0]) if row else 0

    def lottery_history(self, chat_id: str, sender_id: str,
                        limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT reward_kind, reward_name, amount, day, created_at
                   FROM lottery_draws
                   WHERE chat_id = ? AND sender_id = ?
                   ORDER BY created_at DESC, id DESC LIMIT ?""",
                (str(chat_id), str(sender_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _lottery_event_locked(self, chat_id: str, sender_id: str,
                              sender_name: str, message_id: str,
                              reason: str, category: str, points: int,
                              now: int) -> None:
        self.conn.execute(
            """INSERT OR IGNORE INTO point_events
               (chat_id, sender_id, sender_name, message_id, reason,
                category, points, day, season, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(chat_id), str(sender_id), str(sender_name or ""),
             str(message_id), str(reason), str(category), int(points),
             local_day(now), local_season(now), int(now)),
        )

    def _grant_lottery_reward_locked(self, chat_id: str, sender_id: str,
                                     sender_name: str, message_id: str,
                                     reward: dict, now: int) -> str:
        """Apply one lottery reward inside an open transaction."""
        kind = str(reward.get("kind") or "")
        name = str(reward.get("name") or "")

        if kind == "balance":
            amount = max(1, int(reward.get("amount", 0) or 0))
            self._lottery_event_locked(
                chat_id, sender_id, sender_name,
                f"{message_id}:balance", "lottery_reward", "lottery",
                amount, now,
            )
            return f"灵石 +{amount}"

        if kind == "points":
            amount = max(1, int(reward.get("amount", 0) or 0))
            # 'adjust' counts toward earned / level / boards but not the
            # daily chat-point cap.
            self._lottery_event_locked(
                chat_id, sender_id, sender_name,
                f"{message_id}:points", "lottery_points", "adjust",
                amount, now,
            )
            return f"修炼值 +{amount}"

        if kind == "equipment":
            item_id = str(reward.get("item_id") or "")
            price = max(0, int(reward.get("price", 0) or 0))
            owned = self.conn.execute(
                """SELECT 1 FROM equipment_owned
                   WHERE chat_id = ? AND sender_id = ? AND item_id = ?""",
                (str(chat_id), str(sender_id), item_id),
            ).fetchone()
            if owned:
                refund = max(1, price // 2)
                self._lottery_event_locked(
                    chat_id, sender_id, sender_name,
                    f"{message_id}:refund", "lottery_refund", "lottery",
                    refund, now,
                )
                return f"已拥有，折算 {refund} 灵石"

            same_slot_ids = [
                str(i) for i in (reward.get("same_slot_ids") or []) if i
            ]
            equipped = 1
            if same_slot_ids:
                placeholders = ",".join("?" for _ in same_slot_ids)
                row = self.conn.execute(
                    f"""SELECT 1 FROM equipment_owned
                        WHERE chat_id = ? AND sender_id = ? AND equipped = 1
                          AND item_id IN ({placeholders}) LIMIT 1""",
                    [str(chat_id), str(sender_id), *same_slot_ids],
                ).fetchone()
                equipped = 0 if row else 1
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO equipment_owned
                   (chat_id, sender_id, item_id, purchased_at, equipped,
                    enhance_level)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (str(chat_id), str(sender_id), item_id, int(now),
                 equipped),
            )
            if cur.rowcount == 0:
                refund = max(1, price // 2)
                self._lottery_event_locked(
                    chat_id, sender_id, sender_name,
                    f"{message_id}:refund", "lottery_refund", "lottery",
                    refund, now,
                )
                return f"已拥有，折算 {refund} 灵石"
            return "已自动装备" if equipped else "已放入背包"

        if kind == "rod":
            rod_id = str(reward.get("rod_id") or "")
            price = max(0, int(reward.get("price", 0) or 0))
            owned = self.conn.execute(
                """SELECT 1 FROM fishing_inventory
                   WHERE chat_id = ? AND sender_id = ?
                     AND kind = 'rod' AND item_id = ?""",
                (str(chat_id), str(sender_id), rod_id),
            ).fetchone()
            if owned:
                refund = max(1, price // 2)
                self._lottery_event_locked(
                    chat_id, sender_id, sender_name,
                    f"{message_id}:refund", "lottery_refund", "lottery",
                    refund, now,
                )
                return f"已拥有，折算 {refund} 灵石"
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO fishing_inventory
                   (chat_id, sender_id, kind, item_id, quantity, equipped,
                    purchased_at)
                   VALUES (?, ?, 'rod', ?, 1, 0, ?)""",
                (str(chat_id), str(sender_id), rod_id, int(now)),
            )
            if cur.rowcount == 0:
                refund = max(1, price // 2)
                self._lottery_event_locked(
                    chat_id, sender_id, sender_name,
                    f"{message_id}:refund", "lottery_refund", "lottery",
                    refund, now,
                )
                return f"已拥有，折算 {refund} 灵石"
            return "已放入鱼竿背包，发「换鱼竿」切换"

        if kind == "bait":
            bait_id = str(reward.get("bait_id") or "")
            quantity = max(1, int(reward.get("quantity", 1) or 1))
            self.conn.execute(
                """INSERT INTO fishing_inventory
                   (chat_id, sender_id, kind, item_id, quantity, equipped,
                    purchased_at)
                   VALUES (?, ?, 'bait', ?, ?, 0, ?)
                   ON CONFLICT(chat_id, sender_id, kind, item_id)
                   DO UPDATE SET quantity = quantity + excluded.quantity""",
                (str(chat_id), str(sender_id), bait_id, quantity,
                 int(now)),
            )
            return f"{name or '鱼饵'} 已入账"

        if kind == "shield":
            duration = max(0, int(reward.get("duration_sec", 0) or 0))
            maximum = max(duration, int(
                reward.get("max_sec", duration) or duration))
            row = self.conn.execute(
                """SELECT expires_at FROM duel_shields
                   WHERE chat_id = ? AND sender_id = ?""",
                (str(chat_id), str(sender_id)),
            ).fetchone()
            current = int(row[0]) if row else 0
            base = max(int(now), current)
            expires = max(
                current,
                min(base + duration, int(now) + maximum),
            )
            self.conn.execute(
                """INSERT INTO duel_shields (chat_id, sender_id, expires_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(chat_id, sender_id) DO UPDATE SET
                     expires_at = excluded.expires_at""",
                (str(chat_id), str(sender_id), expires),
            )
            return "免战时长已到账"

        return ""

    def commit_lottery_draw(self, *, chat_id: str, sender_id: str,
                            sender_name: str, message_id: str,
                            day: str, now_ts: int, cost: int,
                            limit: int, reward: dict) -> dict:
        """Charge one ticket and apply a lottery reward atomically.

        Returns a result dict with ``ok/code/used/limit/balance/detail``.
        ``code`` is ``ok`` / ``limit`` / ``insufficient`` / ``duplicate``.
        """
        now = int(now_ts)
        cost = max(0, int(cost))
        with self._lock, self.conn:
            used = self.conn.execute(
                """SELECT COUNT(*) FROM lottery_draws
                   WHERE chat_id = ? AND sender_id = ? AND day = ?""",
                (str(chat_id), str(sender_id), str(day)),
            ).fetchone()
            used = int(used[0]) if used else 0
            if used >= int(limit):
                return {"ok": False, "code": "limit", "used": used,
                        "limit": int(limit)}

            balance = self._balance_locked(chat_id, sender_id)
            if balance < cost:
                return {"ok": False, "code": "insufficient",
                        "used": used, "limit": int(limit),
                        "balance": balance}

            cur = self.conn.execute(
                """INSERT OR IGNORE INTO point_events
                   (chat_id, sender_id, sender_name, message_id, reason,
                    category, points, day, season, created_at)
                   SELECT ?, ?, ?, ?, 'lottery_ticket', 'spend', ?, ?, ?, ?
                   WHERE (SELECT COALESCE(SUM(points), 0)
                          FROM point_events
                          WHERE chat_id = ? AND sender_id = ?) >= ?""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 str(message_id), -cost, str(day), local_season(now), now,
                 str(chat_id), str(sender_id), cost),
            )
            if cur.rowcount == 0:
                return {"ok": False, "code": "duplicate", "used": used,
                        "limit": int(limit),
                        "balance": self._balance_locked(
                            chat_id, sender_id)}

            detail = self._grant_lottery_reward_locked(
                chat_id, sender_id, sender_name, message_id, reward, now,
            )
            self.conn.execute(
                """INSERT OR IGNORE INTO lottery_draws
                   (chat_id, sender_id, sender_name, message_id,
                    reward_kind, reward_name, amount, day, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(chat_id), str(sender_id), str(sender_name or ""),
                 str(message_id), str(reward.get("kind") or ""),
                 str(reward.get("name") or ""),
                 int(reward.get("amount", 0) or 0), str(day), now),
            )
            balance = self._balance_locked(chat_id, sender_id)
        return {"ok": True, "code": "ok", "used": used + 1,
                "limit": int(limit), "balance": balance, "detail": detail}

    # ── Maintenance / backfill ────────────────────────────────────

    def delete_earn_events(self, chat_id: str) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                """DELETE FROM point_events
                   WHERE chat_id = ? AND category = 'earn'""",
                (str(chat_id),),
            )
        return cur.rowcount

    def messages_since(self, chat_id: str, since_ts: int) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                """SELECT message_id, chat_id, sender_id, sender_name,
                          content, msg_type, timestamp
                   FROM messages
                   WHERE chat_id = ? AND timestamp >= ?
                   ORDER BY timestamp ASC, id ASC""",
                (str(chat_id), int(since_ts)),
            ).fetchall()
        return [dict(row) for row in rows]
