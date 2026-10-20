"""Router integration for points / equipment / duel commands."""

import tempfile
import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.nickname import NicknameService
from src.router import MessageRouter
from src.trigger.detector import TriggerDetector


class _NoAi:
    def summarize(self, *args, **kwargs):
        raise AssertionError("AI summarize must not be called")

    def chat(self, *args, **kwargs):
        raise AssertionError("AI chat must not be called")

    def proactive_chat(self, *args, **kwargs):
        raise AssertionError("AI proactive must not be called")

    def consolidate_memory(self, *args, **kwargs):
        return ""


class RouterPointsTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.factory_n = 0

        config = BotConfig()
        config.db_path = ":memory:"
        config.todo_enabled = False
        config.sticky_mention_enabled = False
        config.points_enabled = True
        config.points_groups = ["*"]
        config.equipment_enabled = True
        config.duel_enabled = True
        config.duel_groups = ["*"]
        config.admin_wxid = "wxid_admin"

        self.router = MessageRouter(
            store=self.store,
            detector=TriggerDetector(
                keywords=[], bot_display_name="群聊小助手",
            ),
            summarizer=_NoAi(),
            admin_handler=None,
            nickname_service=NicknameService(
                path=Path(tempfile.mkdtemp()) / "nicknames.json"
            ),
            config=config,
        )

    def tearDown(self):
        self.conn.close()

    def _message(self, content, sender_id="wxid_a", name="张三",
                 ts=None, is_at=False):
        self.factory_n += 1
        return {
            "message_id": f"msg{self.factory_n}",
            "chat_id": "123@chatroom",
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()) if ts is None else int(ts),
            "is_at_mentioned": is_at,
            "is_group": True,
        }

    def test_points_are_awarded_for_messages(self):
        self.router.handle(self._message("大家好"))
        self.assertGreater(
            self.router._points_store.earned_of(
                "123@chatroom", "wxid_a"), 0,
        )

    def test_bare_points_command(self):
        reply = self.router.handle(self._message("积分"))
        self.assertIsNotNone(reply)
        self.assertIn("积分", reply)

    def test_my_info_command(self):
        reply = self.router.handle(self._message("我的信息"))
        self.assertIn("余额", reply)
        self.assertIn("武力值", reply)

    def test_query_known_member(self):
        self.router.handle(self._message("大家好", "wxid_b", "李四"))
        reply = self.router.handle(self._message("查询 李四"))
        self.assertIn("李四", reply)
        self.assertIn("积分：", reply)

    def test_query_unknown_stays_silent(self):
        self.router.handle(self._message("大家好", "wxid_b", "李四"))
        self.assertIsNone(self.router.handle(
            self._message("查询 一下天气")))

    def test_shop_and_buy(self):
        self.router.handle(self._message(
            "今天一起去吃饭吧顺便看看最近的新电影怎么样"))
        reply = self.router.handle(self._message("商店"))
        self.assertIn("装备商店", reply)

    def test_activity_query_still_works(self):
        reply = self.router.handle(self._message("活跃查询"))
        self.assertIn("群活跃报告", reply)

    def test_stale_points_command_ignored(self):
        reply = self.router.handle(self._message(
            "积分", ts=int(time.time()) - 3600))
        self.assertIsNone(reply)

    def test_duel_command(self):
        self.router.handle(self._message("大家好", "wxid_b", "李四"))
        # Give both members enough points to pass novice protection.
        self.router._points_store.adjust(
            "123@chatroom", "wxid_a", "张三", 50, "测试")
        self.router._points_store.adjust(
            "123@chatroom", "wxid_b", "李四", 50, "测试")
        reply = self.router.handle(self._message("切磋 李四"))
        self.assertIn("胜率", reply)


if __name__ == "__main__":
    unittest.main()
