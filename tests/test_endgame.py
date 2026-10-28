"""Endgame systems: realm, enhancement, world boss and achievements."""

import time
import unittest

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.achievements import (
    ACHIEVEMENTS,
    AchievementService,
    achievement_by_title,
)
from src.points.boss import (
    BOSS_NAMES,
    BossService,
    boss_hp_for,
    boss_name_for,
    roll_damage,
)
from src.points.duel import DuelService
from src.points.engine import PointEngine
from src.points.equipment import (
    ENHANCE_MAX,
    EquipmentCatalog,
    effective_power,
    enhance_cost,
)
from src.points.handler import PointHandler
from src.points.levels import LevelTable
from src.points.models import PointAward
from src.points.realm import (
    REALM_MAX,
    realm_cost,
    realm_info,
    realm_title,
)
from src.points.store import PointStore
from src.points.util import days_until_week_end, local_week

NOW = 2_000_000_000


class _Rng:
    def __init__(self, *values):
        self.values = list(values)
        self.index = 0

    def random(self) -> float:
        if self.index >= len(self.values):
            return 0.5
        value = self.values[self.index]
        self.index += 1
        return value


class RealmFormulaTests(unittest.TestCase):
    def test_titles_and_costs(self):
        self.assertEqual(realm_title(0), "凡人")
        self.assertEqual(realm_title(1), "炼气")
        self.assertEqual(realm_title(REALM_MAX), "渡劫")
        self.assertEqual(realm_cost(0), 300)
        self.assertEqual(realm_cost(1), 600)
        self.assertEqual(realm_cost(REALM_MAX), 0)
        self.assertEqual(realm_info(2)["power_bonus"], 20)
        self.assertEqual(realm_info(2)["next"], 3)


class EnhanceFormulaTests(unittest.TestCase):
    def test_cost_and_power(self):
        item = {"power": 10, "price": 100}
        self.assertEqual(enhance_cost(item, 0), 10)
        self.assertGreater(enhance_cost(item, 5), enhance_cost(item, 1))
        self.assertEqual(enhance_cost(item, ENHANCE_MAX), 0)
        self.assertEqual(effective_power(item, 0), 10)
        self.assertEqual(effective_power(item, 3), 16)


class BossFormulaTests(unittest.TestCase):
    def test_week_key_and_days_left(self):
        self.assertRegex(local_week(NOW), r"^\d{4}-W\d{2}$")
        self.assertTrue(0 <= days_until_week_end(NOW) <= 6)

    def test_damage_range_and_crit(self):
        low, crit = roll_damage(100, 0.0, 0.99)
        high, _ = roll_damage(100, 0.999, 0.99)
        crit_hit, crit_flag = roll_damage(100, 0.0, 0.0)
        self.assertEqual(low, 80)
        self.assertEqual(high, 120)
        self.assertFalse(crit)
        self.assertTrue(crit_flag)
        self.assertEqual(crit_hit, 160)

    def test_boss_name_is_stable(self):
        self.assertIn(boss_name_for("1@chatroom", "2026-W43"), BOSS_NAMES)
        self.assertEqual(
            boss_name_for("1@chatroom", "2026-W43"),
            boss_name_for("1@chatroom", "2026-W43"),
        )

    def test_hp_scales_and_clamps(self):
        config = BotConfig()
        config.boss_hp_per_member = 100
        config.boss_min_hp = 500
        config.boss_max_hp = 1000
        self.assertEqual(boss_hp_for(1, config), 500)
        self.assertEqual(boss_hp_for(5, config), 500)
        self.assertEqual(boss_hp_for(20, config), 1000)


class EndgameTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.points_enabled = True
        self.config.equipment_enabled = True
        self.config.duel_enabled = True
        self.config.fishing_enabled = True
        self.config.boss_enabled = True
        self.config.points_groups = ["*"]
        self.config.duel_groups = ["*"]
        self.config.boss_groups = ["*"]
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog)
        self.boss = BossService(self.store, self.config, self.duels)
        self.achievements = AchievementService(
            self.store, self.config, self.levels, self.duels)
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels, engine=PointEngine(self.store, self.config),
        )
        self.chat = "1@chatroom"
        self._seq = 0

    def tearDown(self):
        self.conn.close()

    def add_member(self, sender_id="wxid_a", name="张三", earned=100):
        self._seq += 1
        self.messages.insert_message({
            "message_id": f"seed-{self._seq}",
            "chat_id": self.chat,
            "sender_id": sender_id,
            "sender_name": name,
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        if earned:
            self.store.record_events(
                self.chat, f"earn-{self._seq}",
                [PointAward(sender_id, name, "base", earned)],
            )

    def msg(self, content, sender_id="wxid_a", name="张三"):
        self._seq += 1
        return {
            "message_id": f"cmd-{self._seq}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def run_cmd(self, content, sender_id="wxid_a", name="张三"):
        return self.handler.handle(
            content, self.msg(content, sender_id, name))


class RealmStoreTests(EndgameTestBase):
    def test_breakthrough_spends_and_levels_power(self):
        self.add_member(earned=120_000)
        before = self.store.balance_of(self.chat, "wxid_a")
        ok, code, realm, balance = self.store.breakthrough(
            self.chat, "wxid_a", "张三", 3000, REALM_MAX, "realm-1",
            now_ts=NOW,
        )
        self.assertTrue(ok)
        self.assertEqual(code, "ok")
        self.assertEqual(realm, 1)
        self.assertEqual(balance, before - 3000)
        self.assertEqual(self.store.realm_of(self.chat, "wxid_a"), 1)
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.realm_bonus, 10)

    def test_duplicate_breakthrough_is_idempotent(self):
        self.add_member(earned=120_000)
        self.assertTrue(self.store.breakthrough(
            self.chat, "wxid_a", "张三", 3000, REALM_MAX, "same",
            now_ts=NOW)[0])
        ok, code, realm, _ = self.store.breakthrough(
            self.chat, "wxid_a", "张三", 3000, REALM_MAX, "same",
            now_ts=NOW,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "duplicate")
        self.assertEqual(realm, 1)

    def test_breakthrough_command_requires_max_level(self):
        self.add_member(earned=100)
        reply = self.run_cmd("突破")
        self.assertIn("境界突破需要先达到", reply)
        self.assertEqual(self.store.realm_of(self.chat, "wxid_a"), 0)

    def test_breakthrough_command_succeeds_at_max_level(self):
        self.add_member(earned=120_000)
        reply = self.run_cmd("突破")
        self.assertIn("突破成功", reply)
        self.assertIn("炼气", reply)
        self.assertEqual(self.store.realm_of(self.chat, "wxid_a"), 1)
        status = self.run_cmd("境界")
        self.assertIn("炼气", status)


class EnhanceTests(EndgameTestBase):
    def test_store_enhance_deducts_and_caps(self):
        self.add_member(earned=1000)
        item = self.catalog.find("木剑")
        ok, _, _, _ = self.store.buy_item(
            self.chat, "wxid_a", "张三", item,
            self.catalog.same_slot_ids(item),
        )
        self.assertTrue(ok)
        before = self.store.balance_of(self.chat, "wxid_a")
        ok, code, level, balance = self.store.enhance_item(
            self.chat, "wxid_a", "张三", item["id"], 10, ENHANCE_MAX,
            "enh-1", now_ts=NOW,
        )
        self.assertTrue(ok)
        self.assertEqual((code, level), ("ok", 1))
        self.assertEqual(balance, before - 10)

        self.store.conn.execute(
            "UPDATE equipment_owned SET enhance_level = ? "
            "WHERE chat_id = ? AND sender_id = ? AND item_id = ?",
            (ENHANCE_MAX, self.chat, "wxid_a", item["id"]),
        )
        ok, code, level, _ = self.store.enhance_item(
            self.chat, "wxid_a", "张三", item["id"], 10, ENHANCE_MAX,
            "enh-2", now_ts=NOW,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "max")

    def test_enhance_command_and_power(self):
        self.add_member(earned=1000)
        item = self.catalog.find("木剑")
        self.store.buy_item(
            self.chat, "wxid_a", "张三", item,
            self.catalog.same_slot_ids(item),
        )
        base_power = self.duels.power(self.chat, "wxid_a").total
        reply = self.run_cmd("强化 木剑")
        self.assertIn("强化成功", reply)
        self.assertIn("木剑 +1", reply)
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.total, base_power + 2)


class BossServiceTests(EndgameTestBase):
    def setUp(self):
        super().setUp()
        self.add_member(earned=100)
        self.config.boss_min_hp = 100
        self.config.boss_max_hp = 100
        self.config.boss_hp_per_member = 1
        self.config.boss_cooldown_sec = 1800

    def test_status_creates_weekly_boss(self):
        state = self.boss.status(
            self.chat, "测试群", "wxid_a", now_ts=NOW)
        self.assertTrue(state["ok"])
        self.assertEqual(state["hp_total"], 100)
        self.assertEqual(state["hp_left"], 100)
        self.assertEqual(state["cooldown_sec"], 1800)
        self.assertEqual(state["cooldown_left"], 0)
        self.assertIn(state["boss_name"], BOSS_NAMES)

    def test_attack_damages_and_is_idempotent(self):
        msg = self.msg("Boss")
        first = self.boss.attack(msg, now_ts=NOW, rng=_Rng(0.0, 0.99))
        self.assertTrue(first["ok"])
        self.assertGreater(first["damage"], 0)
        # Same message id must not double-hit.
        replay = self.boss.attack(msg, now_ts=NOW, rng=_Rng(0.0, 0.99))
        self.assertFalse(replay["ok"])
        self.assertEqual(replay["code"], "duplicate")

    def test_attack_cooldown(self):
        first = self.boss.attack(
            self.msg("Boss"), now_ts=NOW, rng=_Rng(0.0, 0.99))
        self.assertTrue(first["ok"])
        blocked = self.boss.attack(
            self.msg("Boss"), now_ts=NOW + 1, rng=_Rng(0.0, 0.99))
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["code"], "cooldown")
        self.assertEqual(blocked["cooldown_left"], 1799)
        # Half an hour later the member may hit again.
        again = self.boss.attack(
            self.msg("Boss"), now_ts=NOW + 1800, rng=_Rng(0.0, 0.99))
        self.assertTrue(again["ok"])

    def test_kill_distributes_rewards(self):
        self.config.boss_min_hp = 10
        self.config.boss_max_hp = 10
        result = self.boss.attack(
            self.msg("Boss"), now_ts=NOW, rng=_Rng(0.0, 0.99))
        self.assertTrue(result["killed"])
        self.assertEqual(result["hp_left"], 0)
        self.assertEqual(result["rewards"][0]["sender_id"], "wxid_a")
        self.assertGreater(result["rewards"][0]["amount"], 0)
        earned = self.store.earned_of(self.chat, "wxid_a")
        self.assertEqual(earned, 100)  # boss reward is balance-only
        # A second attack after the kill is refused.
        again = self.boss.attack(
            self.msg("Boss"), now_ts=NOW + 1, rng=_Rng(0.0, 0.99))
        self.assertFalse(again["ok"])
        self.assertEqual(again["code"], "killed")

    def test_boss_commands(self):
        reply = self.run_cmd("Boss")
        self.assertIn("造成", reply)
        self.assertIn("Boss 剩余", reply)
        status = self.run_cmd("Boss状态")
        self.assertIn("本周 Boss", status)
        board = self.run_cmd("Boss榜")
        self.assertIn("伤害榜", board)


class AchievementTests(EndgameTestBase):
    def test_newcomer_unlocks_with_balance_reward(self):
        self.add_member(earned=100)
        before = self.store.balance_of(self.chat, "wxid_a")
        result = self.achievements.evaluate(self.chat, "wxid_a")
        ids = {item["id"] for item in result["new"]}
        self.assertIn("newcomer", ids)
        self.assertNotIn("chatterbox", ids)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"),
            before + 30,
        )
        self.assertEqual(
            self.store.earned_of(self.chat, "wxid_a"), 100)
        again = self.achievements.evaluate(self.chat, "wxid_a")
        self.assertEqual(again["new"], [])

    def test_qualification_helpers(self):
        definition = next(
            item for item in ACHIEVEMENTS if item["id"] == "collector")
        self.assertFalse(AchievementService.qualifies(
            definition, {"equipment_count": 4}))
        self.assertTrue(AchievementService.qualifies(
            definition, {"equipment_count": 5}))
        complete = next(
            item for item in ACHIEVEMENTS if item["id"] == "fish_master")
        self.assertFalse(AchievementService.qualifies(
            complete, {"species_count": 5, "species_total": 6}))
        self.assertTrue(AchievementService.qualifies(
            complete, {"species_count": 6, "species_total": 6}))

    def test_achievement_commands_and_title(self):
        self.add_member(earned=100)
        reply = self.run_cmd("成就")
        self.assertIn("新解锁", reply)
        self.assertIn("群新人", reply)
        titles = self.run_cmd("称号")
        self.assertIn("群新人", titles)
        equipped = self.run_cmd("佩戴称号 群新人")
        self.assertIn("已佩戴称号", equipped)
        self.assertEqual(
            self.store.equipped_title(self.chat, "wxid_a"), "群新人")
        info = self.run_cmd("我的信息")
        self.assertIn("【群新人】", info)

    def test_unknown_title_rejected(self):
        self.add_member(earned=100)
        reply = self.run_cmd("佩戴称号 不存在的称号")
        self.assertIn("没有称号", reply)


class HelpUpgradeTests(unittest.TestCase):
    def test_help_has_endgame_section(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from src.admin import AdminCommandHandler

        config = SimpleNamespace(
            points_enabled=True, fun_enabled=True,
            equipment_enabled=True, duel_enabled=True,
            fishing_enabled=True, todo_enabled=True,
            summarize_enabled=True, boss_enabled=True,
            duel_daily_attempts=10, duel_per_fight_cap=20,
            shield_price=20, shield_duration_sec=14400,
            shield_max_sec=86400, fishing_hourly_attempts=30,
            fishing_max_batch=10, fishing_daily_cap=120,
            boss_cooldown_sec=1800,
        )
        reply = AdminCommandHandler(MagicMock()).handle(
            "帮助", "张三", config=config, is_admin=False)
        self.assertIn("【进阶挑战】", reply)
        self.assertIn("强化 装备名", reply)
        self.assertIn("挑战Boss", reply)
        self.assertIn("境界 / 突破", reply)
        self.assertIn("成就 / 称号", reply)


if __name__ == "__main__":
    unittest.main()
