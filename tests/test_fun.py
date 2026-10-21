"""Fortune lots: drawing, formatting and 解签 persistence."""

import unittest

from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.fun import (
    FIXED_WEIGHTS,
    draw_lot,
    draw_lots,
    format_lot,
    load_lots_config,
)


class DrawLotTests(unittest.TestCase):
    def test_draw_lot_returns_configured_level(self):
        levels = load_lots_config()["levels"]
        lot = draw_lot()
        self.assertIn(lot["level"], [level["name"] for level in levels])
        self.assertTrue(lot["phrase"])

    def test_probabilities_are_fixed(self):
        self.assertEqual(load_lots_config()["weights"], FIXED_WEIGHTS)
        self.assertEqual(sum(FIXED_WEIGHTS), 100)

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
