"""Tests for the endgame progression upgrade.

Covers the extended level ladder, the fourth equipment slot, the mythic
fishing tier, fishing proficiency and the duel career rank/board.
"""

import time
import unittest

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import (
    DuelService,
    duel_rank,
)
from src.points.engine import PointEngine
from src.points.equipment import (
    DEFAULT_ITEMS,
    DEFAULT_SLOTS,
    EquipmentCatalog,
    validate_catalog,
)
from src.points.fishing import FISHING_LEVELS, fishing_level
from src.points.fishing_catalog import (
    DEFAULT_DELAYS,
    DEFAULT_SPECIES,
    RARITIES,
    default_catalog,
    validate_catalog as validate_fishing,
)
from src.points.handler import PointHandler
from src.points.levels import DEFAULT_LEVELS, LevelTable
from src.points.models import PointAward
from src.points.store import PointStore


class LevelLadderTests(unittest.TestCase):
    def test_ladder_extends_past_the_old_cap(self):
        levels = LevelTable(None)
        self.assertEqual(DEFAULT_LEVELS[-1]["level"], 12)
        self.assertEqual(levels.for_points(120_000)["title"], "永恒")
        self.assertEqual(levels.for_points(3_000)["title"], "传说")
        self.assertEqual(levels.for_points(6_000)["title"], "宗师")
        self.assertIsNone(levels.next_for(120_000))


class EquipmentUpgradeTests(unittest.TestCase):
    def test_mount_slot_and_endgame_items(self):
        catalog = validate_catalog(
            {"slots": DEFAULT_SLOTS, "items": DEFAULT_ITEMS})
        slots = {slot["id"]: slot for slot in catalog["slots"]}
        self.assertIn("mount", slots)
        self.assertEqual(slots["mount"]["name"], "坐骑")

        for slot_id in slots:
            items = [i for i in catalog["items"] if i["slot"] == slot_id]
            self.assertGreaterEqual(len(items), 4, slot_id)

        names = {item["name"] for item in catalog["items"]}
        for name in ("太初剑", "不朽战甲", "轮回佩", "麒麟"):
            self.assertIn(name, names)

    def test_default_catalog_loads(self):
        catalog = EquipmentCatalog(None)
        self.assertEqual(len(catalog.slots()), 4)
        self.assertEqual(catalog.find("麒麟")["slot"], "mount")


class FishingUpgradeTests(unittest.TestCase):
    def test_mythic_tier_is_supported(self):
        self.assertIn("mythic", RARITIES)
        self.assertEqual(DEFAULT_DELAYS["mythic"], 90)
        catalog = validate_fishing(default_catalog())
        mythic = [s for s in catalog["species"] if s["rarity"] == "mythic"]
        self.assertGreaterEqual(len(mythic), 3)
        self.assertIn("九色神鱼", {s["name"] for s in mythic})

    def test_defaults_have_new_gear(self):
        catalog = validate_fishing(default_catalog())
        rod_names = {rod["name"] for rod in catalog["rods"]}
        bait_names = {bait["name"] for bait in catalog["baits"]}
        self.assertIn("太初竿", rod_names)
        self.assertIn("龙涎香", bait_names)

    def test_proficiency_thresholds_and_bonus(self):
        self.assertEqual(fishing_level(0)["title"], "新手")
        self.assertEqual(fishing_level(0)["rare_bonus"], 0.0)
        self.assertEqual(fishing_level(25)["title"], "钓客")
        self.assertEqual(fishing_level(2_000)["title"], "渔神")
        maxed = fishing_level(10_000)
        self.assertIsNone(maxed["next"])
        self.assertEqual(maxed["catches_to_next"], 0)
        # Thresholds must be strictly increasing.
        mins = [entry["min_catches"] for entry in FISHING_LEVELS]
        self.assertEqual(mins, sorted(set(mins)))


class DuelRankTests(unittest.TestCase):
    def test_rank_thresholds(self):
        self.assertEqual(duel_rank(0)["title"], "青铜")
        self.assertEqual(duel_rank(5)["title"], "白银")
        self.assertEqual(duel_rank(120)["title"], "战神")
        self.assertIsNone(duel_rank(999)["next"])
        self.assertEqual(duel_rank(3)["wins_to_next"], 2)


class DuelBoardTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.duel_enabled = True
        self.config.duel_groups = ["*"]
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog)
        self.chat = "1@chatroom"
        self._seq = 0

    def tearDown(self):
        self.conn.close()

    def _record(self, attacker, won, moved=3, defender="wxid_b"):
        self._seq += 1
        self.store.record_duel(
            self.chat, f"duel-{self._seq}", attacker, "张三",
            defender, "李四", 30, 25, 0.6, won, moved,
        )

    def test_record_counts_wins_and_stolen(self):
        self._record("wxid_a", True, 5)
        self._record("wxid_a", True, 4)
        self._record("wxid_a", False, 0)
        record = self.duels.record(self.chat, "wxid_a")
        self.assertEqual(record["wins"], 2)
        self.assertEqual(record["losses"], 1)
        self.assertEqual(record["stolen"], 9)
        self.assertEqual(record["rank"]["title"], "青铜")

    def test_leaderboard_orders_by_wins(self):
        self._record("wxid_a", True, 1)
        self._record("wxid_b", True, 2)
        self._record("wxid_b", True, 2)
        rows = self.duels.leaderboard(self.chat)
        self.assertEqual(rows[0]["attacker_id"], "wxid_b")
        self.assertEqual(rows[0]["wins"], 2)
        self.assertEqual(rows[0]["rank"]["title"], "青铜")


class DuelCommandTests(unittest.TestCase):
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
        self.messages.insert_message({
            "message_id": "seed-a",
            "chat_id": self.chat,
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": "seed",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        self.store.record_events(
            self.chat, "earn-a",
            [PointAward("wxid_a", "张三", "base", 100)],
        )
        self.store.record_duel(
            self.chat, "d1", "wxid_a", "张三", "wxid_b", "李四",
            30, 25, 0.6, True, 7,
        )

    def tearDown(self):
        self.conn.close()

    def _msg(self, content):
        return {
            "message_id": f"cmd-{content}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def test_duel_record_command(self):
        reply = self.handler.handle("切磋战绩", self._msg("切磋战绩"))
        self.assertIn("切磋战绩", reply)
        self.assertIn("青铜", reply)
        self.assertIn("胜 1", reply)

    def test_duel_board_command(self):
        reply = self.handler.handle("战神榜", self._msg("战神榜"))
        self.assertIn("战神榜", reply)
        self.assertIn("张三", reply)
        self.assertIn("1胜", reply)


if __name__ == "__main__":
    unittest.main()
