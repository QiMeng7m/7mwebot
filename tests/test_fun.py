"""Fortune lots: drawing, formatting and 解签 persistence."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.fun import (
    FIXED_WEIGHTS,
    draw_lot,
    draw_lots,
    format_lot,
    load_lots_config,
    reset_lots_cache,
)


class DrawLotTests(unittest.TestCase):
    def tearDown(self):
        reset_lots_cache()

    def test_draw_lot_returns_configured_level(self):
        levels = load_lots_config()["levels"]
        lot = draw_lot()
        self.assertIn(lot["level"], [level["name"] for level in levels])
        self.assertTrue(lot["phrase"])

    def test_probabilities_are_fixed(self):
        self.assertEqual(load_lots_config()["weights"], FIXED_WEIGHTS)
        self.assertEqual(sum(FIXED_WEIGHTS), 100)
        self.assertEqual(len(FIXED_WEIGHTS), 6)

    def test_default_levels_start_with_top_tier(self):
        levels = load_lots_config()["levels"]
        self.assertEqual(len(levels), len(FIXED_WEIGHTS))
        self.assertEqual(levels[0]["name"], "上上签")
        self.assertEqual(levels[-1]["name"], "凶")

    def test_legacy_five_level_file_gains_top_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lots.json"
            path.write_text(json.dumps({"levels": [
                {"name": name, "emoji": "x", "phrases": [f"{name}签文"]}
                for name in ("大吉", "中吉", "小吉", "末吉", "凶")
            ]}, ensure_ascii=False), encoding="utf-8")
            import src.fun as fun
            with patch.object(fun, "_LOTS_PATH", path):
                reset_lots_cache()
                config = load_lots_config()
        self.assertEqual(len(config["levels"]), 6)
        self.assertEqual(config["levels"][0]["name"], "上上签")
        self.assertEqual(config["levels"][1]["name"], "大吉")
        self.assertEqual(config["levels"][1]["phrases"], ["大吉签文"])

    def test_draw_lots_formats_reply(self):
        reply = draw_lots("张三")
        self.assertTrue(reply.startswith("@张三 抽签结果："))
        self.assertIn("\n", reply)

    def test_format_lot(self):
        lot = {"level": "大吉", "emoji": "🎉", "phrase": "万事顺遂"}
        self.assertEqual(
            format_lot("张三", lot),
            "@张三 抽签结果：🎉 大吉\n万事顺遂",
        )


class DrawStoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_save_and_get_latest_draw(self):
        self.assertIsNone(self.store.get_fun_draw("1@chatroom", "wxid_a"))
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "小吉", "🍀", "第一条",
            day="2026-10-20",
        )
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "第二条",
            day="2026-10-21",
        )
        lot = self.store.get_fun_draw("1@chatroom", "wxid_a")
        self.assertEqual(lot["level"], "大吉")
        self.assertEqual(lot["phrase"], "第二条")
        self.assertEqual(lot["day"], "2026-10-21")

    def test_draws_are_scoped_to_chat(self):
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "签文",
            day="2026-10-21",
        )
        self.assertIsNone(self.store.get_fun_draw("2@chatroom", "wxid_a"))

    def test_reading_can_only_be_claimed_once_per_day(self):
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "签文",
            day="2026-10-21",
        )
        self.assertTrue(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-21"))
        self.assertFalse(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-21"))

    def test_reading_claim_is_released_on_failure(self):
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "签文",
            day="2026-10-21",
        )
        self.assertTrue(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-21"))
        self.store.release_fun_reading(
            "1@chatroom", "wxid_a", "2026-10-21")
        self.assertTrue(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-21"))

    def test_new_draw_resets_reading(self):
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "第一天",
            day="2026-10-20",
        )
        self.assertTrue(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-20"))
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "中吉", "🔵", "第二天",
            day="2026-10-21",
        )
        self.assertTrue(
            self.store.claim_fun_reading("1@chatroom", "wxid_a", "2026-10-21"))


if __name__ == "__main__":
    unittest.main()
