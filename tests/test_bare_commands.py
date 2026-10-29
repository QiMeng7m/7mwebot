"""Bare-text routing: 帮助 / 待办 / 管理命令 without @mention."""

import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from src.admin import AdminCommandHandler
from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.router import MessageRouter
from src.trigger.detector import TriggerDetector


class _StubSummarizer:
    def summarize(self, messages, requester_name):
        return object()

    def format_summary_for_reply(self, result, requester_name):
        return "@张三 测试总结"

    def chat(self, *args, **kwargs):
        return "AI 回复"

    def consolidate_memory(self, *args, **kwargs):
        return ""


class BareCommandRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.now = int(time.time())
        self.chat = "123@chatroom"

        self.config = BotConfig()
        self.config.db_path = os.path.join(self.tmp.name, "test.db")
        self.config.todo_enabled = True
        self.config.todo_groups = ["*"]
        self.config.admin_wxid = "wxid_admin"
        self.config.sticky_mention_enabled = False

        self.nicks = MagicMock()
        self.nicks.resolve_name.side_effect = lambda sid: sid
        self.nicks.load.return_value = {"wxid_a": "张三"}
        self.admin = AdminCommandHandler(self.nicks)

        self.router = MessageRouter(
            store=self.store,
            detector=TriggerDetector(
                keywords=self.config.trigger_keywords,
                bot_display_name=self.config.bot_display_name,
            ),
            summarizer=_StubSummarizer(),
            admin_handler=self.admin,
            nickname_service=self.nicks,
            config=self.config,
        )
        self._counter = 0

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _message(self, content, sender_id="wxid_a", is_at=False):
        self._counter += 1
        return {
            "message_id": f"m{self._counter}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": self.now,
            "is_at_mentioned": is_at,
            "is_group": True,
        }

    def test_bare_help_returns_play_list(self):
        reply = self.router.handle(self._message("帮助"))

        self.assertIsNotNone(reply)
        self.assertIn("游玩清单", reply)
        self.assertIn("【钓鱼】", reply)
        self.assertNotIn("管理（仅管理员）", reply)

    def test_bare_help_alias(self):
        reply = self.router.handle(self._message("help"))
        self.assertIn("游玩清单", reply)

    def test_bare_help_section(self):
        reply = self.router.handle(self._message("帮助 钓鱼"))

        self.assertIsNotNone(reply)
        self.assertIn("【钓鱼】用法", reply)
        self.assertIn("每小时", reply)

    def test_bare_todo_add_and_view(self):
        added = self.router.handle(self._message("记一下 周五聚餐"))
        self.assertIsNotNone(added)
        self.assertIn("周五聚餐", added)

        listed = self.router.handle(self._message("查看待办"))
        self.assertIn("周五聚餐", listed)

    def test_bare_casual_phrase_does_not_trigger_todo(self):
        self.assertIsNone(self.router.handle(self._message("不要了")))
        self.assertIsNone(self.router.handle(self._message("搞定")))

    def test_bare_admin_command_works_for_admin(self):
        reply = self.router.handle(
            self._message("改名 wxid_b = 李四", sender_id="wxid_admin"),
        )
        self.assertIn("已更新", reply)
        self.nicks.update.assert_called_once_with("wxid_b", "李四")

    def test_bare_admin_command_ignored_for_member(self):
        self.assertIsNone(
            self.router.handle(self._message("改名 wxid_b = 李四")),
        )
        self.nicks.update.assert_not_called()

    def test_bare_refresh_nicknames_for_admin(self):
        reply = self.router.handle(
            self._message("刷新昵称", sender_id="wxid_admin"),
        )
        self.assertIn("昵称缓存已刷新", reply)
        self.assertIn("1 条", reply)

    def test_stale_bare_help_is_ignored(self):
        msg = self._message("帮助")
        msg["timestamp"] = self.now - 3600
        self.assertIsNone(self.router.handle(msg))


if __name__ == "__main__":
    unittest.main()
