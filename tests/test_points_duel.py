"""Duel formulas and DuelService behaviour."""

import time
import unittest

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import DuelService, steal_amount, win_probability
from src.points.equipment import EquipmentCatalog
from src.points.levels import LevelTable
from src.points.models import PointAward
from src.points.store import PointStore


class _Rng:
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


class FormulaTests(unittest.TestCase):
    def test_equal_power_is_fifty_percent(self):
        self.assertAlmostEqual(win_probability(50, 50), 0.5)

    def test_stronger_attacker_is_more_likely(self):
        self.assertGreater(win_probability(75, 50), 0.5)
        self.assertGreater(win_probability(100, 50),
                           win_probability(75, 50))

    def test_probability_is_capped(self):
        self.assertLessEqual(win_probability(10_000, 1), 0.92)
        self.assertGreaterEqual(win_probability(1, 10_000), 0.08)

    def test_steal_base_is_share_of_balance(self):
        self.assertEqual(steal_amount(50, 50, 100), 8)

    def test_stronger_attacker_steals_less(self):
        equal = steal_amount(50, 50, 100)
        stronger = steal_amount(100, 50, 100)
        much_stronger = steal_amount(500, 50, 100)
        self.assertGreater(equal, stronger)
        self.assertGreater(stronger, much_stronger)

    def test_weaker_attacker_is_not_penalised(self):
        self.assertEqual(steal_amount(25, 100, 100),
                         steal_amount(50, 50, 100))

    def test_steal_never_exceeds_balance_or_cap(self):
        self.assertEqual(steal_amount(50, 50, 3), 2)
        self.assertLessEqual(steal_amount(50, 50, 10_000), 20)


class DuelServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.duel_enabled = True
        self.config.duel_groups = ["*"]
        self.config.shield_price = 20
        self.config.duel_daily_attempts = 10
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.duel = DuelService(
            self.store, self.config, self.levels, self.catalog,
        )
        self.chat = "1@chatroom"
        self._msg_n = 0

    def tearDown(self):
        self.conn.close()

    def add_member(self, sender_id, name, earned=100):
        self.messages.insert_message({
            "message_id": f"seed-{sender_id}",
            "chat_id": self.chat,
            "sender_id": sender_id,
            "sender_name": name,
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        if earned:
            self.store.record_events(
                self.chat, f"earn-{sender_id}",
                [PointAward(sender_id, name, "base", earned)],
            )

    def msg(self, sender_id="wxid_a", name="张三"):
        self._msg_n += 1
        return {
            "message_id": f"duel-{self._msg_n}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": "切磋",
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def challenge(self, rng=None, attacker="wxid_a", target="wxid_b",
                  name_a="张三", name_b="李四"):
        return self.duel.challenge(
            self.msg(attacker, name_a), target, rng=rng,
        )


class PowerTests(DuelServiceTestBase):
    def test_base_power_from_level(self):
        self.add_member("wxid_a", "张三", earned=100)
        power = self.duel.power(self.chat, "wxid_a")
        # 100 points => Lv2 => 10 + 2*5
        self.assertEqual(power.level, 2)
        self.assertEqual(power.total, 20)
        self.assertEqual(power.equipment, 0)

    def test_equipment_adds_power(self):
        self.add_member("wxid_a", "张三", earned=100)
        item = {"id": "wood_sword", "name": "木剑", "slot": "weapon",
                "power": 4, "price": 30}
        self.store.buy_item(self.chat, "wxid_a", "张三", item, ["wood_sword"])
        power = self.duel.power(self.chat, "wxid_a")
        self.assertEqual(power.equipment, 4)
        self.assertEqual(power.total, 24)


class ChallengeTests(DuelServiceTestBase):
    def setUp(self):
        super().setUp()
        self.add_member("wxid_a", "张三", earned=100)
        self.add_member("wxid_b", "李四", earned=100)

    def test_win_transfers_points(self):
        before_a = self.store.balance_of(self.chat, "wxid_a")
        before_b = self.store.balance_of(self.chat, "wxid_b")
        result = self.challenge(_Rng(0.0))
        self.assertTrue(result.ok)
        self.assertTrue(result.attacker_win)
        self.assertGreater(result.points_moved, 0)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"),
            before_a + result.points_moved,
        )
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_b"),
            before_b - result.points_moved,
        )
        self.assertEqual(result.attempts_used, 1)

    def test_loss_moves_no_points_but_sets_cooldown(self):
        before_a = self.store.balance_of(self.chat, "wxid_a")
        result = self.challenge(_Rng(0.999))
        self.assertTrue(result.ok)
        self.assertFalse(result.attacker_win)
        self.assertEqual(result.points_moved, 0)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"), before_a,
        )

        again = self.challenge()
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "exhausted")

    def test_self_challenge_rejected(self):
        result = self.challenge(target="wxid_a")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "self")

    def test_unknown_member_rejected(self):
        result = self.challenge(target="wxid_zzz")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_member")

    def test_novice_protection(self):
        self.add_member("wxid_c", "新人", earned=5)
        result = self.challenge(target="wxid_c")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "novice")

    def test_shield_blocks_without_consuming_attempt(self):
        self.store.buy_shield(
            self.chat, "wxid_b", "李四", 20, 14400, 86400,
        )
        result = self.challenge()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "shield")
        self.assertEqual(
            self.store.duel_attempts(self.chat, "wxid_a", time.strftime(
                "%Y-%m-%d")),
            0,
        )

    def test_pair_cooldown(self):
        self.challenge(_Rng(0.0))
        again = self.challenge(_Rng(0.0))
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "pair_cooldown")

    def test_daily_attempt_limit(self):
        self.config.duel_daily_attempts = 1
        first = self.challenge(_Rng(0.0))
        self.assertTrue(first.ok)
        self.add_member("wxid_c", "王五", earned=100)
        again = self.challenge(_Rng(0.0), target="wxid_c")
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "attempts")

    def test_pair_daily_limit(self):
        """Each attacker→defender pair is capped per day."""
        self.config.duel_daily_pair_attempts = 1
        self.config.duel_pair_cooldown_sec = 0
        first = self.challenge(_Rng(0.0))
        self.assertTrue(first.ok)
        again = self.challenge(_Rng(0.0))
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "pair_limit")
        self.assertIn("每天最多 1 次", again.reply)

    def test_other_attackers_are_not_blocked_by_pair_limit(self):
        """Another attacker's duels do not consume this pair's budget."""
        self.config.duel_daily_pair_attempts = 1
        self.config.duel_pair_cooldown_sec = 0
        # wxid_x already attacked wxid_b today.
        self.store.record_duel(
            self.chat, "other-1", "wxid_x", "路人", "wxid_b", "李四",
            10, 10, 0.5, False, 0,
        )
        # wxid_a → wxid_b is still at 0/1 and must be allowed.
        first = self.challenge(_Rng(0.0))
        self.assertTrue(first.ok, first.reply)
        again = self.challenge(_Rng(0.0))
        self.assertFalse(again.ok)
        self.assertEqual(again.code, "pair_limit")

    def test_pair_limit_zero_disables_the_cap(self):
        self.config.duel_daily_pair_attempts = 0
        self.config.duel_pair_cooldown_sec = 0
        self.assertTrue(self.challenge(_Rng(0.0)).ok)
        self.assertTrue(self.challenge(_Rng(0.0)).ok)

    def test_daily_steal_cap(self):
        self.config.duel_daily_steal_cap = 1
        first = self.challenge(_Rng(0.0))
        self.assertTrue(first.ok)
        self.assertEqual(first.points_moved, 1)

        self.add_member("wxid_c", "王五", earned=100)
        self.store.record_events(self.chat, "more", [
            PointAward("wxid_c", "王五", "base", 100),
        ])
        second = self.challenge(_Rng(0.0), target="wxid_c")
        self.assertFalse(second.ok)
        self.assertEqual(second.code, "steal_cap")

    def test_empty_target_balance(self):
        self.store.record_events(
            self.chat, "spend-b", [
                PointAward("wxid_b", "李四", "buy_item", -100, "spend"),
            ],
        )
        result = self.challenge()
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "empty")


if __name__ == "__main__":
    unittest.main()
