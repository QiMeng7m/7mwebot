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


class _FakeChatAi(_NoAi):
    def __init__(self):
        self.last_message = ""
        self.last_context = None

    def chat(self, *, message, context_messages=None, **kwargs):
        self.last_message = message
        self.last_context = context_messages
        return "AI解签：今天适合摸鱼。"


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

    def test_checkin_at_mention_with_custom_id_prefix(self):
        """Custom WeChat IDs prefix group text just like wxid_*."""
        reply = self.router.handle(self._message(
            "zy-519:\n@群聊小助手 签到",
            sender_id="zy-519", name="降娄", is_at=True,
        ))
        self.assertIn("签到成功", reply)

    def test_draw_lots_at_mention_with_custom_id_prefix(self):
        reply = self.router.handle(self._message(
            "canglan1992:\n@群聊小助手 抽签",
            sender_id="canglan1992", name="See.", is_at=True,
        ))
        self.assertIn("抽签结果", reply)

    def test_draw_lots_bare_text(self):
        """「抽签」 works without an @mention."""
        reply = self.router.handle(self._message("抽签"))
        self.assertIn("抽签结果", reply)
        self.assertIn("解签", reply)

    def test_read_lot_without_draw(self):
        reply = self.router.handle(self._message("解签"))
        self.assertIn("先发「抽签」", reply)

    def test_read_lot_uses_ai_and_context(self):
        self.router.handle(self._message("抽签"))
        fake = _FakeChatAi()
        self.router._summarizer = fake
        reply = self.router.handle(self._message("解签"))
        self.assertIn("AI解签", reply)
        # The prompt carries the drawn lot, and the chat context is fed.
        self.assertIn("抽到的是", fake.last_message)
        self.assertIsNotNone(fake.last_context)

    def test_draw_lots_only_once_per_day(self):
        first = self.router.handle(self._message("抽签"))
        second = self.router.handle(self._message("抽签"))
        self.assertIn("抽签结果", first)
        self.assertIn("今天已经抽过一签", second)
        # The original lot is echoed, not re-drawn.
        first_line = next(
            line for line in first.splitlines() if "抽签结果" in line)
        self.assertIn(first_line, second)

    def test_read_lot_only_once_per_day(self):
        self.router.handle(self._message("抽签"))
        self.router._summarizer = _FakeChatAi()
        first = self.router.handle(self._message("解签"))
        second = self.router.handle(self._message("解签"))
        self.assertIn("AI解签", first)
        self.assertIn("今天已经解过签", second)

    def test_read_lot_can_retry_after_ai_failure(self):
        class _BoomAi(_NoAi):
            def chat(self, *args, **kwargs):
                raise RuntimeError("boom")

        self.router.handle(self._message("抽签"))
        self.router._summarizer = _BoomAi()
        failed = self.router.handle(self._message("解签"))
        self.assertIn("掉线", failed)

        self.router._summarizer = _FakeChatAi()
        retried = self.router.handle(self._message("解签"))
        self.assertIn("AI解签", retried)

    def test_read_lot_requires_todays_draw(self):
        # Yesterday's draw does not unlock today's reading.
        self.router._store.save_fun_draw(
            "123@chatroom", "wxid_a", "张三", "大吉", "🎉", "昨天的签",
            day="2000-01-01",
        )
        reply = self.router.handle(self._message("解签"))
        self.assertIn("今天还没有抽签", reply)

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
