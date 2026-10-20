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
                """SELECT item_id, purchased_at, equipped
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
