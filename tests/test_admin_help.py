"""Tests for the 帮助 / help play list built by AdminCommandHandler."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.admin import AdminCommandHandler


def make_config(**overrides):
    values = {
        "points_enabled": True,
        "fun_enabled": True,
        "equipment_enabled": True,
        "duel_enabled": True,
        "fishing_enabled": True,
        "todo_enabled": True,
        "summarize_enabled": True,
        "duel_daily_attempts": 10,
        "duel_per_fight_cap": 20,
        "shield_price": 20,
        "shield_duration_sec": 14400,
        "shield_max_sec": 86400,
        "fishing_hourly_attempts": 30,
        "fishing_max_batch": 10,
        "fishing_daily_cap": 120,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AdminHelpTests(unittest.TestCase):
    def setUp(self):
        self.handler = AdminCommandHandler(MagicMock())

    def test_help_lists_every_play_section(self):
        reply = self.handler.handle(
            "帮助", "张三", config=make_config(), is_admin=False,
        )

        self.assertTrue(reply.startswith("@张三 游玩清单"))
        for section in (
            "【每日】", "【积分与排行】", "【装备与切磋】",
            "【钓鱼】", "【待办】", "【AI 助手】",
        ):
            self.assertIn(section, reply)
        self.assertIn("签到", reply)
        self.assertIn("抽签 / 解签", reply)
        self.assertIn("钓鱼榜 大鱼", reply)
        self.assertIn("@某人 切磋", reply)
        self.assertIn("记一下 周五聚餐", reply)
        # Todo commands need an @mention, so the prefix must be shown.
        self.assertIn("@我 记一下 周五聚餐", reply)
        # Admin commands are hidden from regular members.
        self.assertNotIn("管理（仅管理员）", reply)
        self.assertNotIn("积分调整", reply)

    def test_help_uses_configured_limits(self):
        config = make_config(
            duel_daily_attempts=7,
            duel_per_fight_cap=15,
            shield_price=25,
            shield_duration_sec=7200,
            shield_max_sec=43200,
            fishing_hourly_attempts=12,
            fishing_max_batch=4,
            fishing_daily_cap=80,
        )
        reply = self.handler.handle(
            "帮助", "张三", config=config, is_admin=False,
        )

        self.assertIn("每日 7 次", reply)
        self.assertIn("单次最多抢 15 分", reply)
        self.assertIn("免战券（25 分 / 2 小时，最多叠到 12 小时）", reply)
        self.assertIn("每小时 12 竿", reply)
        self.assertIn("一次最多 4 竿", reply)
        self.assertIn("收益上限 80/天", reply)

    def test_help_hides_disabled_features(self):
        config = make_config(
            points_enabled=False,
            fun_enabled=False,
            equipment_enabled=False,
            duel_enabled=False,
            fishing_enabled=False,
            todo_enabled=False,
            summarize_enabled=False,
        )
        reply = self.handler.handle(
            "帮助", "张三", config=config, is_admin=False,
        )

        for hidden in (
            "【每日】", "签到", "抽签", "【积分与排行】",
            "【装备与切磋】", "商店", "切磋", "【钓鱼】",
            "【待办】", "@我 总结一下",
        ):
            self.assertNotIn(hidden, reply)
        # Chat is always available on @mention.
        self.assertIn("@我 随便聊", reply)

    def test_help_keeps_section_when_only_one_sub_feature_is_on(self):
        config = make_config(
            equipment_enabled=False,
            duel_enabled=True,
        )
        reply = self.handler.handle(
            "帮助", "张三", config=config, is_admin=False,
        )

        self.assertIn("【装备与切磋】", reply)
        self.assertNotIn("装备商店", reply)
        self.assertNotIn("购买 铁剑", reply)
        self.assertIn("免战", reply)

    def test_help_shows_admin_section_for_admin(self):
        reply = self.handler.handle(
            "帮助", "管理员", config=make_config(), is_admin=True,
        )

        self.assertIn("【管理（仅管理员）】", reply)
        self.assertIn("改名 wxid_xxx = 昵称", reply)
        self.assertIn("积分调整", reply)
        self.assertIn("积分重算", reply)

    def test_help_aliases(self):
        for alias in ("帮助", "help", "命令"):
            with self.subTest(alias=alias):
                reply = self.handler.handle(
                    alias, "张三", config=make_config(),
                )
                self.assertIsNotNone(reply)
                self.assertIn("游玩清单", reply)

    def test_help_without_config_shows_all_sections(self):
        reply = self.handler.handle("帮助", "张三")

        self.assertIn("【积分与排行】", reply)
        self.assertIn("【钓鱼】", reply)
        self.assertIn("【待办】", reply)
        self.assertIn("每日 10 次", reply)
        self.assertIn("每小时 30 竿", reply)

    def test_unknown_content_is_not_a_command(self):
        self.assertIsNone(self.handler.handle("随便聊聊", "张三"))


if __name__ == "__main__":
    unittest.main()
