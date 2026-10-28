"""Daily lottery: reward pool, atomic draw, limits and commands."""

import time
import unittest

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import DuelService
from src.points.engine import PointEngine
from src.points.equipment import EquipmentCatalog
from src.points.fishing_catalog import FishingCatalog
from src.points.handler import PointHandler
from src.points.levels import LevelTable
from src.points.lottery import (
    BALANCE_TIERS,
    CATEGORY_WEIGHTS,
    LotteryService,
    build_pool,
    pick_reward,
)
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.util import local_day

NOW = 2_000_000_000
DAY = local_day(NOW)


class _Rng:
    def __init__(self, value: float):
        self.value = value

    def random(self) -> float:
        return self.value


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.config = BotConfig()
        self.catalog = EquipmentCatalog(None)
        self.fishing = FishingCatalog(None)
        self.pool = build_pool(self.catalog, self.fishing, self.config)

    def test_pool_covers_every_category(self):
        kinds = {entry["kind"] for entry in self.pool}
        self.assertEqual(
            kinds,
            {"balance", "points", "equipment", "rod", "bait", "shield"},
        )
        self.assertAlmostEqual(
            sum(entry["weight"] for entry in self.pool), 100.0, places=6)

    def test_larger_currency_is_rarer(self):
        by_amount = {
            entry["amount"]: entry["weight"]
            for entry in self.pool if entry["kind"] == "balance"
        }
        self.assertGreater(by_amount[20], by_amount[5000])
        self.assertGreater(by_amount[100], by_amount[1000])
        amounts = [amount for amount, _ in BALANCE_TIERS]
        self.assertEqual(amounts, sorted(amounts))

    def test_stronger_equipment_is_rarer(self):
        weights = {
            entry["item_id"]: entry["weight"]
            for entry in self.pool if entry["kind"] == "equipment"
        }
        self.assertGreater(weights["wood_sword"], weights["dragon_sword"])
        self.assertGreater(weights["dragon_sword"], weights["origin_sword"])

    def test_pick_reward_boundaries(self):
        pool = [
            {"kind": "a", "weight": 1},
            {"kind": "b", "weight": 1},
        ]
        self.assertEqual(pick_reward(pool, 0.0)["kind"], "a")
        self.assertEqual(pick_reward(pool, 0.999)["kind"], "b")

    def test_category_chances_sum_to_100(self):
        service = LotteryService(
            None, self.config, self.catalog, self.fishing)
        self.assertAlmostEqual(
            sum(service.category_chances().values()), 100.0, places=1)
        self.assertEqual(
            set(service.category_chances()), set(CATEGORY_WEIGHTS))


class LotteryBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.points_enabled = True
        self.config.equipment_enabled = True
        self.config.duel_enabled = True
        self.config.lottery_enabled = True
        self.config.points_groups = ["*"]
        self.config.duel_groups = ["*"]
        self.config.lottery_groups = ["*"]
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.fishing_catalog = FishingCatalog(None)
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog)
        self.lottery = LotteryService(
            self.store, self.config, self.catalog, self.fishing_catalog)
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels, engine=PointEngine(self.store, self.config),
        )
        self.chat = "1@chatroom"
        self._seq = 0

    def tearDown(self):
        self.conn.close()

    def add_member(self, earned=1000):
        self._seq += 1
        self.messages.insert_message({
            "message_id": f"seed-{self._seq}",
            "chat_id": self.chat,
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        if earned:
            self.store.record_events(
                self.chat, f"earn-{self._seq}",
                [PointAward("wxid_a", "张三", "base", earned)],
            )

    def commit(self, message_id, reward, cost=100, limit=3):
        return self.store.commit_lottery_draw(
            chat_id=self.chat, sender_id="wxid_a", sender_name="张三",
            message_id=message_id, day=DAY, now_ts=NOW,
            cost=cost, limit=limit, reward=reward,
        )

    def msg(self, content):
        self._seq += 1
        return {
            "message_id": f"cmd-{self._seq}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def run_cmd(self, content):
        return self.handler.handle(content, self.msg(content))


class StoreDrawTests(LotteryBase):
    def test_balance_reward_charges_ticket(self):
        self.add_member()
        result = self.commit("m1", {
            "kind": "balance", "name": "灵石 +50", "amount": 50,
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["used"], 1)
        self.assertEqual(result["balance"], 950)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 1000)
        self.assertEqual(
            self.store.lottery_draws_today(self.chat, "wxid_a", DAY), 1)

    def test_points_reward_counts_as_earned(self):
        self.add_member()
        result = self.commit("m1", {
            "kind": "points", "name": "修炼值 +20", "amount": 20,
        })
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 1020)
        # Earned points are also spendable balance in this economy.
        self.assertEqual(result["balance"], 920)

    def test_daily_limit(self):
        self.add_member()
        self.assertTrue(self.commit("m1", {
            "kind": "balance", "name": "灵石 +20", "amount": 20,
        }, limit=1)["ok"])
        blocked = self.commit("m2", {
            "kind": "balance", "name": "灵石 +20", "amount": 20,
        }, limit=1)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "limit")

    def test_insufficient_balance(self):
        self.add_member(earned=50)
        result = self.commit("m1", {
            "kind": "balance", "name": "灵石 +20", "amount": 20,
        })
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "insufficient")
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 50)

    def test_replay_is_idempotent(self):
        self.add_member()
        first = self.commit("m1", {
            "kind": "balance", "name": "灵石 +50", "amount": 50,
        })
        second = self.commit("m1", {
            "kind": "balance", "name": "灵石 +50", "amount": 50,
        })
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["code"], "duplicate")
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 950)
        self.assertEqual(
            self.store.lottery_draws_today(self.chat, "wxid_a", DAY), 1)

    def test_equipment_reward_and_duplicate_refund(self):
        self.add_member()
        reward = {
            "kind": "equipment", "name": "装备 木剑",
            "item_id": "wood_sword", "price": 30, "power": 4,
            "same_slot_ids": ["wood_sword"],
        }
        first = self.commit("m1", reward)
        self.assertTrue(first["ok"])
        self.assertEqual(first["detail"], "已自动装备")
        owned = self.store.owned_items(self.chat, "wxid_a")
        self.assertEqual([row["item_id"] for row in owned], ["wood_sword"])
        self.assertEqual(owned[0]["equipped"], 1)

        second = self.commit("m2", reward)
        self.assertTrue(second["ok"])
        self.assertIn("折算", second["detail"])
        # 1000 - 100 - 100 + 15 refund
        self.assertEqual(second["balance"], 815)
        self.assertEqual(
            len(self.store.owned_items(self.chat, "wxid_a")), 1)

    def test_rod_bait_and_shield_rewards(self):
        self.add_member()
        rod = self.commit("m1", {
            "kind": "rod", "name": "鱼竿 碳素竿",
            "rod_id": "carbon", "price": 300,
        })
        self.assertTrue(rod["ok"])
        inventory = self.store.fishing_inventory(
            self.chat, "wxid_a")
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["kind"], "rod")
        self.assertEqual(inventory[0]["equipped"], 0)

        bait = self.commit("m2", {
            "kind": "bait", "name": "鱼饵 红虫×10",
            "bait_id": "redworm", "quantity": 10,
        })
        self.assertTrue(bait["ok"])
        bait = self.commit("m3", {
            "kind": "bait", "name": "鱼饵 红虫×10",
            "bait_id": "redworm", "quantity": 10,
        })
        self.assertTrue(bait["ok"])
        stock = [
            row for row in self.store.fishing_inventory(
                self.chat, "wxid_a")
            if row["kind"] == "bait"
        ]
        self.assertEqual(stock[0]["quantity"], 20)

    def test_shield_reward_extends_immunity(self):
        self.add_member()
        result = self.commit("m1", {
            "kind": "shield", "name": "免战券 4 小时",
            "duration_sec": 14400, "max_sec": 86400,
        })
        self.assertTrue(result["ok"])
        expires = self.store.shield_expires_at(
            self.chat, "wxid_a", NOW)
        self.assertGreater(expires, NOW)


class ServiceDrawTests(LotteryBase):
    def test_low_roll_gets_first_pool_entry(self):
        self.add_member()
        result = self.lottery.draw(
            self.msg("抽奖"), now_ts=NOW, rng=_Rng(0.0))
        self.assertTrue(result["ok"])
        self.assertEqual(result["reward"]["kind"], "balance")
        self.assertEqual(result["reward"]["amount"], 20)
        self.assertEqual(result["used"], 1)
        self.assertEqual(result["balance"], 920)

    def test_high_roll_gets_rarest_entry(self):
        self.add_member()
        result = self.lottery.draw(
            self.msg("抽奖"), now_ts=NOW, rng=_Rng(0.999999))
        self.assertTrue(result["ok"])
        self.assertEqual(result["reward"]["kind"], "shield")
        self.assertEqual(result["reward"]["duration_sec"], 24 * 3600)

    def test_status_and_history(self):
        self.add_member()
        self.lottery.draw(self.msg("抽奖"), now_ts=NOW, rng=_Rng(0.0))
        status = self.lottery.status(
            self.chat, "测试群", "wxid_a", now_ts=NOW)
        self.assertEqual(status["used"], 1)
        self.assertEqual(status["limit"], 3)
        self.assertEqual(status["cost"], 100)
        history = self.lottery.history(self.chat, "wxid_a")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reward_kind"], "balance")

    def test_disabled(self):
        self.add_member()
        self.config.lottery_enabled = False
        result = self.lottery.draw(self.msg("抽奖"), now_ts=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "disabled")


class HandlerLotteryTests(LotteryBase):
    def test_pool_command(self):
        self.add_member()
        reply = self.run_cmd("奖池")
        self.assertIn("每日抽奖", reply)
        self.assertIn("奖池分类概率", reply)
        self.assertIn("装备", reply)
        self.assertIn("鱼竿", reply)

    def test_draw_command(self):
        self.add_member()
        reply = self.run_cmd("抽奖")
        self.assertIn("抽奖：", reply)
        self.assertIn("今日 1/3 次", reply)
        log = self.run_cmd("抽奖记录")
        self.assertIn("最近的抽奖记录", log)

    def test_draw_insufficient(self):
        self.add_member(earned=50)
        reply = self.run_cmd("抽奖")
        self.assertIn("灵石不足", reply)

    def test_daily_limit_command(self):
        self.add_member()
        for _ in range(3):
            self.run_cmd("抽奖")
        reply = self.run_cmd("抽奖")
        self.assertIn("次数已用完", reply)


class HelpLotteryTests(unittest.TestCase):
    def test_help_lists_lottery(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from src.admin import AdminCommandHandler

        config = SimpleNamespace(
            points_enabled=True, fun_enabled=True,
            equipment_enabled=True, duel_enabled=True,
            fishing_enabled=True, todo_enabled=True,
            summarize_enabled=True, boss_enabled=True,
            lottery_enabled=True, lottery_cost=100,
            lottery_daily_attempts=3,
            duel_daily_attempts=10, duel_per_fight_cap=20,
            shield_price=20, shield_duration_sec=14400,
            shield_max_sec=86400, fishing_hourly_attempts=30,
            fishing_max_batch=10, fishing_daily_cap=120,
            boss_cooldown_sec=1800, boss_max_attempts=3,
        )
        reply = AdminCommandHandler(MagicMock()).handle(
            "帮助", "张三", config=config, is_admin=False)
        self.assertIn("抽奖 / 奖池", reply)
        self.assertIn("100 灵石一次", reply)
        self.assertIn("每日 3 次", reply)


if __name__ == "__main__":
    unittest.main()
