"""Command parsing / reply formatting tests for points and duels."""

import tempfile
import time
import unittest
from pathlib import Path

from src.config import BotConfig
from src.db.schema import initialize_db
from src.db.store import MessageStore
from src.nickname import NicknameService
from src.points.duel import DuelService
from src.points.engine import PointEngine
from src.points.equipment import EquipmentCatalog
from src.points.handler import PointHandler, looks_like_command
from src.points.levels import LevelTable
from src.points.models import PointAward
from src.points.store import PointStore
from src.points.util import local_day


class HandlerTestBase(unittest.TestCase):
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
        self.config.admin_wxid = "wxid_admin"
        self.levels = LevelTable(None)
        self.catalog = EquipmentCatalog(None)
        self.engine = PointEngine(self.store, self.config)
        self.duels = DuelService(
            self.store, self.config, self.levels, self.catalog,
        )
        self.nicks = NicknameService(
            path=Path(tempfile.mkdtemp()) / "nicknames.json"
        )
        self.handler = PointHandler(
            self.store, self.config, self.levels, self.catalog,
            self.duels, engine=self.engine, nickname_service=self.nicks,
        )
        self.chat = "1@chatroom"

    def tearDown(self):
        self.conn.close()

    def add_member(self, sender_id, name, earned=100):
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

    def msg(self, content, sender_id="wxid_a", name="张三"):
        self._seq = getattr(self, "_seq", 0) + 1
        return {
            "message_id": f"cmd-{time.time_ns()}-{getattr(self, '_seq', 0)}",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": sender_id,
            "sender_name": name,
            "content": content,
            "msg_type": 1,
            "timestamp": int(time.time()),
        }

    def run_cmd(self, content, sender_id="wxid_a", name="张三"):
        return self.handler.handle(content, self.msg(content, sender_id, name))


class LookLikeCommandTests(unittest.TestCase):
    def test_exact_commands(self):
        for text in ("签到", "积分", "我的信息", "积分榜", "商店", "武力值",
                     "切磋记录"):
            self.assertTrue(looks_like_command(text), text)

    def test_prefix_commands(self):
        self.assertTrue(looks_like_command("查询 张三"))
        self.assertTrue(looks_like_command("购买 木剑"))
        self.assertTrue(looks_like_command("切磋 @张三"))
        self.assertTrue(looks_like_command("@张三 切磋"))

    def test_normal_chat_is_not_a_command(self):
        for text in ("查询一下天气", "积分怎么算", "我们切磋一下", ""):
            self.assertFalse(looks_like_command(text), text)


class QueryTests(HandlerTestBase):
    def setUp(self):
        super().setUp()
        self.add_member("wxid_a", "张三", earned=128)
        self.add_member("wxid_b", "李四", earned=96)

    def test_query_by_name_without_mention(self):
        reply = self.run_cmd("查询 李四")
        self.assertIsNotNone(reply)
        self.assertIn("李四", reply)
        self.assertIn("积分：96", reply)
        self.assertIn("武力值：", reply)

    def test_query_self(self):
        reply = self.run_cmd("查询 我")
        self.assertIn("张三", reply)
        self.assertIn("积分：128", reply)

    def test_query_unknown_member_stays_silent(self):
        self.assertIsNone(self.run_cmd("查询 王五"))
        self.assertIsNone(self.run_cmd("查询 一下天气"))
        self.assertIsNone(self.run_cmd("查询 快递到哪了"))

    def test_ambiguous_name(self):
        self.add_member("wxid_c", "李四", earned=10)
        reply = self.run_cmd("查询 李四")
        self.assertIn("多个同名成员", reply)

    def test_my_info(self):
        reply = self.run_cmd("我的信息")
        self.assertIn("积分：128", reply)
        self.assertIn("余额：128", reply)
        self.assertIn("武力值：", reply)

    def test_member_card_has_level_and_rank(self):
        reply = self.run_cmd("积分")
        self.assertIn("Lv", reply)
        self.assertIn("本月", reply)
        self.assertIn("余额", reply)

    def test_board(self):
        reply = self.run_cmd("积分榜")
        self.assertIn("总榜", reply)
        self.assertIn("张三", reply)
        self.assertIn("128分", reply)
        self.assertIn("李四", reply)

    def test_season_board(self):
        reply = self.run_cmd("月榜")
        self.assertIn("赛季", reply)

    def test_rules(self):
        reply = self.run_cmd("积分规则")
        self.assertIn("每日封顶", reply)


class CheckinTests(HandlerTestBase):
    def test_checkin_strips_wcdb_wxid_prefix(self):
        """WCDB prefixes group text with "wxid_xxx:\\n"."""
        from src.points.handler import _clean_for_commands
        self.assertEqual(
            _clean_for_commands("wxid_vvi4cxesb04k22:\n签到"), "签到",
        )

    def test_checkin_with_wcdb_prefix_is_recognised(self):
        self.add_member("wxid_a", "张三", earned=0)
        msg = self.msg("签到", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n签到"
        self.assertEqual(
            self.handler.handle(msg["content"], msg),
            "@张三 签到成功，积分+10",
        )

    def test_checkin_with_custom_wechat_id_prefix(self):
        """Custom IDs (zy-519) prefix group messages just like wxid_*."""
        self.add_member("zy-519", "降娄", earned=0)
        msg = self.msg("签到", "zy-519", "降娄")
        msg["content"] = "zy-519:\n签到"
        self.assertEqual(
            self.handler.handle(msg["content"], msg),
            "@降娄 签到成功，积分+10",
        )

    def test_checkin_with_bot_group_alias(self):
        """Members @ the bot's group nickname instead of BOT_DISPLAY_NAME."""
        self.config.bot_display_name = "柒梦"
        self.config.bot_aliases = ["小柒"]
        self.add_member("wxid_a", "张三", earned=0)
        msg = self.msg("签到", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n@小柒 签到"
        self.assertEqual(
            self.handler.handle(msg["content"], msg),
            "@张三 签到成功，积分+10",
        )

    def test_checkin_awards_and_confirms(self):
        self.add_member("wxid_a", "张三", earned=0)
        reply = self.run_cmd("签到", sender_id="wxid_a", name="张三")
        self.assertEqual(reply, "@张三 签到成功，积分+10")
        self.assertTrue(self.store.has_reason_on_day(
            self.chat, "wxid_a", "daily_first", local_day(int(time.time())),
        ))

    def test_checkin_after_chatting_still_succeeds(self):
        """Chatting earlier in the day is not a check-in."""
        self.add_member("wxid_a", "张三", earned=0)
        self.engine.award_for_message({
            "message_id": "chat-1",
            "chat_id": self.chat,
            "group_name": "测试群",
            "sender_id": "wxid_a",
            "sender_name": "张三",
            "content": "早上好",
            "msg_type": 1,
            "timestamp": int(time.time()),
        })
        self.assertEqual(
            self.run_cmd("签到", sender_id="wxid_a", name="张三"),
            "@张三 签到成功，积分+10",
        )

    def test_checkin_reports_daily_first_already_on_message(self):
        """The router awards daily_first before the command handler runs."""
        self.add_member("wxid_a", "张三", earned=0)
        msg = self.msg("签到", "wxid_a", "张三")
        self.store.record_events(self.chat, msg["message_id"], [
            PointAward("wxid_a", "张三", "daily_first", 10),
        ])
        self.assertEqual(
            self.handler.handle("签到", msg),
            "@张三 签到成功，积分+10",
        )

    def test_checkin_twice_reports_already_done(self):
        self.add_member("wxid_a", "张三", earned=0)
        self.run_cmd("签到", sender_id="wxid_a", name="张三")
        self.assertEqual(
            self.run_cmd("签到", sender_id="wxid_a", name="张三"),
            "@张三 今天已经签到过啦，明天再来~",
        )


class ShopTests(HandlerTestBase):
    def setUp(self):
        super().setUp()
        self.add_member("wxid_a", "张三", earned=200)

    def test_shop_lists_items(self):
        reply = self.run_cmd("商店")
        self.assertIn("装备商店", reply)
        self.assertIn("木剑", reply)
        self.assertIn("余额 200", reply)

    def test_buy_and_auto_equip(self):
        reply = self.run_cmd("购买 木剑")
        self.assertIn("购买成功", reply)
        self.assertIn("已装备", reply)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"), 170,
        )
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.equipment, 4)

    def test_buy_insufficient(self):
        self.store.record_events(
            self.chat, "spend", [
                PointAward("wxid_a", "张三", "buy_item", -190, "spend"),
            ],
        )
        reply = self.run_cmd("购买 龙纹剑")
        self.assertIn("余额不足", reply)

    def test_bag_and_unequip(self):
        self.run_cmd("购买 木剑")
        reply = self.run_cmd("我的装备")
        self.assertIn("木剑", reply)
        self.assertIn("已装备", reply)

        reply = self.run_cmd("卸下 武器")
        self.assertIn("已卸下", reply)
        power = self.duels.power(self.chat, "wxid_a")
        self.assertEqual(power.equipment, 0)

    def test_power_breakdown(self):
        self.run_cmd("购买 木剑")
        reply = self.run_cmd("武力值")
        self.assertIn("武力值", reply)
        self.assertIn("木剑", reply)
        self.assertIn("今日切磋", reply)

    def test_power_board(self):
        self.add_member("wxid_b", "李四", earned=300)
        reply = self.run_cmd("战力榜")
        self.assertIn("战力榜", reply)
        self.assertIn("李四", reply)


class DuelCommandTests(HandlerTestBase):
    def setUp(self):
        super().setUp()
        self.add_member("wxid_a", "张三", earned=200)
        self.add_member("wxid_b", "李四", earned=200)

    def test_duel_by_name(self):
        reply = self.run_cmd("切磋 李四")
        self.assertIn("胜率", reply)
        self.assertIn("今日", reply)

    def test_duel_by_reply(self):
        reply = self.run_cmd("wxid_b:\n切磋")
        self.assertIn("胜率", reply)

    def test_duel_at_mention_ignores_sender_prefix(self):
        """WCDB prefixes the sender's wxid; it must not become the target."""
        msg = self.msg("切磋 @李四", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n切磋 @李四"
        reply = self.handler.handle(msg["content"], msg)
        self.assertIn("胜率", reply)
        self.assertNotIn("不能和自己", reply)

    def test_duel_prefix_at_mention_ignores_sender_prefix(self):
        msg = self.msg("@李四 切磋", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n@李四 切磋"
        reply = self.handler.handle(msg["content"], msg)
        self.assertIn("胜率", reply)
        self.assertNotIn("不能和自己", reply)

    def test_duel_uses_mentioned_wxid_when_display_name_differs(self):
        """The @ picker may show a group alias, but the wxid is exact."""
        msg = self.msg("@萧山-李四 切磋", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n@萧山-李四 切磋"
        msg["mentioned_ids"] = ["wxid_b"]
        reply = self.handler.handle(msg["content"], msg)
        self.assertIn("胜率", reply)
        self.assertNotIn("没找到成员", reply)

    def test_duel_resolves_unique_alias_prefix(self):
        """Without mention metadata, a unique suffix match still works."""
        msg = self.msg("@萧山-李四 切磋", "wxid_a", "张三")
        msg["content"] = "wxid_a:\n@萧山-李四 切磋"
        reply = self.handler.handle(msg["content"], msg)
        self.assertIn("胜率", reply)
        self.assertNotIn("没找到成员", reply)

    def test_duel_shield(self):
        self.store.buy_shield(
            self.chat, "wxid_b", "李四", 20, 14400, 86400,
        )
        reply = self.run_cmd("切磋 李四")
        self.assertIn("免战", reply)

    def test_duel_unknown_member(self):
        reply = self.run_cmd("切磋 王五")
        self.assertIn("没找到成员", reply)

    def test_duel_log(self):
        self.run_cmd("切磋 李四")
        reply = self.run_cmd("切磋记录")
        self.assertIn("切磋记录", reply)

    def test_buy_shield_command(self):
        reply = self.run_cmd("买免战券")
        self.assertIn("免战券生效", reply)
        self.assertEqual(
            self.store.balance_of(self.chat, "wxid_a"), 180,
        )


class AdminTests(HandlerTestBase):
    def setUp(self):
        super().setUp()
        self.add_member("wxid_a", "张三", earned=10)
        self.add_member("wxid_admin", "管理员", earned=0)

    def test_admin_can_adjust(self):
        reply = self.run_cmd(
            "积分调整 张三 +20 奖励", sender_id="wxid_admin", name="管理员",
        )
        self.assertIn("已调整", reply)
        self.assertEqual(
            self.store.earned_of(self.chat, "wxid_a"), 30,
        )

    def test_non_admin_is_rejected(self):
        reply = self.run_cmd("积分调整 张三 +20")
        self.assertIn("仅管理员", reply)
        self.assertEqual(
            self.store.earned_of(self.chat, "wxid_a"), 10,
        )


class DisabledTests(HandlerTestBase):
    def test_disabled_points_ignores_commands(self):
        self.config.points_enabled = False
        self.assertIsNone(self.run_cmd("积分"))

    def test_other_group_ignored(self):
        self.config.points_groups = ["别的群"]
        self.assertIsNone(self.run_cmd("积分"))


if __name__ == "__main__":
    unittest.main()
