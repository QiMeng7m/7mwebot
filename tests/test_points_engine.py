"""PointEngine tests — awards, anti-spam, caps, reply bonus, rebuild."""

import time
import unittest

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.points.engine import PointEngine
from src.points.store import PointStore


class _Msg:
    def __init__(self):
        self._n = 0

    def make(self, sender_id="wxid_a", sender_name="张三", content="好的",
             msg_type=1, chat_id="1@chatroom", ts=None):
        self._n += 1
        return {
            "message_id": f"m{self._n}",
            "chat_id": chat_id,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "msg_type": msg_type,
            "timestamp": int(time.time()) if ts is None else int(ts),
        }


class EngineTestBase(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.messages = MessageStore(self.conn)
        self.store = PointStore(self.conn, lock=self.messages.lock)
        self.config = BotConfig()
        self.config.points_enabled = True
        self.config.points_groups = ["*"]
        self.config.bot_display_name = "群聊小助手"
        self.engine = PointEngine(self.store, self.config)
        self.factory = _Msg()
        self.chat = "1@chatroom"

    def tearDown(self):
        self.conn.close()

    def earned(self, sender="wxid_a"):
        return self.store.earned_of(self.chat, sender)


class BasicAwardTests(EngineTestBase):
    def test_normal_first_message_has_no_daily_bonus(self):
        """Chatting does not count as signing in."""
        self.assertEqual(self.engine.award_for_message(self.factory.make()), 1)
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="收到")), 1)
        self.assertEqual(self.earned(), 2)

    def test_checkin_message_gets_daily_bonus(self):
        self.config.points_dedup_window = 3
        self.assertEqual(
            self.engine.award_for_message(
                self.factory.make(content="签到")), 11,
        )
        self.assertEqual(
            self.engine.award_for_message(
                self.factory.make(content="签到")), 0,
        )
        self.assertEqual(self.earned(), 11)

    def test_checkin_message_with_wcdb_prefix(self):
        self.assertEqual(
            self.engine.award_for_message(
                self.factory.make(content="wxid_a:\n签到")), 11,
        )

    def test_length_bonus(self):
        text = "今天一起去吃饭吧顺便看看最近的新电影怎么样"
        points = self.engine.award_for_message(
            self.factory.make(content=text))
        self.assertEqual(points, 1 + 1)  # base + length

    def test_media_bonus(self):
        points = self.engine.award_for_message(
            self.factory.make(content="", msg_type=3))
        self.assertEqual(points, 1 + 1)

    def test_system_message_ignored(self):
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="入群", msg_type=10000)), 0)

    def test_bot_message_ignored(self):
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(sender_name="群聊小助手")), 0)

    def test_stale_message_ignored(self):
        old = int(time.time()) - 3600
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(ts=old)), 0)

    def test_disabled_group_ignored(self):
        self.config.points_groups = ["别的群"]
        self.assertEqual(self.engine.award_for_message(self.factory.make()), 0)


class AntiSpamTests(EngineTestBase):
    def test_rate_limit(self):
        self.config.points_rate_limit_per_min = 2
        self.assertGreater(self.engine.award_for_message(
            self.factory.make(content="第一条")), 0)
        self.assertGreater(self.engine.award_for_message(
            self.factory.make(content="第二条")), 0)
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="第三条")), 0)

    def test_duplicate_text_not_scored(self):
        self.config.points_dedup_window = 3
        self.assertGreater(self.engine.award_for_message(
            self.factory.make(content="哈哈")), 0)
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="哈哈")), 0)

    def test_duplicate_text_scored_when_dedup_disabled(self):
        self.config.points_dedup_window = 0
        self.assertGreater(self.engine.award_for_message(
            self.factory.make(content="哈哈")), 0)
        self.assertGreater(self.engine.award_for_message(
            self.factory.make(content="哈哈")), 0)

    def test_rate_limit_zero_is_unlimited(self):
        self.config.points_rate_limit_per_min = 0
        for index in range(20):
            self.assertGreater(self.engine.award_for_message(
                self.factory.make(content=f"第{index}条不同的话")), 0)

    def test_daily_cap_zero_is_unlimited(self):
        self.config.points_daily_cap = 0
        for index in range(30):
            self.assertGreater(self.engine.award_for_message(
                self.factory.make(content=f"第{index}条不同的话")), 0)

    def test_daily_cap(self):
        self.config.points_daily_cap = 12
        # base 1 + check-in 10
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="签到")), 11)
        long_text = "今天一起去吃饭吧顺便看看最近的新电影怎么样"
        # Only 1 point of room left, so the length bonus is trimmed.
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content=long_text)), 1)
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(content="再来一条")), 0)

    def test_daily_cap_is_per_member(self):
        self.config.points_daily_cap = 20
        self.engine.award_for_message(self.factory.make(sender_id="wxid_a"))
        self.assertEqual(self.engine.award_for_message(
            self.factory.make(sender_id="wxid_b", content="签到")), 11)


class ReplyBonusTests(EngineTestBase):
    def setUp(self):
        super().setUp()
        self.engine.award_for_message(self.factory.make(
            sender_id="wxid_b", sender_name="李四", content="出发吧"))

    def test_reply_credits_quoted_member(self):
        msg = self.factory.make(
            sender_id="wxid_a", sender_name="张三",
            content="wxid_b:\n加油",
        )
        self.engine.award_for_message(msg)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_b"), 1 + 2)

    def test_self_reply_not_credited(self):
        msg = self.factory.make(
            sender_id="wxid_a", sender_name="张三",
            content="wxid_a:\n自言自语",
        )
        self.engine.award_for_message(msg)
        self.assertEqual(self.store.earned_of(self.chat, "wxid_a"), 1)


class RebuildTests(EngineTestBase):
    def test_rebuild_recreates_earn_events(self):
        for content in ("你好", "今天一起去吃饭吧顺便看看新电影"):
            msg = self.factory.make(content=content)
            self.messages.insert_message(msg)
            self.engine.award_for_message(msg)

        before = self.earned()
        self.assertGreater(before, 0)
        self.store.adjust(self.chat, "wxid_a", "张三", +4, "奖励")

        result = self.engine.rebuild(self.chat, days=1)
        self.assertEqual(result["messages"], 2)
        self.assertEqual(self.earned(), before + 4)

    def test_rebuild_keeps_manual_adjustment(self):
        self.store.adjust(self.chat, "wxid_a", "张三", +4, "奖励")
        self.engine.rebuild(self.chat, days=1)
        self.assertEqual(self.earned(), 4)


if __name__ == "__main__":
    unittest.main()
