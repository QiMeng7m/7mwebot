"""Fortune lots: drawing, formatting and 解签 persistence."""

import unittest

from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.fun import draw_lot, draw_lots, format_lot, load_lots_config


class DrawLotTests(unittest.TestCase):
    def test_draw_lot_returns_configured_level(self):
        levels = load_lots_config()["levels"]
        lot = draw_lot()
        self.assertIn(lot["level"], [level["name"] for level in levels])
        self.assertTrue(lot["phrase"])

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
        )
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "第二条",
        )
        lot = self.store.get_fun_draw("1@chatroom", "wxid_a")
        self.assertEqual(lot["level"], "大吉")
        self.assertEqual(lot["phrase"], "第二条")

    def test_draws_are_scoped_to_chat(self):
        self.store.save_fun_draw(
            "1@chatroom", "wxid_a", "张三", "大吉", "🎉", "签文",
        )
        self.assertIsNone(self.store.get_fun_draw("2@chatroom", "wxid_a"))


if __name__ == "__main__":
    unittest.main()
