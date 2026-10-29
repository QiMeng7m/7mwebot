"""功法 / 打坐 and the attack/defense split."""

import time
import unittest
from unittest.mock import patch

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import DuelService
from src.points.engine import PointEngine
from src.points.equipment import EquipmentCatalog
from src.points.handler import PointHandler
from src.points.levels import LevelTable
from src.points.lottery import build_pool
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.techniques import (
    LEARN_COST,
    MAX_LAYER,
    TECHNIQUES,
    aggregate_effects,
    breakthrough_chance,
    effects_for,
    required_progress,
    technique_by_name,
)

NOW = 2_000_000_000


class TechniqueFormulaTests(unittest.TestCase):
    def test_progress_and_chance(self):
        self.assertEqual(required_progress(1), 100)
        self.assertEqual(required_progress(8), 800)
        self.assertEqual(required_progress(MAX_LAYER), 0)
        self.assertGreater(breakthrough_chance(1),
                           breakthrough_chance(5))
        self.assertGreaterEqual(breakthrough_chance(9), 0.30)
        self.assertEqual(len(TECHNIQUES), 8)

    def test_aggregate_effects_scales_with_layer(self):
        rows = [{"technique_id": "tuna", "layer": 2}]
        effects = aggregate_effects(rows)
        self.assertEqual(effects["attack"], 4)
        self.assertEqual(effects["defense"], 2)
        self.assertAlmostEqual(effects["meditation_points"], 0.10)

    def test_technique_lookup(self):
        self.assertEqual(technique_by_name("吐纳诀")["id"], "tuna")
        self.assertIsNone(technique_by_name("不存在"))


class TechniqueBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.points_enabled = True
        self.config.equipment_enabled = True
        self.config.duel_enabled = True
        self.config.points_groups = ["*"]
        self.config.duel_groups = ["*"]
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog)
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels, engine=PointEngine(self.store, self.config),
        )
        self.chat = "1@chatroom"
        self._seq = 0
        self.messages.insert_message({
            "message_id": "seed",
            "chat_id": self.chat,
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        self.store.record_events(
            self.chat, "earn", [PointAward("wxid_a", "张三", "base", 1000)])

    def tearDown(self):
        self.conn.close()

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


class AttackDefenseTests(TechniqueBase):
    def test_base_split_preserves_total(self):
        power = self.duels.power(self.chat, "wxid_a")
        # 1000 points => Lv4: attack 5+4*2=13, defense 5+4*3=17.
        self.assertEqual((power.attack, power.defense), (13, 17))
        self.assertEqual(power.total, power.attack + power.defense)
        self.assertEqual(power.base, 30)

    def test_weapon_and_armor_split(self):
        sword = self.catalog.find("木剑")
        armor = self.catalog.find("布衣")
        self.store.buy_item(
            self.chat, "wxid_a", "张三", sword,
            self.catalog.same_slot_ids(sword))
        self.store.buy_item(
            self.chat, "wxid_a", "张三", armor,
            self.catalog.same_slot_ids(armor))
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.equipment_attack, 4)
        self.assertEqual(power.equipment_defense, 3)
        self.assertEqual(power.total, 13 + 17 + 4 + 3)

    def test_learned_technique_adds_attack_and_defense(self):
        ok, _, _ = self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        self.assertTrue(ok)
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.technique_attack, 2)
        self.assertEqual(power.technique_defense, 1)
        self.assertEqual(power.total, 13 + 17 + 3)

    def test_duel_uses_attack_vs_defense(self):
        self.messages.insert_message({
            "message_id": "seed-b",
            "chat_id": self.chat,
            "sender_id": "wxid_b",
            "sender_name": "李四",
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        self.store.record_events(
            self.chat, "earn-b", [PointAward("wxid_b", "李四", "base", 1000)])
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "zhanyi", LEARN_COST, "learn-a")
        result = self.duels.challenge(self.msg("切磋"), "wxid_b")
        self.assertTrue(result.ok)
        attacker = self.duels.power(self.chat, "wxid_a")
        defender = self.duels.power(self.chat, "wxid_b")
        self.assertEqual(result.attacker_power, attacker.attack)
        self.assertEqual(result.defender_power, defender.defense)


class StoreTechniqueTests(TechniqueBase):
    def test_learn_sets_current_and_spends(self):
        ok, code, balance = self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        self.assertTrue(ok)
        self.assertEqual(code, "ok")
        self.assertEqual(balance, 500)
        rows = self.store.techniques_of(self.chat, "wxid_a")
        self.assertEqual(rows[0]["layer"], 1)
        self.assertEqual(
            self.store.technique_state(
                self.chat, "wxid_a")["current_technique"], "tuna")
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 1000)

    def test_learn_duplicate_and_insufficient(self):
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        ok, code, _ = self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-2")
        self.assertFalse(ok)
        self.assertEqual(code, "owned")
        ok, code, _ = self.store.learn_technique(
            self.chat, "wxid_a", "张三", "zhuti", 99999, "learn-3")
        self.assertFalse(ok)
        self.assertEqual(code, "insufficient")

    def test_meditation_awards_earned_only_and_progress(self):
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        before_balance = self.store.balance_of(self.chat, "wxid_a")
        result = self.store.meditate(
            self.chat, "wxid_a", "张三", 5, 10, 5, 100,
            "med-1", now_ts=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"], 5)
        self.assertEqual(result["progress_gain"], 5)
        self.assertEqual(
            self.store.earned_of(self.chat, "wxid_a"), 1010)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"), before_balance)

    def test_meditation_cooldown_and_replay(self):
        first = self.store.meditate(
            self.chat, "wxid_a", "张三", 5, 10, 0, 0,
            "med-1", now_ts=NOW)
        self.assertTrue(first["ok"])
        blocked = self.store.meditate(
            self.chat, "wxid_a", "张三", 5, 10, 0, 0,
            "med-2", now_ts=NOW + 10)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "cooldown")
        replay = self.store.meditate(
            self.chat, "wxid_a", "张三", 5, 10, 0, 0,
            "med-1", now_ts=NOW + 1000)
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["code"], "duplicate")

    def test_breakthrough_success_and_failure(self):
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        self.store.conn.execute(
            """UPDATE technique_progress SET progress = 100
               WHERE chat_id = ? AND sender_id = ? AND technique_id = ?""",
            (self.chat, "wxid_a", "tuna"))
        success = self.store.attempt_technique_breakthrough(
            self.chat, "wxid_a", "tuna", 1, True, 100, MAX_LAYER,
            now_ts=NOW)
        self.assertTrue(success["ok"])
        self.assertEqual(success["code"], "success")
        self.assertEqual(success["layer"], 2)

        self.store.conn.execute(
            """UPDATE technique_progress
               SET progress = 200, failures = 0
               WHERE chat_id = ? AND sender_id = ? AND technique_id = ?""",
            (self.chat, "wxid_a", "tuna"))
        for _ in range(4):
            self.store.conn.execute(
                """UPDATE technique_progress SET progress = 200
                   WHERE chat_id = ? AND sender_id = ?
                     AND technique_id = ?""",
                (self.chat, "wxid_a", "tuna"))
            result = self.store.attempt_technique_breakthrough(
                self.chat, "wxid_a", "tuna", 2, False, 200, MAX_LAYER,
                now_ts=NOW)
            self.assertEqual(result["code"], "fail")
            self.assertEqual(result["progress"], 100)
        self.store.conn.execute(
            """UPDATE technique_progress SET progress = 200
               WHERE chat_id = ? AND sender_id = ? AND technique_id = ?""",
            (self.chat, "wxid_a", "tuna"))
        dropped = self.store.attempt_technique_breakthrough(
            self.chat, "wxid_a", "tuna", 2, False, 200, MAX_LAYER,
            now_ts=NOW)
        self.assertEqual(dropped["code"], "drop")
        self.assertEqual(dropped["layer"], 1)


class HandlerTechniqueTests(TechniqueBase):
    def test_commands_learn_list_meditate_switch(self):
        reply = self.run_cmd("学习功法 吐纳诀")
        self.assertIn("学会《吐纳诀》", reply)
        self.assertIn("攻击", reply)
        listing = self.run_cmd("功法")
        self.assertIn("吐纳诀", listing)
        self.assertIn("第1层", listing)

        med = self.run_cmd("打坐 5")
        self.assertIn("打坐", med)
        self.assertIn("修炼值 +10", med)
        self.assertIn("进度 +5", med)

        switched = self.run_cmd("切换功法 吐纳诀")
        self.assertIn("已切换当前功法", switched)

    def test_meditate_without_technique_still_gives_points(self):
        reply = self.run_cmd("打坐5")
        self.assertIn("修炼值 +10", reply)
        self.assertIn("还没有选择功法", reply)

    def test_meditate_cooldown_message(self):
        self.run_cmd("打坐 5")
        second = self.run_cmd("打坐 5")
        self.assertIn("还在打坐中", second)

    def test_breakthrough_command(self):
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        self.store.conn.execute(
            """UPDATE technique_progress SET progress = 100
               WHERE chat_id = ? AND sender_id = ? AND technique_id = ?""",
            (self.chat, "wxid_a", "tuna"))
        with patch("src.points.handler.random.random", return_value=0.0):
            reply = self.run_cmd("突破功法")
        self.assertIn("突破成功", reply)
        self.assertIn("第2层", reply)


class TechniqueEffectTests(TechniqueBase):
    def test_effects_stack_across_techniques(self):
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "tuna", LEARN_COST, "learn-1")
        self.store.learn_technique(
            self.chat, "wxid_a", "张三", "zhuti", LEARN_COST, "learn-2")
        effects = effects_for(self.store, self.chat, "wxid_a")
        self.assertEqual(effects["attack"], 2)
        self.assertEqual(effects["defense"], 5)

    def test_lottery_luck_boosts_high_tiers(self):
        plain = build_pool(self.catalog, None, self.config, luck=0.0)
        lucky = build_pool(self.catalog, None, self.config, luck=0.2)
        plain_top = max(
            entry["weight"] for entry in plain
            if entry["kind"] == "balance" and entry["amount"] == 5000)
        lucky_top = max(
            entry["weight"] for entry in lucky
            if entry["kind"] == "balance" and entry["amount"] == 5000)
        self.assertGreater(lucky_top, plain_top)


if __name__ == "__main__":
    unittest.main()
