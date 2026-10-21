"""PointStore tests — ledger integrity, boards, shop, shields, duels."""

import unittest

from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.util import local_day, local_season


def _award(sender, reason="base", points=1, category="earn", name=""):
    return PointAward(sender, name or sender, reason, points, category)


class PointStoreTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.chat = "123@chatroom"

    def tearDown(self):
        self.conn.close()

    def add_member(self, sender_id, name, ts=1000):
        self.messages.insert_message({
            "message_id": f"seed-{sender_id}-{ts}",
            "chat_id": self.chat,
            "sender_id": sender_id,
            "sender_name": name,
            "content": "seed",
            "msg_type": 1,
            "timestamp": ts,
        })


class LedgerTests(PointStoreTestBase):
    def test_record_is_idempotent(self):
        awards = [_award("wxid_a"), _award("wxid_a", "length")]
        self.assertEqual(
            self.store.record_events(self.chat, "m1", awards), 2,
        )
        self.assertEqual(
            self.store.record_events(self.chat, "m1", awards), 0,
        )
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 2)

    def test_earned_ignores_transfers_and_spending(self):
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=10)])
        self.store.record_events(self.chat, "m2", [
            _award("wxid_a", "steal_in", 5, "transfer"),
            _award("wxid_a", "buy_item", -7, "spend"),
        ])
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 10)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 8)

    def test_adjust_changes_earned_and_balance(self):
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=10)])
        self.store.adjust(self.chat, "wxid_a", "张三", +5, "奖励")
        self.store.adjust(self.chat, "wxid_a", "张三", -3, "扣分")
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 12)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 12)


class BoardTests(PointStoreTestBase):
    def test_total_board_order_and_ties(self):
        self.store.record_events(self.chat, "m1", [_award("wxid_b", points=5)])
        self.store.record_events(self.chat, "m2", [_award("wxid_a", points=5)])
        self.store.record_events(self.chat, "m3", [_award("wxid_c", points=9)])
        rows = self.store.board(self.chat, "total", 10)
        self.assertEqual([r["sender_id"] for r in rows],
                         ["wxid_c", "wxid_a", "wxid_b"])
        self.assertEqual([r["rank"] for r in rows], [1, 2, 2])

    def test_season_and_day_filters(self):
        self.store.record_events(
            self.chat, "old", [_award("wxid_a", points=7)],
            now_ts=1_600_000_000,
        )
        self.store.record_events(
            self.chat, "new", [_award("wxid_a", points=3)],
        )
        today = self.store.board(self.chat, "day", 10)
        self.assertEqual(today[0]["points"], 3)
        season = self.store.board(self.chat, "season", 10)
        self.assertEqual(season[0]["points"], 3)
        total = self.store.board(self.chat, "total", 10)
        self.assertEqual(total[0]["points"], 10)

    def test_member_stats_ranks_and_streak(self):
        self.add_member("wxid_a", "张三")
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=4)])
        self.store.record_events(self.chat, "m2", [_award("wxid_b", points=8)])
        stats = self.store.member_stats(self.chat, "wxid_a")
        self.assertEqual(stats.earned, 4)
        self.assertEqual(stats.total_rank, 2)
        self.assertEqual(stats.streak_days, 1)

    def test_participants_and_day_total(self):
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=4)])
        self.store.record_events(self.chat, "m2", [_award("wxid_b", points=6)])
        self.assertEqual(self.store.participants(self.chat), 2)
        self.assertEqual(
            self.store.day_total(self.chat, local_day()),
            10,
        )


class SeasonTests(PointStoreTestBase):
    def test_close_season_records_winner_once(self):
        season = local_season()
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=9)])
        self.store.record_events(self.chat, "m2", [_award("wxid_b", points=4)])
        winner = self.store.close_season(self.chat, season)
        self.assertEqual(winner["winner_id"], "wxid_a")
        self.assertEqual(winner["winner_points"], 9)
        # Second call must not change the recorded winner.
        self.store.record_events(self.chat, "m3", [_award("wxid_b", points=50)])
        again = self.store.close_season(self.chat, season)
        self.assertEqual(again["winner_id"], "wxid_a")

    def test_close_empty_season_returns_none(self):
        self.assertIsNone(self.store.close_season(self.chat, "1999-01"))


class EquipmentTests(PointStoreTestBase):
    def setUp(self):
        super().setUp()
        self.store.record_events(
            self.chat, "earn1", [_award("wxid_a", points=100)],
        )

    def test_buy_deducts_balance_but_not_earned(self):
        item = {"id": "iron_sword", "name": "铁剑", "slot": "weapon",
                "power": 9, "price": 90}
        ok, code, balance, equipped = self.store.buy_item(
            self.chat, "wxid_a", "张三", item, ["iron_sword"],
        )
        self.assertTrue(ok)
        self.assertEqual(code, "ok")
        self.assertEqual(balance, 10)
        self.assertTrue(equipped)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 100)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 10)

    def test_buy_insufficient(self):
        item = {"id": "dragon_sword", "name": "龙纹剑", "slot": "weapon",
                "power": 24, "price": 480}
        ok, code, balance, _ = self.store.buy_item(
            self.chat, "wxid_a", "张三", item, ["dragon_sword"],
        )
        self.assertFalse(ok)
        self.assertEqual(code, "insufficient")
        self.assertEqual(balance, 100)

    def test_cannot_buy_twice(self):
        item = {"id": "wood_sword", "name": "木剑", "slot": "weapon",
                "power": 4, "price": 30}
        self.store.buy_item(self.chat, "wxid_a", "张三", item, ["wood_sword"])
        ok, code, _, _ = self.store.buy_item(
            self.chat, "wxid_a", "张三", item, ["wood_sword"],
        )
        self.assertFalse(ok)
        self.assertEqual(code, "owned")

    def test_second_item_goes_to_bag_and_equip_swaps(self):
        sword = {"id": "wood_sword", "name": "木剑", "slot": "weapon",
                 "power": 4, "price": 30}
        blade = {"id": "black_blade", "name": "玄铁刀", "slot": "weapon",
                 "power": 16, "price": 60}
        same_slot = ["wood_sword", "black_blade"]
        self.store.buy_item(self.chat, "wxid_a", "张三", sword, same_slot)
        _, _, _, equipped = self.store.buy_item(
            self.chat, "wxid_a", "张三", blade, same_slot,
        )
        self.assertFalse(equipped)
        owned = {row["item_id"]: row for row in
                 self.store.owned_items(self.chat, "wxid_a")}
        self.assertEqual(owned["wood_sword"]["equipped"], 1)
        self.assertEqual(owned["black_blade"]["equipped"], 0)

        self.store.equip_item(
            self.chat, "wxid_a", "black_blade", same_slot, equip=True,
        )
        owned = {row["item_id"]: row for row in
                 self.store.owned_items(self.chat, "wxid_a")}
        self.assertEqual(owned["wood_sword"]["equipped"], 0)
        self.assertEqual(owned["black_blade"]["equipped"], 1)

    def test_equip_requires_ownership(self):
        self.assertFalse(self.store.equip_item(
            self.chat, "wxid_a", "iron_sword", ["iron_sword"], equip=True,
        ))


class ShieldTests(PointStoreTestBase):
    def setUp(self):
        super().setUp()
        self.store.record_events(
            self.chat, "earn1", [_award("wxid_a", points=50)],
        )

    def test_buy_shield_deducts_and_sets_expiry(self):
        ok, code, expires, balance = self.store.buy_shield(
            self.chat, "wxid_a", "张三", 20, 14400, 86400, now_ts=1000,
        )
        self.assertTrue(ok)
        self.assertEqual(expires, 1000 + 14400)
        self.assertEqual(balance, 30)
        self.assertEqual(
            self.store.shield_expires_at(self.chat, "wxid_a", 1001), expires,
        )
        self.assertEqual(
            self.store.shield_expires_at(self.chat, "wxid_a", 20000), 0,
        )

    def test_shield_extends_but_caps(self):
        self.store.buy_shield(
            self.chat, "wxid_a", "张三", 20, 14400, 86400, now_ts=1000,
        )
        ok, _, expires, _ = self.store.buy_shield(
            self.chat, "wxid_a", "张三", 0, 14400, 86400, now_ts=1000,
        )
        self.assertTrue(ok)
        self.assertEqual(expires, 1000 + 28800)

    def test_insufficient_balance(self):
        ok, code, _, balance = self.store.buy_shield(
            self.chat, "wxid_b", "李四", 20, 14400, 86400, now_ts=1000,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "insufficient")
        self.assertEqual(balance, 0)


class DuelRecordTests(PointStoreTestBase):
    def test_win_transfers_points(self):
        self.store.record_events(self.chat, "e1", [_award("wxid_a", points=30)])
        self.store.record_events(self.chat, "e2", [_award("wxid_b", points=30)])
        ok = self.store.record_duel(
            self.chat, "duel1", "wxid_a", "张三", "wxid_b", "李四",
            50, 40, 0.7, True, 5,
        )
        self.assertTrue(ok)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 35)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_b"), 25)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 30)
        self.assertEqual(
            self.store.duel_stolen(self.chat, "wxid_a", local_day()), 5,
        )

    def test_duplicate_message_id_is_ignored(self):
        self.store.record_events(self.chat, "e1", [_award("wxid_a", points=30)])
        self.store.record_events(self.chat, "e2", [_award("wxid_b", points=30)])
        self.assertTrue(self.store.record_duel(
            self.chat, "duel1", "wxid_a", "张三", "wxid_b", "李四",
            50, 40, 0.7, True, 5,
        ))
        self.assertFalse(self.store.record_duel(
            self.chat, "duel1", "wxid_a", "张三", "wxid_b", "李四",
            50, 40, 0.7, True, 5,
        ))
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 35)

    def test_counts_attempts_and_received(self):
        self.store.record_events(self.chat, "e1", [_award("wxid_a", points=30)])
        self.store.record_duel(
            self.chat, "d1", "wxid_a", "张三", "wxid_b", "李四",
            50, 40, 0.5, False, 0,
        )
        self.assertEqual(
            self.store.duel_attempts(self.chat, "wxid_a", local_day()), 1,
        )
        self.assertEqual(
            self.store.defender_received(self.chat, "wxid_b", local_day()), 1,
        )
        self.assertEqual(
            self.store.pair_duels(
                self.chat, "wxid_a", "wxid_b", local_day()), 1,
        )
        self.assertEqual(
            self.store.pair_duels(
                self.chat, "wxid_x", "wxid_b", local_day()), 0,
        )
        self.assertGreater(
            self.store.last_loss_at(self.chat, "wxid_a"), 0,
        )

    def test_shield_blocked_not_counted_as_attempt(self):
        self.store.record_duel(
            self.chat, "d1", "wxid_a", "张三", "wxid_b", "李四",
            0, 0, 0.0, False, 0, shield_blocked=True,
        )
        self.assertEqual(
            self.store.duel_attempts(self.chat, "wxid_a", local_day()), 0,
        )
        self.assertEqual(
            self.store.defender_received(self.chat, "wxid_b", local_day()), 0,
        )


class RebuildTests(PointStoreTestBase):
    def test_delete_earn_keeps_other_categories(self):
        self.store.record_events(self.chat, "m1", [_award("wxid_a", points=5)])
        self.store.record_events(self.chat, "m2", [
            _award("wxid_a", "buy_item", -3, "spend"),
        ])
        self.assertEqual(self.store.delete_earn_events(self.chat), 1)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 0)
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), -3)


if __name__ == "__main__":
    unittest.main()
