"""Phase-2 playability systems: sets, tasks, dungeon, market, pets..."""

import time
import unittest
from unittest.mock import patch

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import DuelService
from src.points.equipment import EquipmentCatalog
from src.points.expansion import (
    ExpansionService,
    affix_bonuses,
    combat_bonuses,
    set_bonuses,
    world_state,
)
from src.points.handler import PointHandler
from src.points.levels import LevelTable
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.skills import passive_effects
from src.points.util import local_week


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


class ExpansionTestBase(unittest.TestCase):
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
        self.expansion = ExpansionService(
            self.store, self.config, self.levels, self.catalog, self.duels)
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels,
        )
        self.chat = "1@chatroom"
        self._seq = 0

    def tearDown(self):
        self.conn.close()

    def add_member(self, sender_id="wxid_a", name="张三", earned=100,
                   chat_id=None):
        chat_id = chat_id or self.chat
        self._seq += 1
        self.messages.insert_message({
            "message_id": f"seed-{self._seq}",
            "chat_id": chat_id,
            "sender_id": sender_id,
            "sender_name": name,
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        if earned:
            self.store.record_events(
                chat_id, f"earn-{self._seq}",
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
        return self.handler.handle(content, self.msg(content, sender_id, name))

    def buy(self, name, sender_id="wxid_a", earned=50_000, name_="张三"):
        self.add_member(sender_id=sender_id, name=name_,
                        earned=earned)
        item = self.catalog.find(name)
        ok, code, _, _ = self.store.buy_item(
            self.chat, sender_id, name_, item,
            self.catalog.same_slot_ids(item),
        )
        self.assertTrue(ok, code)
        return item


class SetAndSkillTests(ExpansionTestBase):
    def test_two_piece_set_bonus(self):
        self.buy("仙灵甲")
        self.buy("仙灵佩", earned=0)
        bonuses = set_bonuses(
            self.store, self.catalog, self.chat, "wxid_a")
        self.assertAlmostEqual(bonuses.get("attack_pct", 0), 0.06)

    def test_skill_upgrade_uses_book(self):
        self.buy("星陨剑")
        self.expansion._add_item(
            self.chat, "wxid_a", "book", "skill_book", 1)
        before = passive_effects(
            self.store, self.catalog, self.chat, "wxid_a")
        reply = self.expansion.upgrade_skill(
            self.chat, "wxid_a", "张三", "剑意")
        self.assertIn("升级成功", reply)
        self.assertEqual(
            self.store.skill_level_of(self.chat, "wxid_a", "sword_intent"),
            2,
        )
        after = passive_effects(
            self.store, self.catalog, self.chat, "wxid_a")
        self.assertGreater(
            after.get("attack_pct", 0), before.get("attack_pct", 0))

    def test_summary_has_all_sections(self):
        self.add_member(earned=1000)
        summary = self.expansion.summary(self.chat, "wxid_a")
        for key in ("sets", "tasks", "dungeon", "arena", "guild",
                    "pet", "alchemy", "world", "rebirth"):
            self.assertIn(key, summary)


class TaskTests(ExpansionTestBase):
    def test_chat_task_progress_and_claim(self):
        self.add_member(earned=100)
        now = int(time.time())
        for index in range(10):
            self.messages.insert_message({
                "message_id": f"chat-{index}",
                "chat_id": self.chat,
                "sender_id": "wxid_a",
                "sender_name": "张三",
                "content": f"hello {index}",
                "msg_type": 1,
                "timestamp": now,
            })
        status = self.expansion.tasks_status(
            self.chat, "wxid_a", "张三", "daily")
        self.assertIn("群聊活跃", status)
        before = self.store.balance_of(self.chat, "wxid_a")
        reply = self.expansion.claim_task(
            self.chat, "wxid_a", "张三", "群聊活跃")
        self.assertIn("领取成功", reply)
        self.assertGreater(
            self.store.balance_of(self.chat, "wxid_a"), before)
        again = self.expansion.claim_task(
            self.chat, "wxid_a", "张三", "群聊活跃")
        self.assertIn("已经领取", again)


class DungeonArenaTests(ExpansionTestBase):
    def test_dungeon_clear_and_reward(self):
        self.add_member(earned=20_000)
        reply = self.expansion.challenge_dungeon(
            self.chat, "wxid_a", "张三", "", rng=_Rng(0.0))
        self.assertIn("通关", reply)
        self.assertEqual(
            self.expansion._dungeon_best(self.chat, "wxid_a"), 1)

    def test_arena_challenge_records_entry(self):
        self.add_member("wxid_a", "张三", 2000)
        self.add_member("wxid_b", "李四", 2000)
        reply = self.expansion.arena_challenge(
            self.chat, "wxid_a", "张三", rng=_Rng(0.0, 0.0))
        self.assertIn("擂台挑战", reply)
        entry = self.expansion._arena_entry(
            self.chat, local_week(), "wxid_a")
        self.assertGreaterEqual(entry["wins"] + entry["losses"], 1)

    def test_arena_skips_members_who_left_the_group(self):
        self.add_member("wxid_a", "张三", 2000)
        self.add_member("wxid_b", "李四", 2000)   # 已退群
        self.add_member("wxid_c", "王五", 2000)
        with patch(
            "src.points.expansion.load_group_members",
            return_value={"wxid_a": "张三", "wxid_c": "王五"},
        ):
            reply = self.expansion.arena_challenge(
                self.chat, "wxid_a", "张三", rng=_Rng(0.0, 0.0))
        self.assertIn("王五", reply)
        self.assertNotIn("李四", reply)

    def test_arena_keeps_everyone_when_no_member_snapshot(self):
        self.add_member("wxid_a", "张三", 2000)
        self.add_member("wxid_b", "李四", 2000)
        with patch(
            "src.points.expansion.load_group_members", return_value=None,
        ):
            reply = self.expansion.arena_challenge(
                self.chat, "wxid_a", "张三", rng=_Rng(0.0, 0.0))
        self.assertIn("李四", reply)


class GuildMarketTests(ExpansionTestBase):
    def test_guild_create_and_donate_levels_up(self):
        self.add_member(earned=5000)
        created = self.expansion.guild_create(
            self.chat, "wxid_a", "张三", "青云门")
        self.assertIn("创建成功", created)
        donated = self.expansion.guild_donate(
            self.chat, "wxid_a", "张三", "1000")
        self.assertIn("Lv2", donated)
        self.assertIn("青云门", self.expansion.guild_status(
            self.chat, "wxid_a", "张三"))

    def test_market_transfers_equipment_and_money(self):
        self.buy("木剑", sender_id="wxid_a", earned=5000)
        self.store.equip_item(
            self.chat, "wxid_a", "wood_sword", ["wood_sword"],
            equip=False,
        )
        listed = self.expansion.market_sell(
            self.chat, "wxid_a", "张三", "木剑 100")
        self.assertIn("已上架", listed)
        listing = self.conn.execute(
            "SELECT id FROM market_listings").fetchone()[0]
        self.add_member("wxid_b", "李四", 1000)
        before = self.store.balance_of(self.chat, "wxid_b")
        bought = self.expansion.market_buy(
            self.chat, "wxid_b", "李四", str(listing))
        self.assertIn("购买成功", bought)
        self.assertTrue(self.store.owns(self.chat, "wxid_b", "wood_sword"))
        self.assertLess(self.store.balance_of(self.chat, "wxid_b"), before)


class PetAlchemyTests(ExpansionTestBase):
    def test_pet_adopt_and_bonus(self):
        self.add_member(earned=5000)
        reply = self.expansion.pet_adopt(
            self.chat, "wxid_a", "张三", "灵狐")
        self.assertIn("领养成功", reply)
        bonus = combat_bonuses(
            self.store, self.catalog, self.chat, "wxid_a")
        self.assertGreater(bonus.get("flat_attack", 0), 0)

    def test_alchemy_gather_craft_use(self):
        self.add_member(earned=5000)
        self.expansion.gather(
            self.chat, "wxid_a", "张三", rng=_Rng(0.9))
        crafted = self.expansion.craft_pill(
            self.chat, "wxid_a", "张三", "聚气丹")
        self.assertIn("炼制成功", crafted)
        used = self.expansion.use_pill(
            self.chat, "wxid_a", "张三", "聚气丹")
        self.assertIn("服用", used)


class WorldRebirthTests(ExpansionTestBase):
    def test_world_state_is_stable(self):
        first = world_state(self.chat, now_ts=2_000_000_000)
        second = world_state(self.chat, now_ts=2_000_000_000)
        self.assertEqual(first["weather"]["id"], second["weather"]["id"])
        self.assertEqual(first["event"]["id"], second["event"]["id"])

    def test_rebirth_resets_realm_and_adds_bonus(self):
        self.add_member(earned=120_000)
        self.conn.execute(
            """INSERT INTO member_realms
               (chat_id, sender_id, realm, updated_at)
               VALUES (?, ?, 12, ?)""",
            (self.chat, "wxid_a", int(time.time())),
        )
        reply = self.expansion.rebirth(
            self.chat, "wxid_a", "张三")
        self.assertIn("转世成功", reply)
        self.assertEqual(
            self.store.realm_of(self.chat, "wxid_a"), 0)
        self.assertEqual(
            self.expansion._rebirth_cycle(self.chat, "wxid_a"), 1)
        bonus = combat_bonuses(
            self.store, self.catalog, self.chat, "wxid_a")
        self.assertGreater(bonus.get("attack_pct", 0), 0)


class HandlerRoutingTests(ExpansionTestBase):
    def test_expansion_commands_route(self):
        self.add_member(earned=1000)
        self.assertIn("套装", self.run_cmd("套装"))
        self.assertIn("任务", self.run_cmd("任务"))
        self.assertIn("秘境", self.run_cmd("秘境"))
        self.assertIn("宠物", self.run_cmd("宠物"))
        self.assertIn("门派", self.run_cmd("门派"))
        self.assertIn("天象", self.run_cmd("天象"))
        self.assertIn("跨群修炼榜", self.run_cmd("跨群榜"))
        self.assertIn("轮回", self.run_cmd("转世"))


class CrossBoardTests(ExpansionTestBase):
    def test_cross_board_aggregates_chats(self):
        self.add_member("wxid_a", "张三", 500)
        self.add_member("wxid_b", "李四", 300, chat_id="2@chatroom")
        reply = self.expansion.cross_board("张三")
        self.assertIn("跨群修炼榜", reply)
        self.assertIn("张三", reply)


if __name__ == "__main__":
    unittest.main()
