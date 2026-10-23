"""Fishing: catalog validation, formulas, service, store and commands."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.duel import DuelService
from src.points.engine import PointEngine
from src.points.equipment import EquipmentCatalog
from src.points.fishing import (
    FishingService,
    catch_amount,
    effective_miss_chance,
    pick_species,
    roll_weight,
    species_weight,
)
from src.points.fishing_catalog import (
    FishingCatalog,
    default_catalog,
    validate_catalog,
)
from src.points.handler import PointHandler, looks_like_command
from src.points.levels import LevelTable
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.util import local_hour_start


class _Rng:
    """Deterministic random source: returns the given values in order."""

    def __init__(self, *values):
        self.values = list(values)
        self.index = 0

    def random(self) -> float:
        if self.index >= len(self.values):
            return 0.5
        value = self.values[self.index]
        self.index += 1
        return value


def _catalog_file(directory: str, **overrides) -> Path:
    data = {
        "rods": [
            {"id": "bamboo", "name": "竹竿", "price": 0, "default": True},
            {"id": "pro", "name": "好竿", "price": 200,
             "rare_bonus": 0.5, "value_bonus": 0.5},
        ],
        "baits": [
            {"id": "worm", "name": "蚯蚓", "price": 2,
             "rare_bonus": 0.1},
        ],
        "species": [
            {"id": "carp", "name": "鲤鱼", "rarity": "common", "weight": 1,
             "min_weight_g": 100, "max_weight_g": 200, "base_value": 10},
        ],
    }
    data.update(overrides)
    path = Path(directory) / "fishing.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class CatalogTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        catalog = validate_catalog(default_catalog())
        self.assertTrue(catalog["rods"])
        self.assertTrue(catalog["baits"])
        self.assertTrue(catalog["species"])
        defaults = [r for r in catalog["rods"] if r["default"]]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0]["price"], 0)

    def test_duplicate_species_is_rejected(self):
        data = default_catalog()
        data["species"].append(dict(data["species"][0]))
        with self.assertRaises(ValueError):
            validate_catalog(data)

    def test_bad_rarity_is_rejected(self):
        data = default_catalog()
        data["species"][0]["rarity"] = "mythic"
        with self.assertRaises(ValueError):
            validate_catalog(data)

    def test_two_default_rods_rejected(self):
        data = default_catalog()
        data["rods"][1]["default"] = True
        with self.assertRaises(ValueError):
            validate_catalog(data)

    def test_missing_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = FishingCatalog(Path(tmp) / "missing.json")
            self.assertTrue(catalog.load()["species"])
            self.assertEqual(catalog.default_rod()["id"], "bamboo")
            self.assertEqual(catalog.delay_for("legendary"), 60)

    def test_delay_validation(self):
        data = default_catalog()
        self.assertEqual(data["delays"]["rare"], 15)
        data["delays"]["rare"] = 999
        with self.assertRaises(ValueError):
            validate_catalog(data)

    def test_lookup_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = FishingCatalog(_catalog_file(tmp))
            self.assertEqual(catalog.find_rod("好竿")["id"], "pro")
            self.assertEqual(catalog.find_bait("蚯蚓")["id"], "worm")


class FormulaTests(unittest.TestCase):
    def test_rare_bonus_only_boosts_rare_species(self):
        common = {"rarity": "common", "weight": 10}
        rare = {"rarity": "rare", "weight": 10}
        self.assertEqual(species_weight(common, 0.5), 10)
        self.assertAlmostEqual(species_weight(rare, 0.5), 15)

    def test_pick_species_boundaries(self):
        species = [
            {"id": "a", "rarity": "common", "weight": 1},
            {"id": "b", "rarity": "common", "weight": 1},
        ]
        self.assertEqual(pick_species(species, 0, 0.0)["id"], "a")
        self.assertEqual(pick_species(species, 0, 0.999)["id"], "b")

    def test_roll_weight_stays_in_range(self):
        species = {"min_weight_g": 100, "max_weight_g": 200}
        self.assertEqual(roll_weight(species, 0.0), 100)
        self.assertEqual(roll_weight(species, 0.999999), 200)
        for roll in (0.1, 0.5, 0.9):
            value = roll_weight(species, roll)
            self.assertGreaterEqual(value, 100)
            self.assertLessEqual(value, 200)

    def test_catch_amount_grows_with_size_and_gear(self):
        species = {"min_weight_g": 100, "max_weight_g": 200,
                   "base_value": 10}
        small = catch_amount(species, 100)
        big = catch_amount(species, 200)
        geared = catch_amount(species, 200, rod_value_bonus=0.5)
        self.assertLess(small, big)
        self.assertGreater(geared, big)
        self.assertEqual(catch_amount(
            {"min_weight_g": 1, "max_weight_g": 2, "base_value": 0}, 1), 0)

    def test_miss_chance_is_clamped(self):
        self.assertAlmostEqual(effective_miss_chance(0.15, 0.0), 0.15)
        self.assertAlmostEqual(effective_miss_chance(0.15, 0.04), 0.11)
        self.assertAlmostEqual(effective_miss_chance(0.1, 0.5), 0.02)
        self.assertAlmostEqual(effective_miss_chance(0.9, 0.0), 0.5)


class ServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.fishing_enabled = True
        self.config.fishing_groups = ["*"]
        self.config.fishing_hourly_attempts = 30
        self.config.fishing_daily_cap = 0
        self.config.fishing_cooldown_sec = 0
        self.config.fishing_max_batch = 10
        self.config.fishing_miss_chance = 0.15
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = FishingCatalog(_catalog_file(self.tmp.name))
        self.fishing = FishingService(self.store, self.config, self.catalog)
        self.chat = "1@chatroom"
        self._n = 0
        self.add_member("wxid_a", "张三", earned=100)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_member(self, sender_id, name, earned=0):
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

    def msg(self, sender_id="wxid_a", name="张三", message_id=None):
        self._n += 1
        return {
            "message_id": message_id or f"fish-{self._n}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": "钓鱼",
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def cast(self, *rng_values, count=1, now_ts=2_000_000_000,
             message_id=None):
        return self.fishing.cast(
            self.msg(message_id=message_id), count=count,
            rng=_Rng(*rng_values), now_ts=now_ts,
        )


class ServiceTests(ServiceTestBase):
    def test_fishing_adds_balance_but_not_earned(self):
        result = self.cast(0.9, 0.0, 0.0)
        self.assertTrue(result.ok)
        self.assertGreater(result.total_value, 0)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 100)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"),
            100 + result.total_value,
        )
        board = self.store.board(self.chat, "total", 10)
        self.assertEqual(board[0]["points"], 100)
        self.assertEqual(
            board[0]["balance"], 100 + result.total_value,
        )

    def test_miss_is_logged_but_pays_nothing(self):
        result = self.cast(0.0)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_value, 0)
        self.assertFalse(result.casts[0].species_id)
        self.assertEqual(
            self.store.fishing_attempts_since(
                self.chat, "wxid_a", local_hour_start(2_000_000_000)), 1,
        )
        self.assertEqual(self.store.balance_of(self.chat, "wxid_a"), 100)

    def test_hourly_attempt_limit(self):
        self.config.fishing_hourly_attempts = 2
        result = self.cast(0.9, 0.0, 0.0, count=5)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.casts), 2)
        blocked = self.cast(0.9, 0.0, 0.0, now_ts=2_000_000_100)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "attempts")
        self.assertIn("本小时", blocked.reply)
        self.assertEqual(
            blocked.wait_seconds,
            local_hour_start(2_000_000_100) + 3600 - 2_000_000_100)

    def test_hourly_attempt_limit_resets_on_next_hour(self):
        self.config.fishing_hourly_attempts = 1
        hour_start = local_hour_start(2_000_000_000)
        self.assertTrue(self.cast(0.9, 0.0, 0.0, now_ts=hour_start).ok)
        blocked = self.cast(0.9, 0.0, 0.0, now_ts=hour_start + 60)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "attempts")
        allowed = self.cast(0.9, 0.0, 0.0, now_ts=hour_start + 3600)
        self.assertTrue(allowed.ok)
        self.assertEqual(allowed.attempts_used, 1)

    def test_stats_report_hourly_attempts_and_reset(self):
        self.cast(0.9, 0.0, 0.0, now_ts=2_000_000_000)
        stats = self.fishing.stats(
            self.chat, "wxid_a", now_ts=2_000_000_000)
        self.assertEqual(stats["attempts"], 1)
        hour_start = local_hour_start(2_000_000_000)
        self.assertEqual(stats["attempts_reset_in"],
                         hour_start + 3600 - 2_000_000_000)

    def test_daily_cap_truncates_and_blocks(self):
        self.config.fishing_daily_cap = 3
        result = self.cast(0.9, 0.0, 0.0, count=5)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_value, 3)
        self.assertEqual(result.earned_today, 3)
        blocked = self.cast(0.9, 0.0, 0.0, now_ts=2_000_000_100)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "cap")

    def test_cooldown_blocks_next_cast(self):
        self.config.fishing_cooldown_sec = 10
        self.assertTrue(self.cast(0.9, 0.0, 0.0).ok)
        blocked = self.cast(
            0.9, 0.0, 0.0, now_ts=2_000_000_005)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.code, "cooldown")
        self.assertTrue(self.cast(
            0.9, 0.0, 0.0, now_ts=2_000_000_011).ok)

    def test_replaying_same_message_is_idempotent(self):
        first = self.cast(0.9, 0.0, 0.0, message_id="dup-1")
        self.assertTrue(first.ok)
        second = self.cast(
            0.9, 0.0, 0.0, message_id="dup-1", now_ts=2_000_000_100)
        self.assertFalse(second.ok)
        self.assertEqual(second.code, "duplicate")
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"),
            100 + first.total_value,
        )

    def test_rod_bonus_is_applied(self):
        self.store.adjust(self.chat, "wxid_a", "张三", 200)
        ok, _, _ = self.store.buy_fishing_rod(
            self.chat, "wxid_a", "张三", self.catalog.find_rod("pro"))
        self.assertTrue(ok)
        result = self.cast(0.9, 0.0, 0.5)
        base = catch_amount(
            {"min_weight_g": 100, "max_weight_g": 200, "base_value": 10},
            150,
        )
        self.assertGreater(result.total_value, base)

    def test_bait_is_consumed_and_bonuses_apply(self):
        self.store.adjust(self.chat, "wxid_a", "张三", 100)
        ok, _, _ = self.store.buy_fishing_bait(
            self.chat, "wxid_a", "张三",
            self.catalog.find_bait("worm"), 2,
        )
        self.assertTrue(ok)
        result = self.cast(0.9, 0.0, 0.0)
        self.assertTrue(result.ok)
        stock = [
            row for row in self.store.fishing_inventory(
                self.chat, "wxid_a")
            if row["kind"] == "bait"
        ]
        self.assertEqual(stock[0]["quantity"], 1)
        self.assertEqual(result.casts[0].bait_id, "worm")

    def test_buy_rod_without_balance_fails(self):
        ok, code, balance = self.store.buy_fishing_rod(
            self.chat, "wxid_a", "张三", self.catalog.find_rod("pro"))
        self.assertFalse(ok)
        self.assertEqual(code, "insufficient")
        self.assertEqual(balance, 100)

    def test_buy_bait_without_balance_fails(self):
        ok, code, _ = self.store.buy_fishing_bait(
            self.chat, "wxid_a", "张三",
            self.catalog.find_bait("worm"), 99,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "insufficient")

    def test_disabled_blocks_casting(self):
        self.config.fishing_enabled = False
        result = self.cast(0.9, 0.0, 0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "disabled")

    def test_stats_collection_and_totals(self):
        self.cast(0.9, 0.0, 0.0)
        stats = self.fishing.stats(
            self.chat, "wxid_a", now_ts=2_000_000_000)
        self.assertEqual(stats["catches"], 1)
        self.assertEqual(stats["caught_species"], ["carp"])
        self.assertEqual(stats["attempts"], 1)
        self.assertGreater(stats["total_value"], 0)

    def test_rarer_catch_has_longer_feedback_delay(self):
        catalog = FishingCatalog(_catalog_file(
            self.tmp.name,
            species=[
                {"id": "carp", "name": "鲤鱼", "rarity": "common",
                 "weight": 1, "min_weight_g": 100,
                 "max_weight_g": 200, "base_value": 10},
                {"id": "koi", "name": "锦鲤", "rarity": "legendary",
                 "weight": 1, "min_weight_g": 500,
                 "max_weight_g": 3000, "base_value": 100},
            ],
            delays={"miss": 5, "junk": 5, "common": 5,
                    "rare": 15, "epic": 30, "legendary": 60},
        ))
        fishing = FishingService(self.store, self.config, catalog)
        result = fishing.cast(
            self.msg(), rng=_Rng(0.9, 0.9, 0.0),
            now_ts=2_000_000_500,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.casts[0].species_id, "koi")
        self.assertEqual(result.casts[0].delay_sec, 60)
        self.assertEqual(result.delay_sec, 60)

    def test_common_catch_returns_quickly(self):
        result = self.cast(0.9, 0.0, 0.0)
        self.assertEqual(result.delay_sec, 5)


class HandlerTests(unittest.TestCase):
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
        self.config.fishing_enabled = True
        self.config.fishing_groups = ["*"]
        self.config.fishing_cooldown_sec = 0
        self.config.fishing_daily_cap = 0
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = EquipmentCatalog(None)
        self.levels = LevelTable(None)
        self.fishing_catalog = FishingCatalog(_catalog_file(self.tmp.name))
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog)
        self.fishing = FishingService(
            self.store, self.config, self.fishing_catalog)
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels, engine=PointEngine(self.store, self.config),
            fishing=self.fishing,
        )
        self.chat = "1@chatroom"
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
            self.chat, "earn", [PointAward("wxid_a", "张三", "base", 100)])
        self._n = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def msg(self, content):
        self._n += 1
        return {
            "message_id": f"cmd-{self._n}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def handle(self, content):
        return self.handler.handle(content, self.msg(content))

    def test_looks_like_command(self):
        for text in ("钓鱼", "钓鱼 5", "鱼竿", "鱼饵", "鱼篓",
                     "图鉴", "钓鱼榜", "钓鱼榜 今日", "买鱼竿 竹竿",
                     "买鱼饵 红虫 5", "钓鱼记录"):
            self.assertTrue(looks_like_command(text), text)
        self.assertFalse(looks_like_command("今天去钓鱼吗"))

    def test_fish_command_adds_balance_not_points(self):
        reply = self.handle("钓鱼")
        self.assertIn("余额", reply)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 100)
        self.assertGreaterEqual(
            self.store.balance_of(self.chat, "wxid_a"), 100)

    def test_fish_reply_carries_feedback_delay(self):
        reply = self.handle("钓鱼")
        # The test catalog only has common fish (and a 5s miss delay).
        self.assertEqual(getattr(reply, "delay_sec", None), 5)
        self.assertIn("钓鱼中", getattr(reply, "ack_text", ""))
        self.assertIn("请稍等", getattr(reply, "ack_text", ""))

    def test_batch_ack_mentions_cast_count(self):
        reply = self.handle("钓鱼 3")
        self.assertIn("3 竿", getattr(reply, "ack_text", ""))

    def test_fish_batch_command(self):
        reply = self.handle("钓鱼 3")
        self.assertIn("3", reply)
        self.assertLessEqual(
            self.store.fishing_attempts_since(
                self.chat, "wxid_a", local_hour_start()),
            int(self.config.fishing_hourly_attempts),
        )

    def test_shop_listings(self):
        self.assertIn("鱼竿商店", self.handle("鱼竿"))
        self.assertIn("鱼饵商店", self.handle("鱼饵"))
        self.assertIn("鱼篓", self.handle("鱼篓"))
        self.assertIn("图鉴", self.handle("图鉴"))
        self.assertIn("钓鱼榜", self.handle("钓鱼榜"))
        self.assertIn("没有钓鱼记录", self.handle("钓鱼记录"))

    def test_buy_bait_by_alias_and_quantity(self):
        reply = self.handle("买鱼饵 蚯蚓 5")
        self.assertIn("购买成功", reply)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"), 100 - 2 * 5)
        stock = [
            row for row in self.store.fishing_inventory(
                self.chat, "wxid_a")
            if row["kind"] == "bait"
        ]
        self.assertEqual(stock[0]["quantity"], 5)

    def test_buy_rod_insufficient(self):
        reply = self.handle("买鱼竿 好竿")
        self.assertIn("余额不足", reply)

    def test_disabled_fishing_replies(self):
        self.config.fishing_enabled = False
        self.assertIn("未开启", self.handle("钓鱼"))
        self.assertIn("未开启", self.handle("鱼竿"))


if __name__ == "__main__":
    unittest.main()
