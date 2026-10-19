"""Router trigger tests: bare keyword summary and @mention routing."""

import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.nickname import NicknameService
from src.router import MessageRouter
from src.trigger.detector import TriggerDetector


class _StubSummarizer:
    def __init__(self):
        self.summarize_calls = 0
        self.chat_calls = 0

    def summarize(self, messages, requester_name):
        self.summarize_calls += 1
        return object()

    def format_summary_for_reply(self, result, requester_name):
        return f"@{requester_name} 测试总结"

    def chat(self, *args, **kwargs):
        self.chat_calls += 1
        return "AI 回复"

    def consolidate_memory(self, *args, **kwargs):
        return ""


class RouterTriggerTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.now = int(time.time())
        self.chat = "123@chatroom"

        # Someone else spoke recently so summary has content.
        self.store.insert_message({
            "message_id": "seed1",
            "chat_id": self.chat,
            "sender_id": "wxid_b",
            "sender_name": "李四",
            "content": "今晚吃什么",
            "msg_type": 1,
            "timestamp": self.now - 60,
        })

        self.config = BotConfig()
        self.config.db_path = ":memory:"
        self.config.todo_enabled = False
        self.config.sticky_mention_enabled = False
        self.summarizer = _StubSummarizer()
        self.router = MessageRouter(
            store=self.store,
            detector=TriggerDetector(
                keywords=self.config.trigger_keywords,
                bot_display_name=self.config.bot_display_name,
            ),
            summarizer=self.summarizer,
            admin_handler=None,
            nickname_service=NicknameService(path=Path(":memory:").parent / "no-nicks.json"),
            config=self.config,
        )
        self._counter = 0

    def tearDown(self):
        self.conn.close()

    def _message(self, content, ts=None, is_at=False):
        self._counter += 1
        return {
            "message_id": f"m{self._counter}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": content,
            "msg_type": 1,
            "timestamp": int(ts if ts is not None else self.now),
            "is_at_mentioned": is_at,
            "is_group": True,
        }

    def test_bare_keyword_triggers_summary(self):
        reply = self.router.handle(self._message("总结一下"))
        self.assertEqual(reply, "@张三 测试总结")
        self.assertEqual(self.summarizer.summarize_calls, 1)
        self.assertEqual(self.summarizer.chat_calls, 0)

    def test_stale_bare_keyword_is_ignored(self):
        reply = self.router.handle(
            self._message("总结一下", ts=self.now - 3600)
        )
        self.assertIsNone(reply)
        self.assertEqual(self.summarizer.summarize_calls, 0)

    def test_bare_non_keyword_is_ignored(self):
        reply = self.router.handle(self._message("随便聊聊"))
        self.assertIsNone(reply)
        self.assertEqual(self.summarizer.summarize_calls, 0)
        self.assertEqual(self.summarizer.chat_calls, 0)

    def test_at_mention_still_routes_to_chat(self):
        reply = self.router.handle(
            self._message("@群聊小助手 你好", is_at=True)
        )
        self.assertEqual(reply, "@张三 AI 回复")
        self.assertEqual(self.summarizer.chat_calls, 1)

    def test_summarize_disabled_falls_through(self):
        self.config.summarize_enabled = False
        reply = self.router.handle(self._message("总结一下"))
        self.assertIsNone(reply)
        self.assertEqual(self.summarizer.summarize_calls, 0)


if __name__ == "__main__":
    unittest.main()
