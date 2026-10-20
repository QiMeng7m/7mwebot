"""Tests for the 「活跃查询」 activity report and its router integration."""

import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.activity import (
    MAX_DAYS,
    activity_window_start,
    clean_command_text,
    format_activity_report,
    format_relative_time,
    parse_activity_query,
)
from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.nickname import NicknameService
from src.router import MessageRouter
from src.trigger.detector import TriggerDetector


class _FakeNicks:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    def resolve_name(self, wxid):
        return self.mapping.get(wxid, wxid)


class _MsgFactory:
    def __init__(self):
        self._n = 0

    def make(self, chat_id, sender_id, sender_name, ts, msg_type=1,
             content=""):
        self._n += 1
        return {
            "message_id": f"msg{self._n}",
            "chat_id": chat_id,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": content,
            "msg_type": msg_type,
            "timestamp": int(ts),
        }


def _local_ts(days_ago: int, hour: int, minute: int = 0) -> int:
    """Unix seconds for a local wall-clock time N days ago."""
    day = datetime.now() - timedelta(days=days_ago)
    return int(time.mktime(
        (day.year, day.month, day.day, hour, minute, 0, 0, 0, -1)
    ))


class ParseActivityQueryTests(unittest.TestCase):
    def test_plain_keyword_uses_default(self):
        self.assertEqual(parse_activity_query("活跃查询"), 15)

    def test_explicit_days(self):
        self.assertEqual(parse_activity_query("活跃查询 7"), 7)
        self.assertEqual(parse_activity_query("活跃查询7"), 7)
        self.assertEqual(parse_activity_query("活跃查询7天"), 7)
        self.assertEqual(parse_activity_query("活跃查询 7 天"), 7)
        self.assertEqual(parse_activity_query("活跃查询：7天"), 7)

    def test_not_a_command(self):
        for text in ("活跃查询一下", "请帮我活跃查询", "活跃查询 7天的数据",
                     "活跃查询 7.5", "活跃查询abc", ""):
            self.assertIsNone(parse_activity_query(text), text)

    def test_days_are_clamped(self):
        self.assertEqual(parse_activity_query("活跃查询 0"), 1)
        self.assertEqual(parse_activity_query("活跃查询 500"), MAX_DAYS)


class CleanCommandTextTests(unittest.TestCase):
    def test_strips_bot_mention(self):
        self.assertEqual(
            clean_command_text("@群聊小助手 活跃查询", "群聊小助手"),
            "活跃查询",
        )

    def test_strips_invisible_separator(self):
        self.assertEqual(
            clean_command_text("@群聊小助手 活跃查询", "群聊小助手"),
            "活跃查询",
        )

    def test_strips_reply_quote_prefix(self):
        self.assertEqual(
            clean_command_text("wxid_abc123:\n活跃查询", ""),
            "活跃查询",
        )


class TimeHelperTests(unittest.TestCase):
    def test_window_starts_at_local_midnight(self):
        now = int(time.time())
        start = datetime.fromtimestamp(activity_window_start(15, now))
        self.assertEqual(
            (start.hour, start.minute, start.second), (0, 0, 0)
        )
        self.assertEqual(
            start.date(),
            (datetime.now() - timedelta(days=14)).date(),
        )

    def test_relative_time(self):
        now = int(time.time())
        self.assertEqual(format_relative_time(now - 10, now), "刚刚")
        self.assertEqual(format_relative_time(now - 120, now), "2分钟前")
        self.assertEqual(format_relative_time(now - 7200, now), "2小时前")
        self.assertEqual(format_relative_time(now - 3 * 86400, now), "3天前")


class StoreActivityTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.factory = _MsgFactory()
        self.chat = "123@chatroom"
        self.now = int(time.time())
        self.since = activity_window_start(15, self.now)

        # A: 2 today + 1 yesterday
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_a", "张三", _local_ts(0, 10, 0)))
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_a", "张三", _local_ts(0, 11, 0)))
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_a", "张三", _local_ts(1, 9, 0)))
        # B: 1 today at 22:xx
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_b", "李四", _local_ts(0, 22, 0)))
        # C: system message today (ignored) + human message 20 days ago
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_c", "王五", _local_ts(0, 12, 0), msg_type=10000))
        self.store.insert_message(self.factory.make(
            self.chat, "wxid_c", "王五", _local_ts(20, 12, 0)))

    def tearDown(self):
        self.conn.close()

    def test_totals_and_ranking(self):
        data = self.store.get_activity_report_data(self.chat, self.since)
        self.assertEqual(data["total"], 4)
        self.assertEqual(
            [(s["sender_id"], s["message_count"]) for s in data["senders"]],
            [("wxid_a", 3), ("wxid_b", 1)],
        )

    def test_dormant_members(self):
        data = self.store.get_activity_report_data(self.chat, self.since)
        dormant_ids = [d["sender_id"] for d in data["dormant"]]
        self.assertEqual(dormant_ids, ["wxid_c"])
        self.assertLess(data["dormant"][0]["last_ts"], self.since)

    def test_exclude_trigger_message(self):
        trigger = self.factory.make(
            self.chat, "wxid_a", "张三", _local_ts(0, 13, 0))
        self.store.insert_message(trigger)

        data = self.store.get_activity_report_data(
            self.chat, self.since,
            exclude_message_id=trigger["message_id"],
        )
        senders = {s["sender_id"]: s for s in data["senders"]}
        self.assertEqual(senders["wxid_a"]["message_count"], 3)
        self.assertEqual(data["total"], 4)


class FormatActivityReportTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.since = activity_window_start(15, self.now)
        self.data = {
            "senders": [
                {"sender_id": "wxid_a", "sender_name": "张三",
                 "message_count": 60, "last_ts": self.now - 120},
                {"sender_id": "wxid_b", "sender_name": "李四",
                 "message_count": 40, "last_ts": self.now - 2 * 86400},
            ],
            "dormant": [
                {"sender_id": "wxid_c", "sender_name": "王五",
                 "last_ts": self.now - 20 * 86400},
            ],
            "total": 100,
        }

    def _format(self, data=None, nicks=None):
        return format_activity_report(
            data or self.data,
            nicks or _FakeNicks(),
            chat_id="123@chatroom",
            group_name="摸鱼群",
            requester_name="张三",
            days=15,
            now_ts=self.now,
            since_ts=self.since,
        )

    def test_contains_all_sections(self):
        report = self._format()
        self.assertIn("群活跃报告", report)
        self.assertIn("· 摸鱼群", report)
        self.assertIn("100条", report)
        self.assertIn("发言 TOP2", report)
        self.assertIn("1. 张三 60条(60%)", report)
        self.assertIn("2. 李四 40条(40%)", report)
        self.assertNotIn("每日", report)
        self.assertNotIn("活跃时段", report)
        self.assertIn("窗口内未发言：1 人（王五）", report)

    def test_empty_window(self):
        report = self._format(data={
            "senders": [], "dormant": [], "total": 0,
        })
        self.assertIn("统计范围内暂无消息。", report)

    def test_display_is_limited_to_top_five(self):
        data = dict(self.data)
        data["senders"] = [
            {"sender_id": f"wxid_{i}", "sender_name": f"用户{i}",
             "message_count": 10 - i, "last_ts": self.now - 60}
            for i in range(7)
        ]
        data["total"] = sum(s["message_count"] for s in data["senders"])
        report = self._format(data=data)
        self.assertIn("发言 TOP5", report)
        self.assertIn("其余 2 人共 9 条", report)
        self.assertNotIn("6. 用户5", report)

    def test_duplicate_names_are_disambiguated(self):
        data = dict(self.data)
        data["senders"] = [
            {"sender_id": "wxid_a", "sender_name": "张三",
             "message_count": 60, "last_ts": self.now - 60},
            {"sender_id": "wxid_b", "sender_name": "张三",
             "message_count": 40, "last_ts": self.now - 60},
        ]
        report = self._format(data=data)
        self.assertIn("张三", report)
        self.assertIn("张三(2)", report)


class RouterActivityTests(unittest.TestCase):
    def setUp(self):
        self.conn = initialize_db(":memory:")
        self.store = MessageStore(self.conn)
        self.factory = _MsgFactory()

        class _NoAiSummarizer:
            def summarize(self, *args, **kwargs):
                raise AssertionError("AI summarize must not be called")

            def chat(self, *args, **kwargs):
                raise AssertionError("AI chat must not be called")

            def consolidate_memory(self, *args, **kwargs):
                return ""

        config = BotConfig()
        config.db_path = ":memory:"
        config.todo_enabled = False
        config.sticky_mention_enabled = False

        nicks_path = Path(tempfile.mkdtemp()) / "nicknames.json"
        self.router = MessageRouter(
            store=self.store,
            detector=TriggerDetector(
                keywords=[], bot_display_name="群聊小助手",
            ),
            summarizer=_NoAiSummarizer(),
            admin_handler=None,
            nickname_service=NicknameService(path=nicks_path),
            config=config,
        )

    def tearDown(self):
        self.conn.close()

    def _message(self, content, ts=None, is_at=False):
        msg = self.factory.make(
            "123@chatroom", "wxid_a", "张三",
            ts if ts is not None else int(time.time()),
            content=content,
        )
        msg["is_at_mentioned"] = is_at
        msg["is_group"] = True
        return msg

    def test_bare_keyword_triggers_report(self):
        reply = self.router.handle(self._message("活跃查询"))
        self.assertIsNotNone(reply)
        self.assertIn("群活跃报告", reply)

    def test_bot_mention_keyword_triggers_report(self):
        reply = self.router.handle(
            self._message("@群聊小助手 活跃查询 7", is_at=True)
        )
        self.assertIsNotNone(reply)
        self.assertIn("最近7天", reply)

    def test_stale_command_is_ignored(self):
        reply = self.router.handle(
            self._message("活跃查询", ts=int(time.time()) - 3600)
        )
        self.assertIsNone(reply)

    def test_non_command_is_not_intercepted(self):
        reply = self.router.handle(self._message("随便聊聊"))
        self.assertIsNone(reply)


if __name__ == "__main__":
    unittest.main()
